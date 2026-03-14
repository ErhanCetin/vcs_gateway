"""
VCS Gateway service — webhook ingestion orchestrator.

Coordinates the 7-step pipeline. Each step is delegated to a focused
component in webhook_processor/. This class owns only the flow.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import redis.asyncio as aioredis
import structlog

from vcs_gateway.config import Settings
from vcs_gateway.core.exceptions import DuplicateError, ValidationError
from vcs_gateway.core.signature import (
    compute_pr_hash_key,
    validate_github_signature,
    validate_gitlab_token,
)
from vcs_gateway.db.repositories.inbound_event_repository import InboundEventRepository
from vcs_gateway.db.repositories.tenant_repository import TenantRepository
from vcs_gateway.models.events import JourneyStepEvent, JourneyStepStatus, JourneyStepType
from vcs_gateway.queue.publisher import BasePublisher
from vcs_gateway.services.webhook_processor.event_filter import (
    extract_event_type,
    is_event_allowed,
)
from vcs_gateway.services.webhook_processor.idempotency_checker import IdempotencyChecker
from vcs_gateway.services.webhook_processor.payload_parser import parse_payload
from vcs_gateway.services.webhook_processor.tenant_validator import TenantValidator
from vcs_gateway.services.webhook_processor.webhook_persister import WebhookPersister

_SUPPORTED_PROVIDERS = frozenset({"github", "gitlab"})

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Result types — discriminated union, no exceptions for expected outcomes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebhookAccepted:
    correlation_id: UUID
    event_id: UUID
    outbox_id: UUID


@dataclass(frozen=True)
class WebhookDuplicate:
    correlation_id: UUID
    pr_hash_key: str
    detection_method: str  # "redis_cache" | "db_lookup"


@dataclass(frozen=True)
class WebhookIgnored:
    correlation_id: UUID
    event_type: str
    code: str = "EVENT_TYPE_NOT_ALLOWED"


WebhookResult = WebhookAccepted | WebhookDuplicate | WebhookIgnored


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class VcsGatewayService:
    """
    Stateless orchestrator. Inject all dependencies via __init__.
    Instantiate once at application startup and reuse across requests.
    """

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        redis_client: aioredis.Redis[str],
        journey_publisher: BasePublisher,
        settings: Settings,
    ) -> None:
        tenant_repo = TenantRepository(db_pool)
        inbound_repo = InboundEventRepository(db_pool)

        self._inbound_repo = inbound_repo
        self._tenant_validator = TenantValidator(tenant_repo, redis_client, settings)
        self._idempotency_checker = IdempotencyChecker(inbound_repo, redis_client, settings)
        self._webhook_persister = WebhookPersister(db_pool, inbound_repo, settings)
        self._journey = journey_publisher
        self._settings = settings

    async def process_webhook(
        self,
        tenant_id: UUID,
        vcs_provider: str,
        raw_payload: bytes,
        headers: dict[str, str],
    ) -> WebhookResult:
        correlation_id = uuid4()

        # Step 0 — Provider guard (before any DB/network I/O)
        if vcs_provider not in _SUPPORTED_PROVIDERS:
            raise ValidationError(
                f"Unsupported vcs_provider: {vcs_provider!r}",
                {"code": "UNSUPPORTED_PROVIDER"},
            )

        # Step 1 — Tenant validation
        webhook_secret = await self._tenant_validator.get_webhook_secret(tenant_id)

        # Step 2 — Event type filter (needs only headers — no body parse yet)
        event_type = extract_event_type(headers)
        whitelist = await self._tenant_validator.get_event_whitelist(vcs_provider)

        # Step 3 — Signature validation (before body parse)
        # Journey emit happens inside _validate_signature after the result is known.
        await self._validate_signature(
            vcs_provider, raw_payload, headers, webhook_secret, correlation_id, tenant_id,
        )

        # Signature verified — safe to mark webhook as received.
        await self._emit(
            JourneyStepType.webhook_received,
            JourneyStepStatus.in_progress,
            correlation_id,
            tenant_id,
        )

        # Step 4 — Payload parse (safe after signature is verified)
        pr_data = parse_payload(vcs_provider, raw_payload)
        action = pr_data.action

        if not is_event_allowed(event_type, action, whitelist):
            await self._emit(
                JourneyStepType.event_type_validated, JourneyStepStatus.info,
                correlation_id, tenant_id,
                metadata={"event_type": event_type, "action": action},
            )
            return WebhookIgnored(correlation_id=correlation_id, event_type=event_type)

        pr_hash_key = compute_pr_hash_key(
            vcs_provider, str(tenant_id), pr_data.repo_id,
            pr_data.pr_id, pr_data.vcs_instance_id, pr_data.action, pr_data.commit_sha,
        )

        # Step 5 — Idempotency check
        detection = await self._idempotency_checker.check(pr_hash_key)
        if detection is not None:
            await self._emit(
                JourneyStepType.idempotency_checked, JourneyStepStatus.info,
                correlation_id, tenant_id,
                metadata={"detection_method": detection},
            )
            return WebhookDuplicate(correlation_id, pr_hash_key, detection)
        await self._emit(
            JourneyStepType.idempotency_checked,
            JourneyStepStatus.completed,
            correlation_id,
            tenant_id,
        )

        # Step 6 — Persist + schedule outbox
        try:
            event_id, outbox_id = await self._webhook_persister.persist(
                tenant_id=tenant_id,
                vcs_provider=vcs_provider,
                correlation_id=correlation_id,
                pr_data=pr_data,
                pr_hash_key=pr_hash_key,
                raw_payload=raw_payload,
                headers=headers,
            )
        except DuplicateError:
            return WebhookDuplicate(
                correlation_id=correlation_id,
                pr_hash_key=pr_hash_key,
                detection_method="db_constraint",
            )
        await self._idempotency_checker.mark_processed(pr_hash_key)
        await self._emit(
            JourneyStepType.outbox_scheduled, JourneyStepStatus.completed,
            correlation_id, tenant_id,
            metadata={"outbox_id": str(outbox_id), "pr_hash_key": pr_hash_key},
        )
        return WebhookAccepted(
            correlation_id=correlation_id, event_id=event_id, outbox_id=outbox_id,
        )

    async def check_duplicate(self, pr_hash_key: str) -> dict[str, object]:
        """Redis → DB lookup for internal duplicate check endpoint."""
        detection = await self._idempotency_checker.check(pr_hash_key)
        if detection == "redis_cache":
            return {"is_duplicate": True, "cache_hit": True, "pr_hash_key": pr_hash_key}
        if detection == "db_lookup":
            event = await self._inbound_repo.get_by_pr_hash_key(pr_hash_key)
            return {
                "is_duplicate": True,
                "cache_hit": False,
                "pr_hash_key": pr_hash_key,
                "existing_event_id": str(event.event_id) if event else None,
                "existing_correlation_id": str(event.correlation_id) if event else None,
            }
        return {"is_duplicate": False, "cache_hit": False, "pr_hash_key": pr_hash_key}

    async def check_stale(self, pr_hash_key: str, pr_version: int) -> dict[str, object]:
        """DB version comparison for internal stale check endpoint."""
        event = await self._inbound_repo.get_by_pr_hash_key(pr_hash_key)
        if event is not None and event.pr_version > pr_version:
            return {
                "is_stale": True,
                "pr_hash_key": pr_hash_key,
                "provided_version": pr_version,
                "latest_version": event.pr_version,
            }
        return {"is_stale": False, "pr_hash_key": pr_hash_key}

    async def _validate_signature(
        self,
        vcs_provider: str,
        raw_payload: bytes,
        headers: dict[str, str],
        webhook_secret: str,
        correlation_id: UUID,
        tenant_id: UUID,
    ) -> None:
        valid = False
        if vcs_provider == "github":
            valid = validate_github_signature(
                raw_payload, webhook_secret, headers.get("X-Hub-Signature-256", ""),
            )
        elif vcs_provider == "gitlab":
            valid = validate_gitlab_token(headers.get("X-Gitlab-Token", ""), webhook_secret)

        status = JourneyStepStatus.completed if valid else JourneyStepStatus.failed
        await self._emit(JourneyStepType.signature_verified, status, correlation_id, tenant_id)

        if not valid:
            raise ValidationError("Invalid webhook signature", {"code": "INVALID_SIGNATURE"})

    async def _emit(
        self,
        step_type: JourneyStepType,
        status: JourneyStepStatus,
        correlation_id: UUID,
        tenant_id: UUID,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Fire-and-forget journey event — never raises."""
        try:
            event = JourneyStepEvent(
                correlation_id=correlation_id,
                tenant_id=tenant_id,
                step_type=step_type,
                status=status,
                metadata=metadata or {},
            )
            await self._journey.publish(
                exchange_name=self._settings.rabbitmq_exchange_journey,
                routing_key="journey.step.created",
                payload=event.model_dump(mode="json"),
                correlation_id=correlation_id,
            )
        except Exception:
            logger.warning(
                "journey_emit_failed",
                step_type=step_type,
                status=status,
                correlation_id=str(correlation_id),
                tenant_id=str(tenant_id),
            )


"""Step 6 — Transactional write: inbound_event + outbox_event."""

import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from vcs_gateway.config import Settings
from vcs_gateway.core.exceptions import DuplicateError
from vcs_gateway.db.outbox import OutboxRepository
from vcs_gateway.db.repositories.inbound_event_repository import InboundEventRepository
from vcs_gateway.models.events import WebhookReceivedMessage
from vcs_gateway.models.requests import PullRequestData

# X-Gitlab-Token is the plaintext HMAC secret — never store it.
_RELEVANT_HEADERS = frozenset({
    "X-Hub-Signature-256",
    "X-GitHub-Event",
    "X-Gitlab-Event",
    "Content-Type",
})


class WebhookPersister:
    def __init__(
        self,
        db_pool: asyncpg.Pool,
        inbound_repo: InboundEventRepository,
        settings: Settings,
    ) -> None:
        self._pool = db_pool
        self._inbound_repo = inbound_repo
        self._settings = settings

    async def persist(
        self,
        *,
        tenant_id: UUID,
        vcs_provider: str,
        correlation_id: UUID,
        pr_data: PullRequestData,
        pr_hash_key: str,
        raw_payload: bytes,
        headers: dict[str, str],
    ) -> tuple[UUID, UUID]:
        """
        Insert inbound_event and schedule outbox_event in a single transaction.

        Returns:
            (event_id, outbox_id)
        """
        event_id = uuid4()
        raw_json: dict[str, Any] = json.loads(raw_payload)
        relevant_headers = {k: v for k, v in headers.items() if k in _RELEVANT_HEADERS}
        outbox_payload = WebhookReceivedMessage(
            event_id=event_id,
            correlation_id=correlation_id,
            tenant_id=tenant_id,
            vcs_provider=vcs_provider,
            vcs_instance_id=pr_data.vcs_instance_id,
            repo_id=pr_data.repo_id,
            repo_name=pr_data.repo_name,
            pr_id=pr_data.pr_id,
            pr_title=pr_data.pr_title,
            pr_author=pr_data.pr_author,
            pr_url=pr_data.pr_url,
            commit_sha=pr_data.commit_sha,
            pr_action=pr_data.action,
            pr_hash_key=pr_hash_key,
            pr_version=1,
            raw_payload=raw_json,
        ).model_dump(mode="json")

        try:
            async with self._pool.acquire() as conn, conn.transaction():
                await self._inbound_repo.insert(
                    conn,
                    event_id=event_id,
                    correlation_id=correlation_id,
                    tenant_id=tenant_id,
                    vcs_provider=vcs_provider,
                    vcs_instance_id=pr_data.vcs_instance_id,
                    repo_id=pr_data.repo_id,
                    repo_name=pr_data.repo_name,
                    pr_id=pr_data.pr_id,
                    pr_title=pr_data.pr_title,
                    pr_author=pr_data.pr_author,
                    pr_url=pr_data.pr_url,
                    commit_sha=pr_data.commit_sha,
                    action=pr_data.action,
                    pr_hash_key=pr_hash_key,
                    pr_version=1,
                    processing_status="accepted",
                    raw_payload=raw_json,
                    webhook_headers=relevant_headers,
                )
                outbox_id = await OutboxRepository.schedule_event(
                    conn,
                    event_type="vcs.webhook.received",
                    correlation_id=correlation_id,
                    pr_hash_key=pr_hash_key,
                    pr_version=1,
                    payload=outbox_payload,
                    headers={"correlation_id": str(correlation_id)},
                    debounce_seconds=self._settings.outbox_debounce_seconds,
                )
        except asyncpg.UniqueViolationError as exc:
            raise DuplicateError(
                "Webhook already persisted",
                {"pr_hash_key": pr_hash_key},
            ) from exc

        return event_id, outbox_id

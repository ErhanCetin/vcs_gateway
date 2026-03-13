"""
Queue message models — Pydantic v2.

Convention:
  *Message  → messages this service CONSUMES (incoming)
  *Event    → events this service PUBLISHES (outgoing)

All messages inherit BaseMessage which enforces the envelope fields.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class BaseMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: UUID = Field(default_factory=uuid4)
    event_type: str
    correlation_id: UUID
    tenant_id: UUID
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class JourneyStepType(StrEnum):
    webhook_received = "webhook_received"
    signature_verified = "signature_verified"
    event_type_validated = "event_type_validated"
    idempotency_checked = "idempotency_checked"
    event_persisted = "event_persisted"
    outbox_scheduled = "outbox_scheduled"


class JourneyStepStatus(StrEnum):
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"
    info = "info"


# ---------------------------------------------------------------------------
# Published Events (outgoing)
# ---------------------------------------------------------------------------


class JourneyStepEvent(BaseMessage):
    """Published to journey.step.created for all lifecycle steps."""

    event_type: str = "journey.step.created"
    service_name: str = "vcs-gateway"
    step_type: JourneyStepType
    status: JourneyStepStatus
    pr_hash_key: str | None = None
    pr_id: str | None = None
    repo_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WebhookReceivedMessage(BaseMessage):
    """Published to vcs.webhook.received after successful validation + persistence."""

    event_type: str = "vcs.webhook.received"
    vcs_provider: str
    vcs_instance_id: str
    repo_id: str
    repo_name: str | None
    pr_id: str
    pr_title: str | None
    pr_author: str | None
    pr_url: str | None
    commit_sha: str
    pr_action: str
    pr_hash_key: str
    pr_version: int
    raw_payload: dict[str, Any]

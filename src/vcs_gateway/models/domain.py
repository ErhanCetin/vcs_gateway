"""
Domain models — typed representations of DB rows and business objects.

No ORM — asyncpg returns asyncpg.Record objects.
Use these Pydantic v2 models to parse Records into typed Python objects.

Example:
    row = await repo.fetchrow(QUERY, id)
    obj = MyDomainModel.model_validate(dict(row))
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class BaseDomainModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


# ---------------------------------------------------------------------------
# Shared schema — read-only by VCS Gateway
# ---------------------------------------------------------------------------


class Tenant(BaseDomainModel):
    """shared_schema.tenant joined with shared_schema.customer."""

    tenant_id: UUID
    customer_id: UUID
    name: str
    is_active: bool
    webhook_secret: str
    plan_type: str
    customer_plan_type: str | None = None


class VcsEventWhitelist(BaseDomainModel):
    """shared_schema.vcs_event_whitelist row."""

    vcs_provider: str
    event_type: str
    event_action: str
    is_active: bool


# ---------------------------------------------------------------------------
# VCS Gateway private schema
# ---------------------------------------------------------------------------


class InboundEvent(BaseDomainModel):
    """vcs_gateway_schema.inbound_event row."""

    event_id: UUID
    correlation_id: UUID
    tenant_id: UUID
    vcs_provider: str
    vcs_instance_id: str
    repo_id: str
    repo_name: str | None
    pr_id: str
    pr_title: str | None
    pr_author: str | None
    pr_url: str | None
    commit_sha: str
    action: str
    pr_hash_key: str
    pr_version: int
    processing_status: str
    rejection_reason: str | None
    raw_payload: dict[str, Any]
    webhook_headers: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class OutboxEvent(BaseDomainModel):
    """vcs_gateway_schema.outbox_event row."""

    outbox_id: UUID
    event_type: str
    correlation_id: UUID
    pr_hash_key: str | None
    pr_version: int | None
    payload: dict[str, Any]
    headers: dict[str, Any]
    status: str
    dispatch_at: datetime
    cancel_reason: str | None
    retry_count: int
    max_retries: int
    next_retry_at: datetime | None
    error_message: str | None
    created_at: datetime
    published_at: datetime | None

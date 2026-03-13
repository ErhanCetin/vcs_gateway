"""InboundEvent repository — vcs_gateway_schema.inbound_event."""

from typing import Any
from uuid import UUID

import asyncpg

from vcs_gateway.db.repository import BaseRepository
from vcs_gateway.models.domain import InboundEvent

_INSERT_INBOUND = """
    INSERT INTO vcs_gateway_schema.inbound_event (
        event_id, correlation_id, tenant_id, vcs_provider, vcs_instance_id,
        repo_id, repo_name, pr_id, pr_title, pr_author, pr_url,
        commit_sha, action, pr_hash_key, pr_version, processing_status,
        raw_payload, webhook_headers
    ) VALUES (
        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
        $12, $13, $14, $15, $16, $17, $18
    )
    RETURNING *
"""

_SELECT_BY_PR_HASH_KEY = """
    SELECT * FROM vcs_gateway_schema.inbound_event
    WHERE pr_hash_key = $1
"""


class InboundEventRepository(BaseRepository):
    async def get_by_pr_hash_key(self, pr_hash_key: str) -> InboundEvent | None:
        """Check if this exact PR+action+commit was already received."""
        row = await self.fetchrow(_SELECT_BY_PR_HASH_KEY, pr_hash_key)
        return InboundEvent.model_validate(dict(row)) if row else None

    async def insert(
        self,
        conn: asyncpg.Connection,
        event_id: UUID,
        correlation_id: UUID,
        tenant_id: UUID,
        vcs_provider: str,
        vcs_instance_id: str,
        repo_id: str,
        repo_name: str | None,
        pr_id: str,
        pr_title: str | None,
        pr_author: str | None,
        pr_url: str | None,
        commit_sha: str,
        action: str,
        pr_hash_key: str,
        pr_version: int,
        processing_status: str,
        raw_payload: dict[str, Any],
        webhook_headers: dict[str, Any] | None,
    ) -> InboundEvent:
        """Insert within an existing transaction.

        Raises asyncpg.UniqueViolationError on conflict.
        """
        row = await conn.fetchrow(
            _INSERT_INBOUND,
            event_id,
            correlation_id,
            tenant_id,
            vcs_provider,
            vcs_instance_id,
            repo_id,
            repo_name,
            pr_id,
            pr_title,
            pr_author,
            pr_url,
            commit_sha,
            action,
            pr_hash_key,
            pr_version,
            processing_status,
            raw_payload,
            webhook_headers,
        )
        if row is None:  # pragma: no cover
            msg = "INSERT INTO inbound_event returned no row"
            raise RuntimeError(msg)
        return InboundEvent.model_validate(dict(row))

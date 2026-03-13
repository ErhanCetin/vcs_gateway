"""Tenant repository — reads shared_schema.tenant + customer."""

from uuid import UUID

from vcs_gateway.db.repository import BaseRepository
from vcs_gateway.models.domain import Tenant, VcsEventWhitelist


class TenantRepository(BaseRepository):
    async def get_by_id(self, tenant_id: UUID) -> Tenant | None:
        """Fetch tenant config with plan_type from parent customer."""
        row = await self.fetchrow(
            """
            SELECT t.tenant_id, t.customer_id, t.tenant_name AS name,
                   t.is_active, t.webhook_secret,
                   c.plan_type, c.plan_type AS customer_plan_type
            FROM shared_schema.tenant t
            JOIN shared_schema.customer c ON c.customer_id = t.customer_id
            WHERE t.tenant_id = $1
            """,
            tenant_id,
        )
        return Tenant.model_validate(dict(row)) if row else None

    async def get_event_whitelist(self, vcs_provider: str) -> list[VcsEventWhitelist]:
        """Fetch allowed event types for a VCS provider."""
        rows = await self.fetch(
            "SELECT vcs_provider, event_type, event_action, is_active "
            "FROM shared_schema.vcs_event_whitelist "
            "WHERE vcs_provider = $1 AND is_active = TRUE",
            vcs_provider,
        )
        return [VcsEventWhitelist.model_validate(dict(r)) for r in rows]

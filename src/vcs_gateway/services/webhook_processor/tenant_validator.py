"""Step 1 — Tenant validation with Redis cache-aside."""

from uuid import UUID

import redis.asyncio as aioredis

from vcs_gateway.config import Settings
from vcs_gateway.core.exceptions import NotFoundError, ValidationError
from vcs_gateway.db.repositories.tenant_repository import TenantRepository
from vcs_gateway.models.domain import VcsEventWhitelist
from vcs_gateway.redis.client import get_tenant_cache, set_tenant_cache


class TenantValidator:
    def __init__(
        self,
        tenant_repo: TenantRepository,
        redis_client: aioredis.Redis[str],
        settings: Settings,
    ) -> None:
        self._repo = tenant_repo
        self._redis = redis_client
        self._settings = settings

    async def get_webhook_secret(self, tenant_id: UUID) -> str:
        """
        Resolve webhook_secret for tenant.
        Cache-aside: Redis first, DB on miss with write-back.
        Raises NotFoundError or ValidationError for invalid tenants.
        """
        cached = await get_tenant_cache(self._redis, tenant_id)
        if cached is not None:
            if not cached.get("is_active"):
                raise ValidationError("Tenant inactive", {"code": "TENANT_INACTIVE"})
            return str(cached["webhook_secret"])

        tenant = await self._repo.get_by_id(tenant_id)
        if tenant is None:
            raise NotFoundError("Tenant not found", {"tenant_id": str(tenant_id)})
        if not tenant.is_active:
            raise ValidationError("Tenant inactive", {"code": "TENANT_INACTIVE"})

        await set_tenant_cache(self._redis, tenant_id, tenant.model_dump(mode="json"))
        return tenant.webhook_secret

    async def get_event_whitelist(self, vcs_provider: str) -> list[VcsEventWhitelist]:
        return await self._repo.get_event_whitelist(vcs_provider)

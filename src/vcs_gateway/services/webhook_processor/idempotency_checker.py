"""Step 5 — Idempotency check: Redis → DB → write-back."""

import redis.asyncio as aioredis

from vcs_gateway.config import Settings
from vcs_gateway.db.repositories.inbound_event_repository import InboundEventRepository
from vcs_gateway.redis.client import get_idempotency_cache, set_idempotency_cache


class IdempotencyChecker:
    def __init__(
        self,
        inbound_repo: InboundEventRepository,
        redis_client: aioredis.Redis[str],
        settings: Settings,
    ) -> None:
        self._repo = inbound_repo
        self._redis = redis_client
        self._ttl = settings.redis_idempotency_ttl_seconds

    async def check(self, pr_hash_key: str) -> str | None:
        """
        Check if pr_hash_key was already processed.

        Returns:
            "redis_cache" — found in Redis (fast path)
            "db_lookup"   — found in DB (write-back to Redis performed)
            None          — not a duplicate, processing may continue
        """
        cached = await get_idempotency_cache(self._redis, pr_hash_key)
        if cached is not None:
            return "redis_cache"

        existing = await self._repo.get_by_pr_hash_key(pr_hash_key)
        if existing is not None:
            await set_idempotency_cache(self._redis, pr_hash_key, self._ttl)
            return "db_lookup"

        return None

    async def mark_processed(self, pr_hash_key: str) -> None:
        """Write-back to Redis after successful DB insert (best-effort)."""
        await set_idempotency_cache(self._redis, pr_hash_key, self._ttl)

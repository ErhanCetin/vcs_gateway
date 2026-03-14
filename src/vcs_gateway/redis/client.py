import json
from contextlib import suppress
from typing import TYPE_CHECKING, Any
from uuid import UUID

import redis.asyncio as aioredis

if TYPE_CHECKING:
    from vcs_gateway.config import Settings


async def create_redis_client(settings: "Settings") -> "aioredis.Redis[str]":
    """
    Create an async Redis client with connection pool.
    This service uses Redis as READ-ONLY idempotency cache.
    Do NOT write or delete cache entries — the cache is owned by VCS Gateway.
    """
    client: aioredis.Redis[str] = aioredis.Redis.from_url(
        str(settings.redis_url),
        max_connections=settings.redis_pool_max,
        decode_responses=True,
    )
    return client


async def check_redis_health(client: "aioredis.Redis[str]") -> bool:
    """Ping Redis. Returns True if healthy, False otherwise."""
    try:
        return await client.ping()
    except Exception:
        return False


async def get_idempotency_cache(
    client: "aioredis.Redis[str]",
    pr_hash_key: str,
) -> str | None:
    """
    Read the idempotency cache entry for the given pr_hash_key.
    Returns the raw string value ("1") or None on cache miss / Redis unavailable.

    Cache key format: idempotency:{pr_hash_key}
    """
    try:
        return await client.get(f"idempotency:{pr_hash_key}")
    except Exception:
        return None


async def get_tenant_cache(
    client: "aioredis.Redis[str]",
    tenant_id: UUID,
) -> dict[str, Any] | None:
    """
    Cache-Aside: read tenant config from Redis.
    Key: tenant:config:{tenant_id}
    Returns None on cache miss or Redis unavailable.
    TTL managed by set_tenant_cache (default 5 minutes).
    """
    try:
        raw = await client.get(f"tenant:config:{tenant_id}")
        if raw is None:
            return None
        result: dict[str, Any] = json.loads(raw)
        return result
    except Exception:
        return None


async def set_tenant_cache(
    client: "aioredis.Redis[str]",
    tenant_id: UUID,
    tenant_data: dict[str, Any],
    ttl_seconds: int = 300,
) -> None:
    """Write tenant config to Redis (best-effort — never raises)."""
    with suppress(Exception):
        await client.setex(
            f"tenant:config:{tenant_id}",
            ttl_seconds,
            json.dumps(tenant_data, default=str),
        )


async def set_idempotency_cache(
    client: "aioredis.Redis[str]",
    pr_hash_key: str,
    ttl_seconds: int = 259200,
) -> None:
    """Write-back to idempotency cache after successful processing (best-effort — never raises)."""
    with suppress(Exception):
        await client.setex(f"idempotency:{pr_hash_key}", ttl_seconds, "1")


def is_stale(cached_entry: dict[str, object] | None, current_pr_version: int) -> bool:
    """
    Compare cached pr_version with current pr_version.

    Returns True (stale) if:
      - cached_entry exists AND cached pr_version != current_pr_version

    Returns False (not stale) if:
      - cache miss (cached_entry is None) — assume not stale
      - versions match
    """
    if cached_entry is None:
        return False
    cached_version = cached_entry.get("pr_version")
    if cached_version is None:
        return False
    return int(str(cached_version)) != current_pr_version

import json
from typing import TYPE_CHECKING

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
) -> dict[str, object] | None:
    """
    Read the idempotency cache entry for the given pr_hash_key.
    Returns the cached dict or None on cache miss / Redis unavailable.

    Cache key format: idempotency:{pr_hash_key}
    """
    try:
        raw = await client.get(f"idempotency:{pr_hash_key}")
        if raw is None:
            return None
        result: dict[str, object] = json.loads(raw)
        return result
    except Exception:
        return None


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

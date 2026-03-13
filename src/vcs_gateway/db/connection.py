from typing import TYPE_CHECKING

import asyncpg

if TYPE_CHECKING:
    from vcs_gateway.config import Settings


async def create_pool(settings: "Settings") -> asyncpg.Pool:
    """Create and return an asyncpg connection pool."""
    pool = await asyncpg.create_pool(
        dsn=str(settings.database_url),
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        command_timeout=settings.db_command_timeout,
    )
    if pool is None:
        raise RuntimeError("Failed to create asyncpg pool")
    return pool

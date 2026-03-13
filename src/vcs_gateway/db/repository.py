"""Base repository with asyncpg pool access and transaction helper."""

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


class BaseRepository:
    """
    Provides low-level database access methods.
    All queries are parameterized — no string interpolation allowed.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def execute(self, query: str, *args: Any) -> str:
        start = time.perf_counter()
        async with self._pool.acquire() as conn:
            result = await conn.execute(query, *args)
        logger.debug("db_execute", duration_ms=round((time.perf_counter() - start) * 1000, 2))
        return result

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        start = time.perf_counter()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
        logger.debug("db_fetchrow", duration_ms=round((time.perf_counter() - start) * 1000, 2))
        return row

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        start = time.perf_counter()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
        logger.debug("db_fetch", count=len(rows), duration_ms=round((time.perf_counter() - start) * 1000, 2))
        return rows

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[asyncpg.Connection, None]:
        """
        Async context manager that yields an asyncpg connection with an active
        transaction. Commits on clean exit, rolls back on exception.

        Usage:
            async with self.transaction() as conn:
                await conn.execute(INSERT_QUERY, ...)
                await outbox_repo.insert_event(conn, ...)
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                yield conn

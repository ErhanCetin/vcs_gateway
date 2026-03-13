"""
Outbox Pattern implementation.

OutboxRepository  — inserts events into outbox_event inside a transaction.
OutboxPublisher   — background task that polls outbox_event and publishes to RabbitMQ.

The outbox_event table must exist in your service schema:

    CREATE TABLE outbox_event (
        outbox_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        exchange     TEXT NOT NULL,
        routing_key  TEXT NOT NULL,
        payload      JSONB NOT NULL,
        correlation_id TEXT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'pending',
        retry_count  INT  NOT NULL DEFAULT 0,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        published_at TIMESTAMPTZ
    );
"""

import asyncio
import json
from typing import Any
from uuid import UUID

import aio_pika
import asyncpg
import structlog

logger = structlog.get_logger(__name__)

_SELECT_PENDING = """
    SELECT outbox_id, exchange, routing_key, payload, correlation_id, retry_count
    FROM outbox_event
    WHERE status = 'pending'
    ORDER BY created_at
    LIMIT $1
    FOR UPDATE SKIP LOCKED
"""

_MARK_PUBLISHED = """
    UPDATE outbox_event
    SET status = 'published', published_at = NOW()
    WHERE outbox_id = $1
"""

_MARK_FAILED = """
    UPDATE outbox_event
    SET status = 'failed', retry_count = retry_count + 1
    WHERE outbox_id = $1
"""

_INSERT_EVENT = """
    INSERT INTO outbox_event (exchange, routing_key, payload, correlation_id)
    VALUES ($1, $2, $3, $4)
"""


class OutboxRepository:
    """Writes events to outbox_event within an existing transaction."""

    @staticmethod
    async def insert_event(
        conn: asyncpg.Connection,
        *,
        exchange: str,
        routing_key: str,
        payload: dict[str, Any],
        correlation_id: str | UUID,
    ) -> None:
        await conn.execute(
            _INSERT_EVENT,
            exchange,
            routing_key,
            json.dumps(payload),
            str(correlation_id),
        )


class OutboxPublisher:
    """
    Background asyncio task.
    Polls outbox_event every poll_interval seconds,
    publishes pending rows to RabbitMQ, marks them published.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        amqp_connection: aio_pika.RobustConnection,
        poll_interval: float = 1.0,
        batch_size: int = 50,
        max_retries: int = 3,
    ) -> None:
        self._pool = pool
        self._amqp_connection = amqp_connection
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._max_retries = max_retries

    async def run(self) -> None:
        logger.info("outbox_publisher_started")
        while True:
            try:
                await self._process_batch()
            except asyncio.CancelledError:
                logger.info("outbox_publisher_stopped")
                return
            except Exception:
                logger.exception("outbox_publisher_error")
            await asyncio.sleep(self._poll_interval)

    async def _process_batch(self) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(_SELECT_PENDING, self._batch_size)
                if not rows:
                    return

                channel = await self._amqp_connection.channel()

                for row in rows:
                    try:
                        exchange = await channel.get_exchange(row["exchange"])
                        await exchange.publish(
                            aio_pika.Message(
                                body=row["payload"].encode(),
                                content_type="application/json",
                                headers={"correlation_id": row["correlation_id"]},
                            ),
                            routing_key=row["routing_key"],
                        )
                        await conn.execute(_MARK_PUBLISHED, row["outbox_id"])
                        logger.debug("outbox_published", routing_key=row["routing_key"])
                    except Exception:
                        logger.exception("outbox_publish_failed", outbox_id=str(row["outbox_id"]))
                        if row["retry_count"] >= self._max_retries:
                            await conn.execute(_MARK_FAILED, row["outbox_id"])

                await channel.close()

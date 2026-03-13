"""
Outbox Pattern — VCS Gateway specific (debounce-aware).

OutboxRepository  — inserts/cancels events in vcs_gateway_schema.outbox_event.
OutboxPublisher   — background task that polls SCHEDULED rows at dispatch_at
                    and publishes to RabbitMQ.

Status lifecycle:
  SCHEDULED → DISPATCHED  (happy path)
  SCHEDULED → CANCELLED   (newer version arrived within debounce window)
  DISPATCHED → FAILED     (publish error after max_retries exhausted)
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import aio_pika
import asyncpg
import structlog

logger = structlog.get_logger(__name__)

_SCHEMA = "vcs_gateway_schema"

_SELECT_DUE = f"""
    SELECT outbox_id, event_type, correlation_id, pr_hash_key, pr_version,
           payload, headers, retry_count, max_retries
    FROM {_SCHEMA}.outbox_event
    WHERE status = 'SCHEDULED'
      AND dispatch_at <= NOW()
    ORDER BY dispatch_at
    LIMIT $1
    FOR UPDATE SKIP LOCKED
"""

_MARK_DISPATCHED = f"""
    UPDATE {_SCHEMA}.outbox_event
    SET status = 'DISPATCHED', published_at = NOW()
    WHERE outbox_id = $1
"""

_MARK_FAILED = f"""
    UPDATE {_SCHEMA}.outbox_event
    SET status = 'FAILED', retry_count = retry_count + 1,
        error_message = $2, next_retry_at = $3
    WHERE outbox_id = $1
"""

_CANCEL_PREVIOUS = f"""
    UPDATE {_SCHEMA}.outbox_event
    SET status = 'CANCELLED', cancel_reason = $3
    WHERE pr_hash_key = $1
      AND pr_version  < $2
      AND status      = 'SCHEDULED'
"""

_INSERT_EVENT = f"""
    INSERT INTO {_SCHEMA}.outbox_event
        (event_type, correlation_id, pr_hash_key, pr_version,
         payload, headers, dispatch_at)
    VALUES ($1, $2, $3, $4, $5, $6, NOW() + $7 * INTERVAL '1 second')
    RETURNING outbox_id
"""


class OutboxRepository:
    """Writes/cancels outbox events within an existing transaction."""

    @staticmethod
    async def schedule_event(
        conn: asyncpg.Connection,
        *,
        event_type: str,
        correlation_id: UUID,
        pr_hash_key: str,
        pr_version: int,
        payload: dict[str, Any],
        headers: dict[str, Any],
        debounce_seconds: int,
    ) -> UUID:
        """
        Schedule an outbox event with debounce delay.
        Automatically cancels any SCHEDULED events for older versions of the same PR.
        Returns the new outbox_id.
        """
        # Cancel stale events for older PR versions
        await conn.execute(
            _CANCEL_PREVIOUS,
            pr_hash_key,
            pr_version,
            "superseded_by_newer_version",
        )

        row = await conn.fetchrow(
            _INSERT_EVENT,
            event_type,
            correlation_id,
            pr_hash_key,
            pr_version,
            json.dumps(payload),
            json.dumps(headers),
            debounce_seconds,
        )
        if row is None:  # pragma: no cover
            msg = "INSERT INTO outbox_event returned no row"
            raise RuntimeError(msg)
        return row["outbox_id"]  # type: ignore[no-any-return]


class OutboxPublisher:
    """
    Background asyncio task.
    Polls outbox_event for SCHEDULED rows whose dispatch_at has passed,
    publishes to RabbitMQ, marks them DISPATCHED.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        amqp_connection: aio_pika.abc.AbstractRobustConnection,
        exchange_name: str,
        poll_interval: float = 1.0,
        batch_size: int = 50,
    ) -> None:
        self._pool = pool
        self._amqp_connection = amqp_connection
        self._exchange_name = exchange_name
        self._poll_interval = poll_interval
        self._batch_size = batch_size

    async def run(self) -> None:
        logger.info("outbox_publisher_started", exchange=self._exchange_name)
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
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(_SELECT_DUE, self._batch_size)
            if not rows:
                return

            channel = await self._amqp_connection.channel()
            try:
                exchange = await channel.get_exchange(self._exchange_name)

                for row in rows:
                    outbox_id: UUID = row["outbox_id"]
                    try:
                        headers: dict[str, Any] = json.loads(row["headers"])
                        await exchange.publish(
                            aio_pika.Message(
                                body=row["payload"].encode(),
                                content_type="application/json",
                                headers=headers,
                            ),
                            routing_key=row["event_type"],
                        )
                        await conn.execute(_MARK_DISPATCHED, outbox_id)
                        logger.debug(
                            "outbox_dispatched",
                            outbox_id=str(outbox_id),
                            event_type=row["event_type"],
                        )
                    except Exception as exc:
                        retry_count: int = row["retry_count"]
                        max_retries: int = row["max_retries"]
                        logger.exception(
                            "outbox_dispatch_failed",
                            outbox_id=str(outbox_id),
                            retry_count=retry_count,
                        )
                        next_retry_at = (
                            datetime.now(UTC) + timedelta(seconds=30 * (retry_count + 1))
                            if retry_count < max_retries
                            else None
                        )
                        await conn.execute(_MARK_FAILED, outbox_id, str(exc), next_retry_at)
            finally:
                await channel.close()

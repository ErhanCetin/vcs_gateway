"""
BaseConsumer — abstract base for all RabbitMQ queue consumers.

Subclass this and implement process_message().
Business errors (ServiceError subclasses) → nack, no requeue → DLQ.
Transient errors → nack with requeue (up to prefetch limit).
Success → ack.
"""

import json
import time
from abc import ABC, abstractmethod
from typing import Any

import aio_pika
import structlog

from vcs_gateway.core.exceptions import ServiceError

logger = structlog.get_logger(__name__)


class BaseConsumer(ABC):
    def __init__(
        self,
        amqp_connection: aio_pika.RobustConnection,
        queue_name: str,
        dlq_name: str,
        prefetch_count: int = 10,
    ) -> None:
        self._connection = amqp_connection
        self._queue_name = queue_name
        self._dlq_name = dlq_name
        self._prefetch_count = prefetch_count
        self._channel: aio_pika.Channel | None = None

    @abstractmethod
    async def process_message(self, payload: dict[str, Any], correlation_id: str) -> None:
        """
        Implement the business logic for processing a single message.
        Raise ServiceError subclasses for business errors (→ DLQ).
        Raise any other Exception for transient errors (→ requeue).
        """

    async def start(self) -> None:
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=self._prefetch_count)

        queue = await self._channel.declare_queue(
            self._queue_name,
            durable=True,
            arguments={"x-dead-letter-exchange": "", "x-dead-letter-routing-key": self._dlq_name},
        )
        await self._channel.declare_queue(self._dlq_name, durable=True)

        await queue.consume(self._on_message)
        logger.info("consumer_started", queue=self._queue_name)

    async def stop(self) -> None:
        if self._channel:
            await self._channel.close()
        logger.info("consumer_stopped", queue=self._queue_name)

    async def _on_message(self, message: aio_pika.IncomingMessage) -> None:
        correlation_id = (message.headers or {}).get("correlation_id", "unknown")
        start = time.perf_counter()

        structlog.contextvars.bind_contextvars(
            correlation_id=correlation_id,
            queue=self._queue_name,
        )

        try:
            payload = json.loads(message.body)
            await self.process_message(payload, correlation_id)
            await message.ack()
            logger.info(
                "message_processed",
                duration_ms=round((time.perf_counter() - start) * 1000, 2),
            )
        except ServiceError as exc:
            # Business error — do not requeue → goes to DLQ
            logger.error(
                "message_business_error",
                error_code=exc.error_code,
                error=str(exc),
                duration_ms=round((time.perf_counter() - start) * 1000, 2),
            )
            await message.nack(requeue=False)
        except Exception:
            # Transient error — requeue
            logger.exception(
                "message_transient_error",
                duration_ms=round((time.perf_counter() - start) * 1000, 2),
            )
            await message.nack(requeue=True)
        finally:
            structlog.contextvars.unbind_contextvars("correlation_id", "queue")

"""
BasePublisher — direct RabbitMQ publish (non-outbox).

Use for fire-and-forget events where transactional guarantees are NOT needed
(e.g., journey.step.created events where occasional loss is acceptable).

For business-critical events (analysis.completed, etc.) always use
OutboxRepository + OutboxPublisher instead.
"""

import json
from typing import Any
from uuid import UUID

import aio_pika
import structlog

logger = structlog.get_logger(__name__)


class BasePublisher:
    def __init__(self, amqp_connection: aio_pika.RobustConnection) -> None:
        self._connection = amqp_connection

    async def publish(
        self,
        *,
        exchange_name: str,
        routing_key: str,
        payload: dict[str, Any],
        correlation_id: str | UUID,
    ) -> None:
        channel = await self._connection.channel()
        try:
            exchange = await channel.get_exchange(exchange_name)
            await exchange.publish(
                aio_pika.Message(
                    body=json.dumps(payload).encode(),
                    content_type="application/json",
                    headers={"correlation_id": str(correlation_id)},
                ),
                routing_key=routing_key,
            )
            logger.debug("message_published", exchange=exchange_name, routing_key=routing_key)
        finally:
            await channel.close()

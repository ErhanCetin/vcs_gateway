"""
Queue consumer entrypoint.

Used by services that consume RabbitMQ messages as their primary function.
Run with: uv run python -m vcs_gateway.worker
"""

import asyncio
import signal

from vcs_gateway.config import get_settings
from vcs_gateway.core.logging import configure_logging, get_logger
from vcs_gateway.db.connection import create_pool
from vcs_gateway.queue.connection import create_amqp_connection
from vcs_gateway.redis.client import create_redis_client

# Import your consumer(s) here:
# from vcs_gateway.queue.consumers.my_consumer import MyConsumer

logger = get_logger(__name__)

_shutdown_event = asyncio.Event()


def _handle_signal(sig: signal.Signals) -> None:
    logger.info("shutdown_signal_received", signal=sig.name)
    _shutdown_event.set()


async def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    logger.info("worker_starting", service=settings.vcs_gateway)

    # Register OS signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal, sig)

    # Initialize dependencies
    db_pool = await create_pool(settings)
    amqp_connection = await create_amqp_connection(settings)
    redis_client = await create_redis_client(settings)

    # Initialize consumer(s)
    # consumer = MyConsumer(
    #     pool=db_pool,
    #     amqp_connection=amqp_connection,
    #     redis=redis_client,
    #     settings=settings,
    # )

    logger.info("worker_ready")

    try:
        # Start consumer(s) and wait for shutdown signal
        # await asyncio.gather(consumer.start(), _shutdown_event.wait())
        await _shutdown_event.wait()
    finally:
        logger.info("worker_stopping")
        # await consumer.stop()
        await redis_client.aclose()
        await amqp_connection.close()
        await db_pool.close()
        logger.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from vcs_gateway.api.exception_handlers import (
    business_rule_handler,
    database_handler,
    not_found_handler,
    queue_handler,
    validation_handler,
)
from vcs_gateway.api.health import router as health_router
from vcs_gateway.api.internal.endpoints import router as internal_router
from vcs_gateway.api.v1.webhooks import router as webhooks_router
from vcs_gateway.config import get_settings
from vcs_gateway.core.exceptions import (
    BusinessRuleError,
    DatabaseError,
    NotFoundError,
    QueueError,
    ValidationError,
)
from vcs_gateway.core.logging import configure_logging, get_logger
from vcs_gateway.core.middleware import CorrelationIdMiddleware, RequestLoggingMiddleware
from vcs_gateway.core.telemetry import configure_telemetry, instrument_fastapi
from vcs_gateway.db.connection import create_pool
from vcs_gateway.db.outbox import OutboxPublisher
from vcs_gateway.queue.connection import create_amqp_connection
from vcs_gateway.queue.publisher import BasePublisher
from vcs_gateway.redis.client import create_redis_client
from vcs_gateway.services.vcs_gateway import VcsGatewayService

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    # 1. Logging
    configure_logging(settings)
    logger.info("starting_up", service=settings.service_name, env=settings.environment)

    # 2. OpenTelemetry (before any I/O so instrumentors are active)
    configure_telemetry(
        service_name=settings.service_name,
        service_version=settings.otel_service_version,
        environment=settings.environment,
        otlp_endpoint=settings.otel_exporter_otlp_endpoint,
        sample_rate=settings.otel_sample_rate,
        enabled=settings.otel_enabled,
    )

    # 3. Database pool
    app.state.db_pool = await create_pool(settings)
    logger.info("db_pool_created")

    # 4. RabbitMQ connection
    app.state.amqp_connection = await create_amqp_connection(settings)
    logger.info("amqp_connection_created")

    # 5. Redis client
    app.state.redis = await create_redis_client(settings)
    logger.info("redis_client_created")

    # 6. Journey publisher — fire-and-forget, non-transactional
    app.state.journey_publisher = BasePublisher(app.state.amqp_connection)

    # 7. VcsGatewayService — stateless, shared across all requests
    app.state.vcs_service = VcsGatewayService(
        db_pool=app.state.db_pool,
        redis_client=app.state.redis,
        journey_publisher=app.state.journey_publisher,
        settings=settings,
    )

    # 8. Outbox publisher background task
    outbox_publisher = OutboxPublisher(
        pool=app.state.db_pool,
        amqp_connection=app.state.amqp_connection,
        exchange_name=settings.rabbitmq_exchange_webhook,
        poll_interval=settings.outbox_poll_interval_seconds,
        batch_size=settings.outbox_batch_size,
    )
    outbox_task = asyncio.create_task(outbox_publisher.run())

    logger.info("startup_complete")
    yield

    # Shutdown — reverse order
    logger.info("shutting_down")
    outbox_task.cancel()
    with suppress(asyncio.CancelledError):
        await outbox_task

    await app.state.redis.aclose()
    await app.state.amqp_connection.close()
    await app.state.db_pool.close()
    logger.info("shutdown_complete")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.service_name,
        version="0.1.0",
        docs_url="/docs" if settings.environment == "local" else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # OpenTelemetry FastAPI instrumentation (must run after app creation)
    instrument_fastapi(app)

    # Middleware (order matters — outermost first)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(CorrelationIdMiddleware)

    # Exception handlers — translate domain exceptions to HTTP responses
    app.add_exception_handler(NotFoundError, not_found_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ValidationError, validation_handler)  # type: ignore[arg-type]
    app.add_exception_handler(BusinessRuleError, business_rule_handler)  # type: ignore[arg-type]
    app.add_exception_handler(DatabaseError, database_handler)  # type: ignore[arg-type]
    app.add_exception_handler(QueueError, queue_handler)  # type: ignore[arg-type]

    # Routers
    app.include_router(health_router)
    app.include_router(webhooks_router, prefix="/api/v1")
    app.include_router(internal_router, prefix="/internal/v1")

    return app


app = create_app()

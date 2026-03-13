"""
OpenTelemetry setup — call configure_telemetry() once at application startup (in lifespan).

Instruments:
  - FastAPI (HTTP server spans)
  - asyncpg (DB query spans)
  - aio-pika (RabbitMQ publish/consume spans)
  - Redis (cache operation spans)
  - httpx (outbound HTTP spans)

Exporter: OTLP gRPC → Jaeger / Grafana Tempo / any OTel collector.
Set OTEL_ENABLED=false to disable in local dev without a collector.
"""

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.aio_pika import AioPikaInstrumentor
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from vcs_gateway.core.logging import get_logger

logger = get_logger(__name__)


def configure_telemetry(
    service_name: str,
    service_version: str,
    environment: str,
    otlp_endpoint: str,
    sample_rate: float = 1.0,
    enabled: bool = True,
) -> None:
    """
    Initialize OpenTelemetry tracing. Call once during FastAPI lifespan startup.

    Args:
        service_name:    e.g. "vcs-gateway"
        service_version: e.g. "0.1.0"
        environment:     "local" | "staging" | "production"
        otlp_endpoint:   gRPC collector endpoint (e.g. "http://localhost:4317")
        sample_rate:     Fraction of traces to sample (1.0 = all, 0.1 = 10%)
        enabled:         Set False to skip all OTel setup (local dev without collector)
    """
    if not enabled:
        logger.info("OpenTelemetry disabled — skipping setup")
        return

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
        }
    )

    sampler = ParentBased(root=TraceIdRatioBased(sample_rate))
    provider = TracerProvider(resource=resource, sampler=sampler)

    exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)

    AsyncPGInstrumentor().instrument()
    AioPikaInstrumentor().instrument()
    RedisInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()

    logger.info(
        "OpenTelemetry configured",
        service=service_name,
        otlp_endpoint=otlp_endpoint,
        sample_rate=sample_rate,
    )


def instrument_fastapi(app: object) -> None:
    """
    Instrument a FastAPI app instance. Call after configure_telemetry() in lifespan.
    """
    FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]

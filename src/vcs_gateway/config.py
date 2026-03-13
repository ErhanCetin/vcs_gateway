from functools import lru_cache
from typing import Literal

from pydantic import AmqpDsn, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # -----------------------------------------------------------------------
    # Service identity
    # -----------------------------------------------------------------------
    service_name: str = "vcs-gateway"
    environment: Literal["local", "staging", "production"] = "local"
    log_level: str = "INFO"
    port: int = 8000

    # -----------------------------------------------------------------------
    # Database (asyncpg DSN format)
    # -----------------------------------------------------------------------
    database_url: PostgresDsn
    database_schema: str = "vcs_gateway_schema"
    db_pool_min: int = 2
    db_pool_max: int = 10
    db_command_timeout: int = 30

    # -----------------------------------------------------------------------
    # RabbitMQ
    # -----------------------------------------------------------------------
    rabbitmq_url: AmqpDsn
    rabbitmq_prefetch_count: int = 10

    # -----------------------------------------------------------------------
    # Redis (read-only idempotency cache)
    # -----------------------------------------------------------------------
    redis_url: RedisDsn
    redis_pool_max: int = 20

    # -----------------------------------------------------------------------
    # Outbox publisher
    # -----------------------------------------------------------------------
    outbox_poll_interval_seconds: float = 1.0
    outbox_batch_size: int = 50

    # -----------------------------------------------------------------------
    # OpenTelemetry
    # -----------------------------------------------------------------------
    otel_enabled: bool = True
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_version: str = "0.1.0"
    otel_sample_rate: float = 1.0

    # -----------------------------------------------------------------------
    # VCS Gateway — service-specific settings
    # -----------------------------------------------------------------------
    rabbitmq_exchange_webhook: str = "vcs.webhook.received"
    rabbitmq_exchange_journey: str = "journey.events"
    redis_idempotency_ttl_seconds: int = 259200  # 72 hours
    outbox_debounce_seconds: int = 30
    webhook_hmac_algorithm: str = "sha256"
    webhook_request_timeout_seconds: int = 5


@lru_cache
def get_settings() -> Settings:
    """Return cached Settings instance. Parsed once at startup."""
    return Settings()

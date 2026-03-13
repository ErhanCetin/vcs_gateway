"""Domain exception hierarchy.

All business and infrastructure errors inherit from ServiceError.
FastAPI exception handlers in main.py translate these to HTTP responses.
"""


class ServiceError(Exception):
    """Base exception for all service errors."""

    error_code: str = "SERVICE_ERROR"

    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, object] = details or {}


# ---------------------------------------------------------------------------
# Business errors — do NOT retry
# ---------------------------------------------------------------------------


class ValidationError(ServiceError):
    """Input failed validation."""
    error_code = "VALIDATION_ERROR"


class NotFoundError(ServiceError):
    """Requested resource does not exist."""
    error_code = "NOT_FOUND"


class DuplicateError(ServiceError):
    """Resource already exists (idempotency conflict)."""
    error_code = "DUPLICATE"


class StaleEventError(ServiceError):
    """PR version mismatch — a newer version already exists."""
    error_code = "STALE_EVENT"


class QuotaExceededError(ServiceError):
    """Tenant quota limit has been reached."""
    error_code = "QUOTA_EXCEEDED"


class BusinessRuleError(ServiceError):
    """A domain business rule was violated."""
    error_code = "BUSINESS_RULE_VIOLATION"


# ---------------------------------------------------------------------------
# Infrastructure errors — may retry
# ---------------------------------------------------------------------------


class DatabaseError(ServiceError):
    """Database operation failed."""
    error_code = "DATABASE_ERROR"


class QueueError(ServiceError):
    """RabbitMQ operation failed."""
    error_code = "QUEUE_ERROR"


class ExternalServiceError(ServiceError):
    """External HTTP dependency returned an error."""
    error_code = "EXTERNAL_SERVICE_ERROR"

    def __init__(
        self, message: str, status_code: int | None = None, details: dict[str, object] | None = None
    ) -> None:
        super().__init__(message, details)
        self.status_code = status_code


class RedisError(ServiceError):
    """Redis operation failed."""
    error_code = "REDIS_ERROR"

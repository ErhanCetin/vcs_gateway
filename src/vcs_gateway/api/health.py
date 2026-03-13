"""Health check endpoints — /health/live and /health/ready."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from vcs_gateway.redis.client import check_redis_health

router = APIRouter(tags=["health"])


class LivenessResponse(BaseModel):
    status: str = "ok"


class ReadinessResponse(BaseModel):
    status: str
    checks: dict[str, str]


@router.get("/health/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    """Always returns ok — confirms the process is running."""
    return LivenessResponse()


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness(request: Request) -> ReadinessResponse:
    """
    Returns ready only if all dependencies are healthy.
    Add service-specific checks inside this function.
    """
    checks: dict[str, str] = {}

    # Database
    try:
        await request.app.state.db_pool.fetchval("SELECT 1")
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"

    # RabbitMQ
    try:
        conn = request.app.state.amqp_connection
        checks["rabbitmq"] = "ok" if not conn.is_closed else "error"
    except Exception:
        checks["rabbitmq"] = "error"

    # Redis
    redis_healthy = await check_redis_health(request.app.state.redis)
    checks["redis"] = "ok" if redis_healthy else "degraded"

    # Add more checks here for service-specific external dependencies

    all_ok = all(v in ("ok", "degraded") for v in checks.values())
    return ReadinessResponse(
        status="ready" if all_ok else "not_ready",
        checks=checks,
    )

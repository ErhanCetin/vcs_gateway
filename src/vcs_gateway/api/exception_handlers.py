"""
FastAPI exception handlers — domain exceptions → HTTP responses.

Registered once in main.py via app.add_exception_handler().
Keeps HTTP concerns out of the service layer.
"""

from fastapi import Request
from fastapi.responses import JSONResponse

from vcs_gateway.core.exceptions import (
    BusinessRuleError,
    DatabaseError,
    NotFoundError,
    QueueError,
    ValidationError,
)


async def not_found_handler(request: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"code": exc.error_code, "message": exc.message, **exc.details},
    )


async def validation_handler(request: Request, exc: ValidationError) -> JSONResponse:
    # Map specific error codes to appropriate HTTP status codes.
    # The service layer encodes intent via details["code"].
    code = exc.details.get("code", "")
    if code == "INVALID_SIGNATURE":
        status = 401
    elif code in {"TENANT_INACTIVE", "UNSUPPORTED_PROVIDER"}:
        status = 403
    else:
        status = 400
    return JSONResponse(
        status_code=status,
        content={"code": code or exc.error_code, "message": exc.message},
    )


async def business_rule_handler(request: Request, exc: BusinessRuleError) -> JSONResponse:
    code = exc.details.get("code", exc.error_code)
    return JSONResponse(
        status_code=403,
        content={"code": code, "message": exc.message},
    )


async def database_handler(request: Request, exc: DatabaseError) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        headers={"Retry-After": "60"},
        content={"code": exc.error_code, "message": "Database unavailable", "retry_after": 60},
    )


async def queue_handler(request: Request, exc: QueueError) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        headers={"Retry-After": "60"},
        content={"code": exc.error_code, "message": "Queue unavailable", "retry_after": 60},
    )

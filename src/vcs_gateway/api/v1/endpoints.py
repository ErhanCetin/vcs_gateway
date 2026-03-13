"""
Service-specific REST endpoints — v1.

Replace the example endpoint with your actual API surface.
If this service is queue-consumer only (no REST), remove this file
and remove the v1_router import from main.py.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/ping")
async def ping() -> dict:
    """Example endpoint — replace or remove."""
    return {"message": "pong"}

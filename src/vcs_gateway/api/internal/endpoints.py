"""
Internal service-to-service endpoints — NOT part of the public API.

Mounted at /internal/v1 in main.py.
Used by: VCS Worker (duplicate/stale checks), platform tooling.
"""

from typing import Any

from fastapi import APIRouter, Query, Request

router = APIRouter(tags=["internal"])


@router.get("/events/check-duplicate")
async def check_duplicate(
    request: Request,
    pr_hash_key: str = Query(..., min_length=64, max_length=64),
) -> dict[str, Any]:
    """Check whether a pr_hash_key has already been processed (Redis → DB)."""
    return await request.app.state.vcs_service.check_duplicate(pr_hash_key)


@router.get("/events/check-stale")
async def check_stale(
    request: Request,
    pr_hash_key: str = Query(..., min_length=64, max_length=64),
    pr_version: int = Query(..., ge=1),
) -> dict[str, Any]:
    """Check whether the given pr_version is outdated compared to the stored record."""
    return await request.app.state.vcs_service.check_stale(pr_hash_key, pr_version)

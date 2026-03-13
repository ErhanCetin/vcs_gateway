"""
Internal service-to-service endpoints.

These are NOT public API — only other platform services call these.
Mount with prefix /internal/v1 in main.py when needed.

Example: VCS Gateway exposes /internal/v1/events/check-stale
"""

from fastapi import APIRouter

router = APIRouter()

# Add internal endpoints here when needed

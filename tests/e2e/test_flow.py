"""
End-to-end tests — full flow from HTTP request or queue message to DB assertion.

Uses all real infrastructure via testcontainers.
These tests are slower — run separately with: pytest -m e2e
"""

import pytest


@pytest.mark.e2e
async def test_health_endpoint_placeholder() -> None:
    """Replace with actual E2E flow tests."""
    pass

"""
Integration tests for DB repository layer.

Uses real PostgreSQL via testcontainers.
Each test gets a fresh connection pool.
"""

import pytest


@pytest.mark.integration
async def test_db_connection(db_pool) -> None:
    """Verify the test DB pool is functional."""
    result = await db_pool.fetchval("SELECT 1")
    assert result == 1

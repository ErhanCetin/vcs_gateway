"""
Pytest fixtures for all test levels.

Unit tests: use mock fixtures (no containers needed)
Integration tests: use testcontainers (real PostgreSQL, RabbitMQ, Redis)
"""

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from testcontainers.postgres import PostgresContainer
from testcontainers.rabbitmq import RabbitMqContainer
from testcontainers.redis import RedisContainer

# ---------------------------------------------------------------------------
# Event loop — single loop for the entire test session
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# Testcontainers — session-scoped (start once, reuse across all tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:15-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def rabbitmq_container():
    with RabbitMqContainer("rabbitmq:3.13-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def redis_container():
    with RedisContainer("redis:7-alpine") as container:
        yield container


# ---------------------------------------------------------------------------
# DB pool — function-scoped (fresh pool per test, rolled back after)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="function")
async def db_pool(postgres_container):
    import asyncpg
    pool = await asyncpg.create_pool(
        dsn=postgres_container.get_connection_url().replace("psycopg2", ""),
        min_size=1,
        max_size=5,
    )
    yield pool
    await pool.close()


# ---------------------------------------------------------------------------
# Redis client — function-scoped
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="function")
async def redis_client(redis_container):
    import redis.asyncio as aioredis
    client = aioredis.Redis.from_url(
        f"redis://{redis_container.get_container_host_ip()}:{redis_container.get_exposed_port(6379)}",
        decode_responses=True,
    )
    yield client
    await client.aclose()


# ---------------------------------------------------------------------------
# AMQP connection — function-scoped
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="function")
async def amqp_connection(rabbitmq_container):
    import aio_pika
    conn = await aio_pika.connect_robust(
        f"amqp://guest:guest@{rabbitmq_container.get_container_host_ip()}:{rabbitmq_container.get_exposed_port(5672)}/"
    )
    yield conn
    await conn.close()

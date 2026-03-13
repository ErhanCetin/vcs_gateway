# VCS Gateway — Claude Code Instructions

## Service Context

**What this service does:**
VCS Gateway is the primary entry point for all webhook events from GitHub, GitLab, and Bitbucket. It validates incoming PR webhooks, filters irrelevant events, checks for duplicates via Redis + DB idempotency, persists the event, and publishes it to RabbitMQ for downstream processing. It is a defensive layer — it never performs analysis, quota checks, or normalization.

**Position in pipeline:**
[External VCS Providers] → **VCS GATEWAY** → VCS Worker

**Reference docs:**
- Service doc: `/Users/ironman/netconomy/private/ai_dev_performance/NEW_MICROSERVICES/focus/SERVICE_DETAILS/services/vcs_gateway_service.md`
- Full architecture: `/Users/ironman/netconomy/private/ai_dev_performance/NEW_MICROSERVICES/focus/SERVICE_DETAILS/Architecture/DATA_FLOW_V2.md`

---

## Service Responsibilities (what to implement)

1. **Tenant validation** — Extract `tenant_id` from URL `/webhooks/{vcs_provider}/{tenant_id}`, verify active in DB
2. **Event type filtering** — Only allow PR events (opened/synchronize/reopened for GitHub; open/update/reopen for GitLab)
3. **Webhook signature validation** — HMAC-SHA256 (GitHub `X-Hub-Signature-256`), GitLab token (`X-Gitlab-Token`)
4. **PR Hash deduplication** — SHA256 of `vcs_provider:tenant_id:repo_id:pr_id:vcs_instance_id:action:commit_sha`
   - Cache-Aside: check Redis first → miss → query `inbound_event` DB → write-back to Redis (TTL 72h)
5. **Event persistence** — Save `inbound_event` to DB + Outbox pattern for queue publishing
6. **Journey event** — Fire-and-forget publish of `journey.step.created` to RabbitMQ (BasePublisher, NOT outbox)
7. **Queue publishing** — Outbox publishes to `vcs.webhook.received` exchange after DB commit

## What This Service Does NOT Do

- ❌ Quota checks (VCS Worker → Policy Engine)
- ❌ Payload normalization (VCS Worker)
- ❌ LLM calls (LLM Service)
- ❌ Auto-onboarding of tenants/repos (VCS Worker)
- ❌ Writing to Redis — READ-ONLY on idempotency cache

---

## Project Structure

```
src/vcs_gateway/
├── main.py          # FastAPI app + lifespan (DB, AMQP, Redis, Outbox startup)
├── worker.py        # Queue consumer entrypoint (NOT used by this service — API only)
├── config.py        # All env vars via pydantic-settings (single Settings class)
├── api/
│   ├── health.py    # /health/live + /health/ready
│   ├── v1/
│   │   └── webhooks.py  # POST /webhooks/{vcs_provider}/{tenant_id}
│   └── internal/
│       └── endpoints.py # GET /internal/events/{pr_hash_key} (stale check fallback)
├── core/
│   ├── logging.py   # structlog JSON configuration
│   ├── exceptions.py# Domain exception hierarchy
│   └── middleware.py# CorrelationId + RequestLogging middleware
├── db/
│   ├── connection.py# asyncpg pool creation
│   ├── repository.py# BaseRepository with transaction() context manager
│   └── outbox.py    # OutboxRepository (insert) + OutboxPublisher (background)
├── queue/
│   ├── connection.py# aio-pika RobustConnection
│   ├── consumer.py  # BaseConsumer (not used here — API-only service)
│   └── publisher.py # BasePublisher (journey events — fire-and-forget)
├── redis/
│   └── client.py    # Redis client + is_stale() + get_idempotency_cache()
├── models/
│   ├── events.py    # WebhookReceivedMessage, JourneyStepEvent (Pydantic v2)
│   └── domain.py    # InboundEvent, Tenant domain models
└── services/
    └── vcs_gateway.py  # Business logic orchestration
```

---

## Database Schema (vcs_gateway_schema)

Key tables:
- `tenant` — tenant_id (UUID), name, is_active, webhook_secret, plan_type
- `inbound_event` — id, tenant_id, vcs_provider, repo_id, pr_id, commit_sha, pr_hash_key (UNIQUE), correlation_id, raw_payload, status, created_at
- `outbox_event` — id, exchange, routing_key, payload, status (pending/published/failed), created_at

---

## Architecture Rules

- **Async everywhere** — every function that does I/O must be `async def`. Never use blocking calls.
- **Repository pattern** — `services/vcs_gateway.py` never touches DB directly. All DB access via repository classes.
- **Outbox pattern** — queue publishing for `vcs.webhook.received` MUST use `OutboxRepository.insert_event()` inside a DB transaction. Journey events use `BasePublisher` (fire-and-forget, loss is acceptable).
- **Pydantic v2** — all data models use Pydantic v2. No dataclasses, no TypedDict for API/queue models.
- **Cache-Aside idempotency** — check Redis first, then DB on miss, write-back to Redis.

---

## Code Rules

- **Type hints** — every function must have full type annotations (args + return type). mypy strict mode.
- **Logging** — use `structlog` only. `print()` is forbidden. Get logger with `get_logger(__name__)`.
- **SQL queries** — parameterized queries only. String interpolation in SQL is forbidden.
- **Exceptions** — raise from `core/exceptions.py` hierarchy. Never raise bare `Exception`.
- **Imports** — use absolute imports from `vcs_gateway.*`. No relative imports.
- **Line length** — max 100 characters (ruff enforced).

---

## Constraints — Do NOT

- ❌ Add new dependencies without justification and updating `pyproject.toml`
- ❌ Change business logic unless explicitly asked
- ❌ Modify DB schema without creating an Alembic migration file
- ❌ Use blocking I/O anywhere in the async path
- ❌ Write to Redis — this service is READ-ONLY on the idempotency cache
- ❌ Catch exceptions silently — always log with context before re-raising
- ❌ Use `print()` — use structlog
- ❌ Perform quota checks or payload normalization — that's VCS Worker's job
- ❌ Use `worker.py` — VCS Gateway is API-only, no queue consumption

---

## When Reviewing Code — Always Return

1. **Findings** — what issues exist (with file:line references)
2. **Risks** — concurrency, transaction boundaries, data loss risk, HMAC validation bypass
3. **Suggested fix** — concrete change, not generic advice

---

## Test Rules

- **Unit tests** (`tests/unit/`) — mock all external I/O. Test: signature validation, hash calculation, event type filtering, stale detection logic.
- **Integration tests** (`tests/integration/`) — use testcontainers for real PostgreSQL + Redis + RabbitMQ. Test: full webhook flow end-to-end, duplicate rejection, outbox publishing.
- **Every endpoint** must have both unit and integration tests.
- Run tests: `uv run pytest`

---

## Common Commands

```bash
# Install dependencies
uv sync

# Run API server (local)
uv run uvicorn vcs_gateway.main:app --reload --port 8001

# Run tests
uv run pytest
uv run pytest tests/unit -m unit
uv run pytest tests/integration -m integration

# Lint + format
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Type check
uv run mypy src/

# DB migration
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "description"

# Start local infra
docker compose up -d
```

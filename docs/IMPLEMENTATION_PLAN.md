# VCS Gateway — Implementation Plan

> **For Claude:** This is the authoritative, ordered guide for implementing the VCS Gateway service.
> Read this file at the start of every session before writing any code.
> Complete phases in order. Do not skip ahead.
>
> **Service doc:** `docs/vcs_gateway_service.md`
> **Architecture:** `/Users/ironman/netconomy/private/ai_dev_performance/NEW_MICROSERVICES/focus/SERVICE_DETAILS/Architecture/DATA_FLOW_V2.md` (Step 0 / Step 1)

---

## Quick Status Tracker

Update this table as phases are completed:

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Prerequisites & verification | ✅ done |
| 1 | Config — add missing settings | ✅ done |
| 2 | DB Migration — shared schema seed + inbound_event + outbox_event | ✅ done |
| 3 | Domain Models & Queue Models | ✅ done |
| 4 | Repository Layer | ✅ done |
| 5 | Signature Validation (pure functions) | ✅ done |
| 6 | Redis Layer | ✅ done |
| 7 | Business Logic — VcsGatewayService | ✅ done |
| 8 | API Endpoints | ✅ done |
| 9 | Outbox Dispatcher — debounce logic | ✅ done |
| 10 | Tests | ⬜ todo |

Status values: ⬜ todo | 🔄 in progress | ✅ done

---

## Shared Schema Dependencies

VCS Gateway **reads** these shared tables (does NOT own them):

| Table | Access | Purpose |
|-------|--------|---------|
| `shared_schema.customer` | READ-ONLY | Customer info (display/logging only) |
| `shared_schema.tenant` | READ-ONLY | Tenant validation + webhook_secret |
| `shared_schema.vcs_event_whitelist` | READ-ONLY | Event type filtering |

VCS Gateway **writes** to:

| Table | Access | Purpose |
|-------|--------|---------|
| `shared_schema.tenant_vcs_config` | WRITE (upsert) | Auto-register new repos on first webhook |

**In production:** `shared_schema` is bootstrapped by a dedicated platform-level migration tool.
**In local dev:** Tables are seeded by migration `0002_seed_shared_schema.py` (see Phase 2).

**Canonical reference:** `/Users/ironman/netconomy/private/ai_dev_performance/NEW_MICROSERVICES/focus/SERVICE_DETAILS/Architecture/DATA_FLOW_V2_shared_db.md`

---

## Phase 0 — Prerequisites

**Goal:** Verify the skeleton compiles and local infra is running before writing any code.

### Read these files first (every session)
- `CLAUDE.md` — service rules and constraints
- `memory/MEMORY.md` — decisions made in previous sessions
- This file — check the status tracker above

### Verify skeleton
```bash
cd /Users/ironman/netconomy/private/ai-dev-engineering-knowledge/vcs-gateway

uv sync
uv run ruff check src/
uv run mypy src/
```
All three must succeed before starting any phase.

### Start local infrastructure
```bash
docker compose up -d
# Wait ~10 seconds for healthchecks to pass
docker compose ps
# All services should show "healthy"
```

---

## Phase 1 — Config

**File:** `src/vcs_gateway/config.py`
**Goal:** Add all VCS Gateway-specific settings to the `Settings` class.

**Read first:** Current `src/vcs_gateway/config.py`

### Add these fields to `Settings`

```python
# RabbitMQ exchange names
rabbitmq_exchange_webhook: str = "vcs.webhook.received"
rabbitmq_exchange_journey: str = "journey.events"

# Redis
redis_idempotency_ttl_seconds: int = 259200  # 72 hours

# Outbox debounce
outbox_debounce_seconds: int = 30

# Webhook validation
webhook_hmac_algorithm: str = "sha256"
```

### Also fix the service_name field

The template left `vcs_gateway: str = "vcs-gateway"` which is wrong. Change to:
```python
service_name: str = "vcs-gateway"
```

### Verify
```bash
uv run ruff check src/vcs_gateway/config.py
uv run mypy src/vcs_gateway/config.py
```

---

## Phase 2 — DB Migration

**Goal:** Create all tables needed by VCS Gateway — including shared schema seed for local dev.

### Step 2a — Create `migrations/versions/0002_seed_shared_schema.py`

**Purpose:** Seed `shared_schema` tables for local development. In production, these tables are managed by the platform migration pipeline and this migration is a no-op (uses `IF NOT EXISTS` + `ON CONFLICT DO NOTHING`).

Create new Alembic migration:
```bash
uv run alembic revision --autogenerate -m "seed_shared_schema"
# Then replace the generated body with the raw SQL below
```

Migration `upgrade()` body:
```python
op.execute("CREATE SCHEMA IF NOT EXISTS shared_schema")

# customer
op.execute("""
CREATE TABLE IF NOT EXISTS shared_schema.customer (
    customer_id     UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name    VARCHAR(255) NOT NULL,
    contact_email   VARCHAR(255) NOT NULL UNIQUE,
    billing_email   VARCHAR(255),
    plan_type       VARCHAR(50)  NOT NULL DEFAULT 'starter',
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
)
""")
op.execute("CREATE INDEX IF NOT EXISTS idx_customer_email ON shared_schema.customer (contact_email)")

# tenant
op.execute("""
CREATE TABLE IF NOT EXISTS shared_schema.tenant (
    tenant_id       UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id     UUID         NOT NULL REFERENCES shared_schema.customer (customer_id),
    tenant_name     VARCHAR(255) NOT NULL,
    slug            VARCHAR(100) NOT NULL UNIQUE,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    webhook_secret  VARCHAR(255) NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
)
""")
op.execute("CREATE INDEX IF NOT EXISTS idx_tenant_customer ON shared_schema.tenant (customer_id)")
op.execute("CREATE INDEX IF NOT EXISTS idx_tenant_slug ON shared_schema.tenant (slug)")

# tenant_vcs_config
op.execute("""
CREATE TABLE IF NOT EXISTS shared_schema.tenant_vcs_config (
    config_id       UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID         NOT NULL REFERENCES shared_schema.tenant (tenant_id),
    vcs_provider    VARCHAR(50)  NOT NULL,
    vcs_instance_id VARCHAR(255) NOT NULL DEFAULT 'github.com',
    repo_id         VARCHAR(255) NOT NULL,
    repo_name       VARCHAR(500),
    repo_url        VARCHAR(1000),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tenant_vcs_repo UNIQUE (tenant_id, vcs_provider, repo_id)
)
""")

# vcs_event_whitelist
op.execute("""
CREATE TABLE IF NOT EXISTS shared_schema.vcs_event_whitelist (
    whitelist_id    UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    vcs_provider    VARCHAR(50)  NOT NULL,
    event_type      VARCHAR(100) NOT NULL,
    event_action    VARCHAR(100) NOT NULL,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    description     TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_vcs_event UNIQUE (vcs_provider, event_type, event_action)
)
""")
op.execute("""
CREATE INDEX IF NOT EXISTS idx_vcs_event_whitelist_lookup
    ON shared_schema.vcs_event_whitelist (vcs_provider, event_type, event_action)
    WHERE is_active = TRUE
""")

# Seed: event whitelist
op.execute("""
INSERT INTO shared_schema.vcs_event_whitelist (vcs_provider, event_type, event_action) VALUES
  ('github', 'pull_request',       'opened'),
  ('github', 'pull_request',       'synchronize'),
  ('github', 'pull_request',       'reopened'),
  ('gitlab', 'Merge Request Hook', 'open'),
  ('gitlab', 'Merge Request Hook', 'update'),
  ('gitlab', 'Merge Request Hook', 'reopen')
ON CONFLICT (vcs_provider, event_type, event_action) DO NOTHING
""")

# Seed: 1 test customer + 1 test tenant (local dev only)
op.execute("""
WITH ins_customer AS (
    INSERT INTO shared_schema.customer (company_name, contact_email, plan_type)
    VALUES ('Acme Corp', 'admin@acme.com', 'growth')
    ON CONFLICT (contact_email) DO NOTHING
    RETURNING customer_id
)
INSERT INTO shared_schema.tenant (customer_id, tenant_name, slug, webhook_secret)
SELECT customer_id, 'Acme Dev Team', 'acme-dev', 'test-secret-12345'
FROM ins_customer
ON CONFLICT (slug) DO NOTHING
""")
```

Migration `downgrade()` body:
```python
# Drop only if running a full teardown — shared schema is owned by platform
# In practice, never downgrade shared_schema in production
op.execute("DROP TABLE IF EXISTS shared_schema.tenant_vcs_config")
op.execute("DROP TABLE IF EXISTS shared_schema.vcs_event_whitelist")
op.execute("DROP TABLE IF EXISTS shared_schema.tenant")
op.execute("DROP TABLE IF EXISTS shared_schema.customer")
```

---

**Read first:** `migrations/versions/0001_initial.py` (current state — only has outbox_event)

### Step 2b — Replace outbox_event table

The template `outbox_event` is too generic. Replace with the VCS Gateway schema:

```sql
CREATE TABLE IF NOT EXISTS vcs_gateway_schema.outbox_event (
    outbox_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      TEXT        NOT NULL,                   -- 'vcs.webhook.received'
    correlation_id  UUID        NOT NULL,
    pr_hash_key     VARCHAR(64),
    pr_version      INTEGER,
    payload         JSONB       NOT NULL,
    headers         JSONB       NOT NULL DEFAULT '{}',
    status          VARCHAR(20) NOT NULL DEFAULT 'SCHEDULED',
    dispatch_at     TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '30 seconds',
    cancel_reason   VARCHAR(50),
    retry_count     INTEGER     NOT NULL DEFAULT 0,
    max_retries     INTEGER     NOT NULL DEFAULT 3,
    next_retry_at   TIMESTAMPTZ,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at    TIMESTAMPTZ,
    CONSTRAINT outbox_event_status_check
        CHECK (status IN ('SCHEDULED', 'CANCELLED', 'DISPATCHED', 'FAILED'))
)
```

Index:
```sql
CREATE INDEX idx_outbox_scheduled
ON vcs_gateway_schema.outbox_event (dispatch_at)
WHERE status = 'SCHEDULED'
```

### Step 2c — Create inbound_event table

```sql
CREATE TABLE IF NOT EXISTS vcs_gateway_schema.inbound_event (
    event_id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    correlation_id    UUID         NOT NULL,
    tenant_id         UUID         NOT NULL,
    vcs_provider      VARCHAR(50)  NOT NULL,   -- 'github' | 'gitlab' | 'bitbucket'
    vcs_instance_id   VARCHAR(255) NOT NULL DEFAULT 'github.com',
    repo_id           VARCHAR(255) NOT NULL,
    repo_name         VARCHAR(500),
    pr_id             VARCHAR(255) NOT NULL,
    pr_title          TEXT,
    pr_author         VARCHAR(255),
    pr_url            VARCHAR(1000),
    commit_sha        VARCHAR(64)  NOT NULL,
    action            VARCHAR(50)  NOT NULL,   -- 'opened' | 'synchronize' | 'reopened'
    pr_hash_key       VARCHAR(64)  NOT NULL,
    pr_version        INTEGER      NOT NULL DEFAULT 1,
    processing_status VARCHAR(50)  NOT NULL DEFAULT 'accepted',
    rejection_reason  VARCHAR(255),
    raw_payload       JSONB        NOT NULL,
    webhook_headers   JSONB,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT inbound_event_status_check
        CHECK (processing_status IN ('accepted', 'rejected', 'duplicate'))
)
```

Indexes:
```sql
CREATE UNIQUE INDEX uq_inbound_event_correlation_id
    ON vcs_gateway_schema.inbound_event (correlation_id);

CREATE UNIQUE INDEX uq_inbound_event_pr_hash_key
    ON vcs_gateway_schema.inbound_event (pr_hash_key);

CREATE INDEX idx_inbound_event_tenant_created
    ON vcs_gateway_schema.inbound_event (tenant_id, created_at DESC);

CREATE INDEX idx_inbound_event_tenant_pr
    ON vcs_gateway_schema.inbound_event (tenant_id, pr_id);

CREATE INDEX idx_inbound_event_status_created
    ON vcs_gateway_schema.inbound_event (processing_status, created_at DESC);
```

### Step 2d — Apply migrations
```bash
uv run alembic upgrade head
```

Verify in psql:
```bash
docker exec -it vcs-gateway-postgres-1 psql -U appuser -d vcs_gateway_db \
  -c "\dt vcs_gateway_schema.*"
# Should list: inbound_event, outbox_event

docker exec -it vcs-gateway-postgres-1 psql -U appuser -d vcs_gateway_db \
  -c "\dt shared_schema.*"
# Should list: customer, tenant, tenant_vcs_config, vcs_event_whitelist

docker exec -it vcs-gateway-postgres-1 psql -U appuser -d vcs_gateway_db \
  -c "SELECT tenant_id, tenant_name, slug FROM shared_schema.tenant"
# Should show the test tenant seeded above
```

---

## Phase 3 — Domain Models

**Files:** `src/vcs_gateway/models/domain.py`, `src/vcs_gateway/models/events.py`, `src/vcs_gateway/models/requests.py` (new)
**Goal:** Define all Pydantic v2 models used throughout the service.

**Read first:** Current stubs of `models/domain.py` and `models/events.py`

### 3a — domain.py

Add these models (all `model_config = ConfigDict(from_attributes=True, frozen=True)`):

```python
class Tenant(BaseDomainModel):
    tenant_id: UUID
    customer_id: UUID           # FK → shared_schema.customer
    name: str
    is_active: bool
    webhook_secret: str
    plan_type: str              # joined from shared_schema.customer.plan_type
    customer_plan_type: str | None = None  # alias for plan_type when joined

class VcsEventWhitelist(BaseDomainModel):
    vcs_provider: str
    event_type: str
    event_action: str
    is_active: bool

class InboundEvent(BaseDomainModel):
    event_id: UUID
    correlation_id: UUID
    tenant_id: UUID
    vcs_provider: str
    vcs_instance_id: str
    repo_id: str
    repo_name: str | None
    pr_id: str
    pr_title: str | None
    pr_author: str | None
    pr_url: str | None
    commit_sha: str
    action: str
    pr_hash_key: str
    pr_version: int
    processing_status: str
    rejection_reason: str | None
    raw_payload: dict[str, Any]
    webhook_headers: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

class OutboxEvent(BaseDomainModel):
    outbox_id: UUID
    event_type: str
    correlation_id: UUID
    pr_hash_key: str | None
    pr_version: int | None
    payload: dict[str, Any]
    headers: dict[str, Any]
    status: str
    dispatch_at: datetime
    cancel_reason: str | None
    retry_count: int
    max_retries: int
    next_retry_at: datetime | None
    error_message: str | None
    created_at: datetime
    published_at: datetime | None
```

### 3b — events.py

Replace placeholder content with:

```python
class JourneyStepType(str, Enum):
    webhook_received = "webhook_received"
    signature_verified = "signature_verified"
    event_type_validated = "event_type_validated"
    idempotency_checked = "idempotency_checked"
    event_persisted = "event_persisted"
    outbox_scheduled = "outbox_scheduled"

class JourneyStepStatus(str, Enum):
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"
    info = "info"

class JourneyStepEvent(BaseMessage):
    service_name: str = "vcs-gateway"
    step_type: JourneyStepType
    status: JourneyStepStatus
    tenant_id: UUID
    pr_hash_key: str | None = None
    pr_id: str | None = None
    repo_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

class WebhookReceivedMessage(BaseMessage):
    event_id: UUID
    tenant_id: UUID
    vcs_provider: str
    repo_id: str
    repo_name: str | None
    pr_id: str
    pr_title: str | None
    pr_author: str | None
    pr_url: str | None
    commit_sha: str
    pr_action: str
    pr_hash_key: str
    pr_version: int
    raw_payload: dict[str, Any]
```

### 3c — models/requests.py (new file)

```python
class GitHubPullRequest(BaseModel):
    number: int
    title: str
    user: dict[str, Any]          # {"login": "username"}
    html_url: str
    head: dict[str, Any]          # {"sha": "abc123"}
    base: dict[str, Any]

class GitHubRepository(BaseModel):
    id: int
    full_name: str                 # "org/repo"
    html_url: str

class GitHubWebhookPayload(BaseModel):
    action: str
    pull_request: GitHubPullRequest
    repository: GitHubRepository

class GitLabObjectAttributes(BaseModel):
    iid: int                      # MR number
    title: str
    url: str
    last_commit: dict[str, Any]   # {"id": "abc123"}
    action: str
    author_id: int

class GitLabProject(BaseModel):
    id: int
    path_with_namespace: str      # "group/repo"
    web_url: str

class GitLabWebhookPayload(BaseModel):
    object_kind: str              # "merge_request"
    object_attributes: GitLabObjectAttributes
    project: GitLabProject
    user: dict[str, Any]          # {"username": "...", "email": "..."}

class PullRequestData(BaseModel):
    """Normalized PR data extracted from any VCS webhook payload."""
    pr_id: str
    repo_id: str
    repo_name: str | None
    pr_title: str
    pr_author: str
    pr_url: str
    commit_sha: str
    action: str                   # normalized: 'opened' | 'synchronize' | 'reopened'
    vcs_instance_id: str = "github.com"
```

### Verify
```bash
uv run ruff check src/vcs_gateway/models/
uv run mypy src/vcs_gateway/models/
```

---

## Phase 4 — Repository Layer

**Goal:** DB access classes. Business logic never calls the DB directly.

**Read first:** `src/vcs_gateway/db/repository.py` (BaseRepository) and `src/vcs_gateway/db/outbox.py`

### 4a — Create `db/repositories/` package

```bash
mkdir -p src/vcs_gateway/db/repositories
touch src/vcs_gateway/db/repositories/__init__.py
```

### 4b — `db/repositories/tenant_repository.py`

```python
class TenantRepository(BaseRepository):
    async def get_by_id(self, tenant_id: UUID) -> Tenant | None:
        """Fetch tenant config with plan_type from parent customer. Returns None if not found."""
        row = await self.fetchrow(
            """
            SELECT t.tenant_id, t.customer_id, t.tenant_name AS name,
                   t.is_active, t.webhook_secret,
                   c.plan_type, c.plan_type AS customer_plan_type
            FROM shared_schema.tenant t
            JOIN shared_schema.customer c ON c.customer_id = t.customer_id
            WHERE t.tenant_id = $1
            """,
            tenant_id,
        )
        return Tenant.model_validate(dict(row)) if row else None

    async def get_event_whitelist(self, vcs_provider: str) -> list[VcsEventWhitelist]:
        """Fetch allowed event types for a VCS provider."""
        rows = await self.fetch(
            "SELECT vcs_provider, event_type, event_action, is_active "
            "FROM shared_schema.vcs_event_whitelist "
            "WHERE vcs_provider = $1 AND is_active = TRUE",
            vcs_provider,
        )
        return [VcsEventWhitelist.model_validate(dict(r)) for r in rows]
```

**Note:** `shared_schema.*` tables are owned by the platform but seeded locally via migration 0002.

### 4c — `db/repositories/tenant_vcs_config_repository.py` (new)

```python
class TenantVcsConfigRepository(BaseRepository):
    async def upsert(
        self,
        conn: asyncpg.Connection,
        tenant_id: UUID,
        vcs_provider: str,
        vcs_instance_id: str,
        repo_id: str,
        repo_name: str | None,
        repo_url: str | None,
    ) -> None:
        """
        Auto-onboard: register repo on first webhook. Idempotent via ON CONFLICT.
        Must be called within an existing transaction.
        """
        await conn.execute(
            """
            INSERT INTO shared_schema.tenant_vcs_config
                (tenant_id, vcs_provider, vcs_instance_id, repo_id, repo_name, repo_url)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (tenant_id, vcs_provider, repo_id) DO UPDATE SET
                repo_name  = EXCLUDED.repo_name,
                repo_url   = EXCLUDED.repo_url,
                is_active  = TRUE,
                updated_at = NOW()
            """,
            tenant_id, vcs_provider, vcs_instance_id, repo_id, repo_name, repo_url,
        )
```

### 4e — `db/repositories/inbound_event_repository.py`

```python
class InboundEventRepository(BaseRepository):
    async def get_by_pr_hash_key(self, pr_hash_key: str) -> InboundEvent | None:
        """Check if this exact PR+action+commit was already received."""
        row = await self.fetchrow(
            "SELECT * FROM vcs_gateway_schema.inbound_event "
            "WHERE pr_hash_key = $1",
            pr_hash_key,
        )
        return InboundEvent.model_validate(dict(row)) if row else None

    async def insert(
        self,
        conn: asyncpg.Connection,
        event_data: dict[str, Any],
    ) -> InboundEvent:
        """Insert within an existing transaction. Raises DuplicateError on conflict."""
        row = await conn.fetchrow(
            """
            INSERT INTO vcs_gateway_schema.inbound_event (
                event_id, correlation_id, tenant_id, vcs_provider, vcs_instance_id,
                repo_id, repo_name, pr_id, pr_title, pr_author, pr_url,
                commit_sha, action, pr_hash_key, pr_version, processing_status,
                raw_payload, webhook_headers
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                $12, $13, $14, $15, $16, $17, $18
            )
            RETURNING *
            """,
            *event_data.values(),  # ordered per INSERT columns above
        )
        return InboundEvent.model_validate(dict(row))
```

### Verify
```bash
uv run ruff check src/vcs_gateway/db/
uv run mypy src/vcs_gateway/db/
```

---

## Phase 5 — Signature Validation

**File:** `src/vcs_gateway/core/signature.py` (new file)
**Goal:** Pure functions — no I/O, no dependencies. Fully unit-testable.

```python
import hashlib
import hmac

def validate_github_signature(
    payload_bytes: bytes,
    secret: str,
    signature_header: str,
) -> bool:
    """
    Validate GitHub HMAC-SHA256 webhook signature.
    signature_header format: "sha256=<hex_digest>"
    """
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def validate_gitlab_token(token_header: str, secret: str) -> bool:
    """
    Validate GitLab webhook token (constant-time comparison).
    GitLab sends the secret as plaintext in X-Gitlab-Token.
    """
    return hmac.compare_digest(token_header.encode(), secret.encode())


def compute_pr_hash_key(
    vcs_provider: str,
    tenant_id: str,
    repo_id: str,
    pr_id: str,
    vcs_instance_id: str,
    action: str,
    commit_sha: str,
) -> str:
    """
    Compute the idempotency hash for a PR webhook event.
    Format: SHA256(vcs_provider:tenant_id:repo_id:pr_id:vcs_instance_id:action:commit_sha)
    """
    key = f"{vcs_provider}:{tenant_id}:{repo_id}:{pr_id}:{vcs_instance_id}:{action}:{commit_sha}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
```

### Verify
```bash
uv run ruff check src/vcs_gateway/core/signature.py
uv run mypy src/vcs_gateway/core/signature.py
```

---

## Phase 6 — Redis Layer

**File:** `src/vcs_gateway/redis/client.py`
**Goal:** Add VCS Gateway-specific Redis operations to the existing client module.

**Read first:** Current `src/vcs_gateway/redis/client.py`

### Add these functions

```python
async def get_tenant_cache(
    client: redis.Redis,
    tenant_id: UUID,
) -> dict[str, Any] | None:
    """
    Cache-Aside: read tenant config from Redis.
    Key: tenant:config:{tenant_id}
    Returns None on cache miss — caller must query DB and write back.
    TTL: 5 minutes (set by the caller using set_tenant_cache).
    """
    raw = await client.get(f"tenant:config:{tenant_id}")
    if raw is None:
        return None
    return json.loads(raw)


async def set_tenant_cache(
    client: redis.Redis,
    tenant_id: UUID,
    tenant_data: dict[str, Any],
    ttl_seconds: int = 300,  # 5 minutes
) -> None:
    """Write tenant config to Redis cache (best-effort, do not raise on failure)."""
    await client.setex(
        f"tenant:config:{tenant_id}",
        ttl_seconds,
        json.dumps(tenant_data, default=str),
    )


async def get_idempotency_cache(
    client: redis.Redis,
    pr_hash_key: str,
) -> str | None:
    """
    Cache-Aside: check if pr_hash_key was already processed.
    Key: idempotency:{pr_hash_key}
    Returns "1" on cache hit, None on miss.
    """
    return await client.get(f"idempotency:{pr_hash_key}")


async def set_idempotency_cache(
    client: redis.Redis,
    pr_hash_key: str,
    ttl_seconds: int = 259200,  # 72 hours
) -> None:
    """Write-back to idempotency cache after DB hit (best-effort)."""
    await client.setex(f"idempotency:{pr_hash_key}", ttl_seconds, "1")
```

**Important:** `set_tenant_cache` and `set_idempotency_cache` are called from the service layer, not from repositories. Wrap calls in `try/except` — Redis write failures must never abort the main flow.

### Verify
```bash
uv run ruff check src/vcs_gateway/redis/
uv run mypy src/vcs_gateway/redis/
```

---

## Phase 7 — Business Logic

**File:** `src/vcs_gateway/services/vcs_gateway.py`
**Goal:** Implement the full webhook processing orchestration (7 steps from service doc Section 4).

**Read first:**
- Service doc Section 4 (Data Flow — all 7 steps with field names)
- All completed phases above

### Class structure

```python
@dataclass
class WebhookAccepted:
    correlation_id: UUID
    event_id: UUID
    outbox_id: UUID

@dataclass
class WebhookDuplicate:
    pr_hash_key: str
    correlation_id: UUID
    detection_method: str  # "redis_cache" | "db_lookup"

@dataclass
class WebhookIgnored:
    code: str             # "EVENT_TYPE_NOT_ALLOWED"
    event_type: str

WebhookResult = WebhookAccepted | WebhookDuplicate | WebhookIgnored


class VcsGatewayService:
    async def process_webhook(
        self,
        tenant_id: UUID,
        vcs_provider: str,
        raw_payload: bytes,
        headers: dict[str, str],
        db_pool: asyncpg.Pool,
        redis_client: redis.Redis,
        journey_publisher: BasePublisher,
        settings: Settings,
    ) -> WebhookResult: ...
```

### Step-by-step implementation inside `process_webhook`

**Step 1 — Tenant validation**
```
correlation_id = uuid7()

Try Redis: get_tenant_cache(redis_client, tenant_id)
If miss:
    query TenantRepository.get_by_id(tenant_id)
    If None → raise NotFoundError("Tenant not found")
    If not tenant.is_active → raise BusinessRuleError("Tenant inactive")
    Write back: set_tenant_cache() — best effort (wrap in try/except)
Publish journey: step_type=webhook_received, status=in_progress
```

**Step 2 — Event type filtering**
```
event_type = headers.get("X-GitHub-Event") or headers.get("X-Gitlab-Event")
action = parsed_payload.get("action") or gitlab_payload.object_attributes.action

whitelist = TenantRepository.get_event_whitelist(vcs_provider)
If (event_type, action) not in whitelist:
    Publish journey: step_type=event_type_validated, status=info (not an error)
    return WebhookIgnored(code="EVENT_TYPE_NOT_ALLOWED", event_type=event_type)
```

**Step 3 — Signature validation**
```
if vcs_provider == "github":
    sig = headers.get("X-Hub-Signature-256", "")
    valid = validate_github_signature(raw_payload, tenant.webhook_secret, sig)
elif vcs_provider == "gitlab":
    token = headers.get("X-Gitlab-Token", "")
    valid = validate_gitlab_token(token, tenant.webhook_secret)

If not valid:
    Publish journey: step_type=signature_verified, status=failed
    raise ValidationError("Invalid webhook signature")

Publish journey: step_type=signature_verified, status=completed
```

**Step 4 — Parse payload**
```
if vcs_provider == "github":
    parsed = GitHubWebhookPayload.model_validate_json(raw_payload)
    pr_data = PullRequestData(
        pr_id=str(parsed.pull_request.number),
        repo_id=str(parsed.repository.id),
        repo_name=parsed.repository.full_name,
        pr_title=parsed.pull_request.title,
        pr_author=parsed.pull_request.user["login"],
        pr_url=parsed.pull_request.html_url,
        commit_sha=parsed.pull_request.head["sha"],
        action=parsed.action,
    )
elif vcs_provider == "gitlab":
    ... (similar for GitLab)

On ValidationError → raise ValidationError("Invalid payload schema")
```

**Step 5 — Idempotency check**
```
pr_hash_key = compute_pr_hash_key(
    vcs_provider, str(tenant_id), pr_data.repo_id,
    pr_data.pr_id, pr_data.vcs_instance_id, pr_data.action, pr_data.commit_sha
)

# Redis check first
cached = await get_idempotency_cache(redis_client, pr_hash_key)
if cached:
    Publish journey: step_type=idempotency_checked, status=info, metadata={"detection_method": "redis_cache"}
    return WebhookDuplicate(pr_hash_key, correlation_id, "redis_cache")

# DB fallback
existing = await InboundEventRepository.get_by_pr_hash_key(pr_hash_key)
if existing:
    # Write-back to Redis (best-effort)
    await set_idempotency_cache(redis_client, pr_hash_key, settings.redis_idempotency_ttl_seconds)
    Publish journey: step_type=idempotency_checked, status=info, metadata={"detection_method": "db_lookup"}
    return WebhookDuplicate(pr_hash_key, correlation_id, "db_lookup")

Publish journey: step_type=idempotency_checked, status=completed
```

**Step 6 — Transactional write**
```
async with db_pool.acquire() as conn:
    async with conn.transaction():
        event_id = uuid4()
        outbox_id = uuid4()
        dispatch_at = datetime.utcnow() + timedelta(seconds=settings.outbox_debounce_seconds)

        # 1. Insert inbound_event
        inbound = await InboundEventRepository.insert(conn, {
            event_id, correlation_id, tenant_id, vcs_provider, "github.com",
            pr_data.repo_id, pr_data.repo_name, pr_data.pr_id, pr_data.pr_title,
            pr_data.pr_author, pr_data.pr_url, pr_data.commit_sha, pr_data.action,
            pr_hash_key, pr_version=1, processing_status="accepted",
            raw_payload=json.loads(raw_payload), webhook_headers=relevant_headers
        })

        # 2. Auto-onboard: upsert tenant_vcs_config (idempotent, no-op if already exists)
        await TenantVcsConfigRepository.upsert(
            conn,
            tenant_id=tenant_id,
            vcs_provider=vcs_provider,
            vcs_instance_id=pr_data.vcs_instance_id,
            repo_id=pr_data.repo_id,
            repo_name=pr_data.repo_name,
            repo_url=pr_data.pr_url,  # use repo_url from pr_data if available
        )

        # 3. Insert outbox_event via OutboxRepository
        payload = WebhookReceivedMessage(
            event_id=event_id, correlation_id=correlation_id,
            tenant_id=tenant_id, vcs_provider=vcs_provider,
            ... (all fields from pr_data)
        ).model_dump()

        outbox = await OutboxRepository.insert_event(conn,
            exchange=settings.rabbitmq_exchange_webhook,
            routing_key="vcs.webhook.received",
            payload=payload,
            correlation_id=str(correlation_id),
            pr_hash_key=pr_hash_key,
            pr_version=1,
            dispatch_at=dispatch_at,
        )

Publish journey: step_type=outbox_scheduled, status=completed
return WebhookAccepted(correlation_id, event_id, outbox_id)
```

**Step 7 — Journey publishing (fire-and-forget helper)**
```python
async def _publish_journey_step(
    publisher: BasePublisher,
    step_type: JourneyStepType,
    status: JourneyStepStatus,
    **kwargs: Any,
) -> None:
    """Never raises — loss of journey events is acceptable."""
    try:
        event = JourneyStepEvent(
            event_id=uuid4(),
            event_type="journey.step.created",
            correlation_id=kwargs.get("correlation_id", uuid4()),
            tenant_id=kwargs.get("tenant_id"),
            step_type=step_type,
            status=status,
            pr_hash_key=kwargs.get("pr_hash_key"),
            pr_id=kwargs.get("pr_id"),
            repo_id=kwargs.get("repo_id"),
            metadata=kwargs.get("metadata", {}),
        )
        await publisher.publish(
            exchange=settings.rabbitmq_exchange_journey,
            routing_key="journey.step.created",
            payload=event.model_dump(),
        )
    except Exception:
        logger.warning("Failed to publish journey step — ignoring", step_type=step_type.value)
```

### Verify
```bash
uv run ruff check src/vcs_gateway/services/
uv run mypy src/vcs_gateway/services/
```

---

## Phase 8 — API Endpoints

**Files:**
- `src/vcs_gateway/api/v1/webhooks.py` (create new — replaces `endpoints.py` placeholder)
- `src/vcs_gateway/api/internal/endpoints.py` (update existing placeholder)
- `src/vcs_gateway/main.py` (update router registration)

**Read first:** Service doc Section 5 (API Specification — all response codes and payloads)

### 8a — `api/v1/webhooks.py`

```python
router = APIRouter(tags=["webhooks"])

@router.post("/webhooks/github/{tenant_id}", status_code=202)
async def receive_github_webhook(
    tenant_id: UUID,
    request: Request,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
) -> dict[str, Any]:
    raw_payload = await request.body()
    headers = {
        "X-Hub-Signature-256": x_hub_signature_256,
        "X-GitHub-Event": x_github_event,
    }

    service = VcsGatewayService()
    result = await service.process_webhook(
        tenant_id=tenant_id,
        vcs_provider="github",
        raw_payload=raw_payload,
        headers=headers,
        db_pool=request.app.state.db_pool,
        redis_client=request.app.state.redis,
        journey_publisher=request.app.state.journey_publisher,
        settings=get_settings(),
    )

    return _build_response(result)


@router.post("/webhooks/gitlab/{tenant_id}", status_code=202)
async def receive_gitlab_webhook(...): ...  # same pattern, different headers


def _build_response(result: WebhookResult) -> tuple[dict[str, Any], int]:
    if isinstance(result, WebhookAccepted):
        return {
            "status": "accepted",
            "correlation_id": str(result.correlation_id),
            "event_id": str(result.event_id),
            "outbox_id": str(result.outbox_id),
            "message": "Webhook scheduled for processing",
            "dispatch_in": "30s",
        }, 202
    elif isinstance(result, WebhookDuplicate):
        return {
            "status": "ignored",
            "code": "DUPLICATE_WEBHOOK",
            "pr_hash_key": result.pr_hash_key,
            "correlation_id": str(result.correlation_id),
            "detection_method": result.detection_method,
        }, 200
    elif isinstance(result, WebhookIgnored):
        return {
            "status": "ignored",
            "code": result.code,
            "event_type": result.event_type,
        }, 200
```

Exception handlers in `main.py` (map domain exceptions to HTTP responses):
- `NotFoundError` → 404
- `ValidationError` (signature / payload) → 401 or 400 (depending on context)
- `BusinessRuleError` (tenant inactive) → 403
- `DatabaseError` → 500 with `Retry-After: 60` header

### 8b — `api/internal/endpoints.py`

```python
router = APIRouter(tags=["internal"])

@router.get("/events/check-duplicate")
async def check_duplicate(
    pr_hash_key: str = Query(..., min_length=64, max_length=64),
    request: Request = ...,
) -> dict[str, Any]:
    # Check Redis first, then DB
    cached = await get_idempotency_cache(request.app.state.redis, pr_hash_key)
    if cached:
        return {"is_duplicate": True, "cache_hit": True, "pr_hash_key": pr_hash_key}

    repo = InboundEventRepository(request.app.state.db_pool)
    event = await repo.get_by_pr_hash_key(pr_hash_key)
    if event:
        return {"is_duplicate": True, "cache_hit": False, "existing_event_id": str(event.event_id), ...}

    return {"is_duplicate": False, "cache_hit": False, "pr_hash_key": pr_hash_key}


@router.get("/events/check-stale")
async def check_stale(
    pr_hash_key: str = Query(...),
    pr_version: int = Query(..., ge=1),
    request: Request = ...,
) -> dict[str, Any]:
    repo = InboundEventRepository(request.app.state.db_pool)
    event = await repo.get_by_pr_hash_key(pr_hash_key)
    if event and event.pr_version > pr_version:
        return {"is_stale": True, "provided_version": pr_version, "latest_version": event.pr_version, ...}
    return {"is_stale": False, "pr_hash_key": pr_hash_key}
```

### 8c — Update `main.py` router registration

```python
from vcs_gateway.api.v1.webhooks import router as webhooks_router
from vcs_gateway.api.internal.endpoints import router as internal_router

app.include_router(webhooks_router)
app.include_router(internal_router, prefix="/internal/v1")
```

Also add to `app.state` in lifespan:
```python
app.state.journey_publisher = BasePublisher(amqp_connection)
```

### Verify
```bash
uv run ruff check src/vcs_gateway/api/
uv run mypy src/vcs_gateway/api/
uv run uvicorn vcs_gateway.main:app --port 8001
curl http://localhost:8001/health/live
# Expected: {"status": "ok", ...}
```

---

## Phase 9 — Outbox Dispatcher

**File:** `src/vcs_gateway/db/outbox.py`
**Goal:** Update `OutboxPublisher.run()` to handle the 30-second debounce and SCHEDULED status.

**Read first:** Current `src/vcs_gateway/db/outbox.py`

### Updated dispatcher query

```python
async def run(self) -> None:
    while True:
        await asyncio.sleep(self.settings.outbox_poll_interval_seconds)
        try:
            await self._dispatch_batch()
        except Exception as e:
            logger.error("Outbox dispatch error", error=str(e))


async def _dispatch_batch(self) -> None:
    async with self.pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT * FROM vcs_gateway_schema.outbox_event
                WHERE status = 'SCHEDULED'
                  AND dispatch_at <= NOW()
                ORDER BY dispatch_at ASC
                LIMIT $1
                FOR UPDATE SKIP LOCKED
                """,
                self.settings.outbox_batch_size,
            )

            for row in rows:
                # Check if superseded by a newer entry for same pr_hash_key
                if row["pr_hash_key"]:
                    newer = await conn.fetchrow(
                        """
                        SELECT outbox_id FROM vcs_gateway_schema.outbox_event
                        WHERE pr_hash_key = $1
                          AND pr_version > $2
                          AND status = 'SCHEDULED'
                        LIMIT 1
                        """,
                        row["pr_hash_key"], row["pr_version"],
                    )
                    if newer:
                        await conn.execute(
                            "UPDATE vcs_gateway_schema.outbox_event "
                            "SET status = 'CANCELLED', cancel_reason = 'SUPERSEDED_BY_NEW_COMMIT' "
                            "WHERE outbox_id = $1",
                            row["outbox_id"],
                        )
                        continue

                # Publish to RabbitMQ
                try:
                    await self.publisher.publish(
                        exchange=self.settings.rabbitmq_exchange_webhook,
                        routing_key="vcs.webhook.received",
                        payload=row["payload"],
                        headers=row["headers"],
                    )
                    await conn.execute(
                        "UPDATE vcs_gateway_schema.outbox_event "
                        "SET status = 'DISPATCHED', published_at = NOW() "
                        "WHERE outbox_id = $1",
                        row["outbox_id"],
                    )
                except Exception as e:
                    retry_count = row["retry_count"] + 1
                    if retry_count >= row["max_retries"]:
                        await conn.execute(
                            "UPDATE vcs_gateway_schema.outbox_event "
                            "SET status = 'FAILED', retry_count = $1, error_message = $2 "
                            "WHERE outbox_id = $3",
                            retry_count, str(e), row["outbox_id"],
                        )
                    else:
                        next_retry = datetime.utcnow() + timedelta(seconds=30 * (2 ** retry_count))
                        await conn.execute(
                            "UPDATE vcs_gateway_schema.outbox_event "
                            "SET retry_count = $1, next_retry_at = $2, dispatch_at = $2 "
                            "WHERE outbox_id = $3",
                            retry_count, next_retry, row["outbox_id"],
                        )
```

### Verify
```bash
uv run ruff check src/vcs_gateway/db/outbox.py
uv run mypy src/vcs_gateway/db/outbox.py
```

---

## Phase 10 — Tests

**Goal:** Unit tests for pure logic, integration tests for full DB + RabbitMQ flows.

**Read first:** `tests/conftest.py` (testcontainers fixtures already set up)

### 10a — Unit tests

**`tests/unit/test_signature.py`**
```python
# Test cases:
# - valid github signature → True
# - invalid github signature → False
# - missing "sha256=" prefix → False
# - valid gitlab token → True
# - mismatched gitlab token → False
# - compute_pr_hash_key returns consistent 64-char hex string
# - compute_pr_hash_key with different inputs produces different hashes
```

**`tests/unit/test_service.py`**
```python
# Use pytest-mock to mock: TenantRepository, InboundEventRepository, Redis, BasePublisher
# Test each step:
# - tenant not found → NotFoundError raised
# - tenant inactive → BusinessRuleError raised
# - event type not in whitelist → WebhookIgnored returned
# - invalid signature → ValidationError raised
# - invalid payload → ValidationError raised
# - Redis cache hit (duplicate) → WebhookDuplicate returned, no DB call
# - DB hit (duplicate) → WebhookDuplicate returned, Redis write-back called
# - happy path → WebhookAccepted returned, DB insert called, journey events published
# - DB insert fails → DatabaseError raised, no outbox event
```

**`tests/unit/test_models.py`**
```python
# - GitHub payload parses correctly from sample JSON fixture
# - GitLab payload parses correctly from sample JSON fixture
# - PullRequestData fields correctly extracted from parsed payloads
# - WebhookReceivedMessage serializes to dict correctly
# - JourneyStepEvent serializes to dict correctly
```

### 10b — Integration tests

**`tests/integration/test_webhook_flow.py`**
```python
# Using testcontainers (PostgreSQL + Redis + RabbitMQ):
# 1. Seed tenant row in DB with known webhook_secret
# 2. Seed vcs_event_whitelist rows
# 3. POST /webhooks/github/{tenant_id} with valid signature + valid PR payload
# 4. Assert response: 202 Accepted with correlation_id, event_id, outbox_id
# 5. Assert inbound_event row created in DB
# 6. Assert outbox_event row created in DB with status=SCHEDULED, dispatch_at ~30s in future
```

**`tests/integration/test_idempotency.py`**
```python
# 1. Seed tenant
# 2. POST same webhook twice (identical payload + signature)
# 3. First → 202 Accepted
# 4. Second → 200 {"status": "ignored", "code": "DUPLICATE_WEBHOOK", "detection_method": "db_lookup"}
#    (Redis not warmed up in test, so should hit DB)
# 5. Assert only 1 inbound_event row in DB
```

**`tests/integration/test_outbox_dispatcher.py`**
```python
# 1. Insert an outbox_event row with dispatch_at = NOW() (immediate)
# 2. Run OutboxPublisher._dispatch_batch() directly
# 3. Assert row status updated to DISPATCHED
# 4. Assert message received on RabbitMQ test queue
```

### Run all tests
```bash
uv run pytest tests/unit -m unit -v --cov=vcs_gateway --cov-report=term-missing
uv run pytest tests/integration -m integration -v
```

---

## Final Verification (End-to-End)

```bash
# Start everything
docker compose up -d
uv run alembic upgrade head

# Start the service
uv run uvicorn vcs_gateway.main:app --reload --port 8001

# Health checks
curl http://localhost:8001/health/live
curl http://localhost:8001/health/ready

# Send a test webhook (get tenant_id from DB first)
curl -X POST http://localhost:8001/webhooks/github/<tenant_id> \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: pull_request" \
  -H "X-Hub-Signature-256: sha256=<computed_hmac>" \
  -d '{
    "action": "opened",
    "pull_request": {
      "number": 42,
      "title": "Add feature",
      "user": {"login": "john"},
      "html_url": "https://github.com/org/repo/pull/42",
      "head": {"sha": "abc123def456"}
    },
    "repository": {
      "id": 987654321,
      "full_name": "org/repo",
      "html_url": "https://github.com/org/repo"
    }
  }'
# Expected: HTTP 202 with {status: "accepted", correlation_id: ..., dispatch_in: "30s"}
```

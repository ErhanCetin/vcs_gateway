# VCS Gateway Service Documentation

> **Version:** 1.1.0
> **Last Updated:** 2025-12-31
> **Owner:** Platform Engineering Team
> **Status:** Ready for Implementation

---

## Table of Contents

1. [Service Overview](#1-service-overview)
2. [Responsibilities](#2-responsibilities)
3. [Architecture](#3-architecture)
4. [Data Flow](#4-data-flow)
5. [API Specification](#5-api-specification)
6. [Database Schema](#6-database-schema)
7. [Queue Integration](#7-queue-integration)
8. [External Dependencies](#8-external-dependencies)
9. [Configuration](#9-configuration)
10. [Error Handling](#10-error-handling)
11. [Monitoring & Observability](#11-monitoring--observability)
12. [Performance Considerations](#12-performance-considerations)
13. [Security](#13-security)
14. [Testing Strategy](#14-testing-strategy)
15. [Deployment](#15-deployment)
16. [Runbook](#16-runbook)

---

## 1. Service Overview

### Purpose

The **VCS Gateway Service** is the primary entry point for all version control system (VCS) webhook events. It validates incoming pull request webhooks from GitHub, GitLab, and Bitbucket, performs critical filtering and validation, and routes events to downstream analysis services.

This service acts as a **defensive layer** protecting the system from:
- Invalid or malicious webhook payloads
- Duplicate PR analysis requests
- Quota-exceeded tenants
- Rate limit abuse

### Business Value

- ✅ **Ensures 99.9% webhook ingestion uptime** - Critical for real-time PR analysis
- ✅ **Reduces duplicate processing by 95%** - PR hash deduplication prevents wasted LLM costs
- ✅ **Protects quota limits** - Pre-flight quota checks prevent cost overruns
- ✅ **Enables multi-VCS support** - Unified interface for GitHub, GitLab, Bitbucket
- ✅ **Provides audit trail** - Every webhook tracked in pr_journey for debugging

### Key Characteristics

- **Type:** API Gateway Service (Webhook Ingestion)
- **Language/Framework:** Python 3.11+ / FastAPI 0.104+
- **Communication Pattern:** Synchronous REST (inbound) + Asynchronous Events (outbound)
- **Scaling Strategy:** Horizontal auto-scaling (2-20 pods based on CPU)
- **Stateful/Stateless:** Stateless (all state in PostgreSQL + Redis)

---

## 2. Responsibilities

### Primary Responsibilities

1. **Tenant Validation from URL Path (MVP Critical)**
   - Extract `tenant_id` from URL path: `/webhooks/github/{tenant_id}`
   - Verify tenant exists and is active in database
   - Reject requests with invalid or inactive tenant_id

2. **Event Type Filtering (MVP Critical - Security)**
   - Filter webhook events to **only allow PR-related events**
   - GitHub: Only `pull_request` events with actions: `opened`, `synchronize`, `reopened`
   - GitLab: Only `Merge Request Hook` events with actions: `open`, `update`, `reopen`
   - ⚠️ **Critical:** Prevents system overload if customer misconfigures webhook to send all events

3. **Webhook Signature Validation**
   - Verify HMAC-SHA256 signatures from GitHub (`X-Hub-Signature-256`)
   - Verify GitLab tokens (`X-Gitlab-Token`)
   - Verify Bitbucket signatures (`X-Hub-Signature`)
   - Use tenant-specific webhook secrets from database
   - Reject unauthenticated webhook requests

4. **PR Hash Deduplication & Idempotency (MVP Critical)**
   - Calculate `pr_hash_key` using SHA256: `vcs_provider:tenant_id:repo_id:pr_id:vcs_instance_id:action:commit_sha`
   - Check internal idempotency using Cache-Aside pattern:
     - **Step 1:** Check Redis cache (`idempotency:pr_hash:{pr_hash_key}`)
     - **Step 2:** On cache miss, query `vcs_gateway_schema.inbound_event` table
     - **Step 3:** Write-back to Redis cache (TTL: 72 hours)
   - Perform both checks:
     - **Idempotency:** Exact duplicate webhook (same `pr_hash_key`)
     - **Stale Detection:** Same PR + commit_sha already processed
   - Return early with 200 OK if duplicate (prevents wasted LLM calls)
   - **Benefit:** Industry-standard idempotency pattern prevents duplicate processing

5. **Event Persistence with Idempotency (MVP Critical)**
   - Save `inbound_event` to database with unique `correlation_id`
   - Use Outbox Pattern for transactional event publishing
   - Guarantee at-least-once delivery to downstream services

6. **PR Journey Tracking (Event-Based - NEW)**
   - Publish `journey.step.created` events to RabbitMQ
   - Journey Service consumes events and manages journey state
   - Enable end-to-end request tracking and debugging
   - **Benefit:** Loose coupling, VCS Gateway doesn't need journey DB access

7. **Event Publishing to Queue**
   - Publish to `vcs.webhook.received` queue after DB persistence
   - Message includes: `correlation_id`, `tenant_id`, `vcs_provider`, `repo_id`, `pr_id`, `commit_sha`, raw payload
   - VCS Worker Service consumes and performs async processing (see vcs_worker_service.md)

### What This Service Does NOT Do

- ❌ **Does NOT perform auto-onboarding** (handled by VCS Worker Service)
- ❌ **Does NOT perform quota checks** (handled by VCS Worker Service → Policy Engine)
- ❌ **Does NOT normalize VCS payloads** (handled by VCS Worker Service)
- ❌ **Does NOT perform LLM calls or AI analysis** (handled by LLM Service)
- ❌ **Does NOT access developer or project master data** (handled by Response Processor)
- ❌ **Does NOT make decisions about analysis type** (handled by Orchestrator)
- ❌ **Does NOT send notifications or delivery messages** (handled by Delivery Service)
- ❌ **Does NOT store quota data** (handled by Policy Engine Service)

### Service Boundaries

```
┌─────────────────────────────────────────────────────────────┐
│          VCS GATEWAY SERVICE                                │
├─────────────────────────────────────────────────────────────┤
│ Upstream (Inbound):                                         │
│ - GitHub Webhooks: POST /webhooks/github/{tenant_id}        │
│ - GitLab Webhooks: POST /webhooks/gitlab/{tenant_id}        │
│ - Bitbucket Webhooks: POST /webhooks/bitbucket/{tenant_id}  │
│                                                             │
│ Downstream (Outbound):                                      │
│ - Policy Engine Service: POST /quota/check (sync API call) │
│ - Message Queue: repo.change.detected (async)              │
│ - Journey Service: journey.events exchange (fire-and-forget)│
│                                                             │
│ Database (Owned Tables):                                    │
│ - vcs_gateway_schema.inbound_event                          │
│ - vcs_gateway_schema.outbox_event                           │
│                                                             │
│ Event Publishing (RabbitMQ):                                │
│ - journey.events exchange (journey.step.created events)     │
│ - repo.change.detected (webhook events to downstream)       │
│                                                             │
│ Database (Read-Only Access):                                │
│ - shared_schema.tenant (verify tenant_id from URL)          │
│ - processor_schema.pr_analysis (deduplication)              │
│                                                             │
│ Database (Read-Write Access):                               │
│ - shared_schema.tenant_vcs_config (auto-onboard repos)      │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Architecture

### High-Level Architecture Diagram (with Fire-and-Forget Pattern)

```
┌──────────────────────────────────────────────────────┐
│   External VCS Providers                             │
│   - GitHub                                           │
│   - GitLab                                           │
│   - Bitbucket                                        │
└───────────────────┬──────────────────────────────────┘
                    │ HTTPS Webhooks
                    ▼
        ┌────────────────────────────────────────┐
        │   Kong API Gateway                     │
        │  - Rate Limiting                       │
        │  - SSL Termination                     │
        │  - IP Whitelisting (Enterprise)        │
        └───────────┬────────────────────────────┘
                    │
                    ▼
┌────────────────────────────────────────────────────────────────┐
│     VCS GATEWAY SERVICE (Webhook Handler - Fast Response)      │
│                                                                │
│  ┌──────────────────────────────────────────────┐            │
│  │   API Layer (FastAPI)                        │            │
│  │  - POST /webhooks/github/{tenant_id}         │            │
│  │  - POST /webhooks/gitlab/{tenant_id}         │            │
│  │  - POST /webhooks/bitbucket/{tenant_id}      │            │
│  │  - GET /health/live                          │            │
│  │  - GET /health/ready                         │            │
│  └──────────────┬───────────────────────────────┘            │
│                 │                                             │
│  ┌──────────────▼───────────────────────────────┐            │
│  │   Business Logic Layer (Sync - FAST)         │            │
│  │  - Tenant Validation (Redis Cache-Aside)     │ ⬅️ NEW    │
│  │  - Journey Tracking Initialization           │            │
│  │  - Webhook Signature Verification            │            │
│  │  - Event Type Filtering (PR events only)     │            │
│  │  - Payload Schema Validation (Pydantic)      │            │
│  │  - Idempotency Check (Cache-Aside Pattern)   │ ⬅️ NEW    │
│  │  - DB Persistence (inbound_event)            │ ⬅️ NEW    │
│  │  - Publish to RabbitMQ Queue                 │ ⬅️ NEW    │
│  │  - Fast Response (202 Accepted - 35-50ms)    │ ⬅️ NEW    │
│  └──────────────┬───────────────────────────────┘            │
└─────────────────┼────────────────────────────────────────────┘
                  │
         ┌────────┴────────────┐
         ▼                     ▼
  ┌──────────────┐      ┌─────────────────────────────┐
  │  PostgreSQL  │      │  Redis (Cache Only)    ⬅️ NEW│
  │  (Shared DB) │      │                              │
  │              │      │  Use Cases:                  │
  │  Schemas:    │      │  - Tenant Config Cache       │
  │  - shared    │      │  - Idempotency Cache (72h)   │
  │  - vcs_gw    │      │                              │
  │              │      │  NOT USED FOR:               │
  │  Tables:     │      │  - Queueing (RabbitMQ)       │
  │  - tenant    │      │  - Worker coordination       │
  │  - journey   │      │                              │
  └──────────────┘      │  Pattern: Cache-Aside        │
                        │  Performance: 95% hit rate   │
                        └──────────────┬───────────────┘
                                       │
                  ┌────────────────────┴────────────────┐
                  ▼                                     ▼
         ┌─────────────────┐                  ┌─────────────────────┐
         │   RabbitMQ      │                  │  VCS Worker Service │
         │                 │                  │  (Separate Service) │
         │  Queues:        │                  │                     │
         │  - vcs.webhook  │───────consume────▶│  - Quota checks    │
         │    .received    │                  │  - Repo onboarding  │
         │                 │                  │  - Normalization    │
         │  - vcs.rejection│◀───────produce───│  - Downstream pub   │
         │                 │                  │                     │
         └─────────────────┘                  │  See: vcs_worker_   │
                                              │  service.md         │
                                              └─────────────────────┘

Key Architecture Changes:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Redis: → Cache ONLY (tenant config + idempotency, NO queueing)
2. Split: VCS Gateway (webhook acceptance) + VCS Worker (async processing)
3. Cache-Aside Pattern: Redis → DB lookup with write-back
4. Queue-Based: RabbitMQ for reliable message delivery to VCS Worker
5. Fast Response: 202 Accepted in 35-50ms (vs 250-350ms)
6. No VCS Timeout: Worker service eliminates timeout risk
7. Stateless Services: Both VCS Gateway and Worker scale independently
```

### Technology Stack

| Component | Technology | Version | Justification |
|-----------|-----------|---------|---------------|
| **Runtime** | Python | 3.11+ | Team expertise, async support, rich libraries |
| **Web Framework** | FastAPI | 0.104+ | Native async, auto OpenAPI docs, high performance |
| **ASGI Server** | Uvicorn | 0.24+ | Production ASGI server for FastAPI |
| **Process Manager** | Gunicorn + Uvicorn Workers | 21.2+ | Multi-worker for production |
| **Database Driver** | asyncpg | 0.29+ | Fastest async PostgreSQL driver |
| **ORM** | SQLAlchemy | 2.0+ | Mature ORM with async support |
| **Migration Tool** | Alembic | 1.12+ | Database schema versioning |
| **Queue Client** | aio-pika | 9.3+ | Async RabbitMQ client |
| **HTTP Client** | httpx | 0.25+ | Async HTTP for Policy Engine Service calls |
| **Validation** | Pydantic | 2.5+ | Request/response validation |
| **Logging** | structlog | 23.2+ | Structured JSON logging |
| **Monitoring** | OpenTelemetry | 1.21+ | Metrics, logs, traces |
| **Cache (MVP Critical)** | Redis | 7.0+ | Tenant cache + idempotency (NO queue) |
| **Redis Client** | redis-py | 5.0+ | Async Redis client (Cache-Aside pattern) |

### Design Patterns Used

1. **Cache-Aside Pattern (NEW - MVP Critical)**
   - Redis cache layer for tenant config and idempotency
   - Read: Check Redis → (miss) → Query DB → Write back to Redis
   - Write: Update DB → Invalidate/update Redis cache
   - **Benefit:** 95% cache hit rate, 10x faster lookups (2ms vs 20ms)

2. **Queue-Based Processing Pattern (NEW - MVP Critical)**
   - Webhook handler: Fast validation → Write to DB → Publish to Queue → Return 202 Accepted
   - VCS Worker Service: Consume from queue → Process async → Publish downstream
   - **Benefit:** 7x faster webhook response (35-50ms vs 250-350ms) + Decoupled processing

3. **Factory Pattern**
   - `WebhookNormalizerFactory` creates VCS-specific normalizers
   - Easy to add new VCS providers (e.g., Azure DevOps)

4. **Repository Pattern**
   - `InboundEventRepository`, `JourneyRepository`
   - Abstracts database operations for testability

5. **Outbox Pattern**
   - Transactional event publishing (DB write + queue publish atomic)
   - Guarantees at-least-once delivery

6. **Strategy Pattern**
   - `SignatureValidator` interface with GitHub/GitLab/Bitbucket implementations
   - Each VCS has different signature verification logic

7. **Circuit Breaker Pattern**
   - Policy Engine Service calls protected with circuit breaker
   - Fallback: Accept event, log warning (process later)

8. **Message Queue Pattern (RabbitMQ)**
   - Producer: VCS Gateway (publishes to `vcs.webhook.received` queue after DB insert)
   - Consumer: VCS Worker Service (separate stateless service)
   - **Benefit:** Decoupled async processing + Horizontal scaling of workers

---

## 4. Data Flow

### Request/Event Flow Diagram (with Redis Cache & Fire-and-Forget Pattern)

```
┌─────────────────┐
│  VCS Webhook    │
│  Arrives        │
└────────┬────────┘
         │
         ▼
┌──────────────────────────────────────────────────┐
│  STEP 1: Tenant Validation (Redis Cache First)  │ ⬅️ NEW: Redis cache
│  - Extract tenant_id from URL path              │
│  - Try Redis cache: GET tenant:config:{id}      │
│  - Cache Hit (2ms) or DB Query (20ms)           │
│  - IF NOT FOUND: Return 404 Not Found           │
│  - IF INACTIVE: Return 403 Forbidden            │
└────────┬─────────────────────────────────────────┘
         │ (tenant valid) - ~2-5ms
         ▼
┌──────────────────────────────────────┐
│  STEP 2: Initialize Journey Tracking│
│  ⚠️ CRITICAL - Track ALL webhooks    │
│  - Generate correlation_id (UUID v7) │
│  - Publish journey.step.created event│ ⬅️ NEW (Event-Based)
│  - Journey Service handles DB write  │
└────────┬─────────────────────────────┘
         │ ~2ms (async event publish)
         ▼
┌──────────────────────────────────────┐
│  STEP 3: Signature Validation        │
│  - Verify HMAC-SHA256 signature      │
│  - Use webhook_secret from cache     │
│  - IF INVALID:                       │
│    ├─ Journey step (signature_failed)│
│    ├─ Update journey (status='failed')│
│    └─ Return 401 Unauthorized        │
│  - IF VALID:                         │
│    └─ Journey step (signature_verified) ⬅️ NEW
└────────┬─────────────────────────────┘
         │ (signature valid) ~10ms
         ▼
┌──────────────────────────────────────┐
│  STEP 4: Event Type Filtering        │
│  - Only allow PR events (opened, etc.)│
│  - IF NOT ALLOWED:                   │
│    ├─ Journey step (event_rejected)  │
│    ├─ Update journey (status='ignored')│
│    └─ Return 200 OK (ignored)        │ ⬅️ CHANGED: 200 instead of 400
│  - IF ALLOWED:                       │
│    └─ Journey step (event_type_validated) ⬅️ NEW
└────────┬─────────────────────────────┘
         │ (event allowed) ~5ms
         ▼
┌──────────────────────────────────────┐
│  STEP 5: Payload Schema Validation   │
│  - Validate JSON schema (Pydantic)   │
│  - IF INVALID:                       │
│    ├─ Journey step (validation_failed)│
│    ├─ Update journey (status='failed')│
│    └─ Return 400 Bad Request         │
│  - IF VALID:                         │
│    └─ Journey step (payload_validated) ⬅️ NEW
└────────┬─────────────────────────────┘
         │ (payload valid) ~5ms
         ▼
┌──────────────────────────────────────────────────┐
│  STEP 5.5: Idempotency Check (Cache-Aside)     │ ⬅️ NEW STEP
│  - Generate pr_hash_key (SHA256)                │
│  - Redis: GET idempotency:{pr_hash_key}         │
│    ├─ Cache HIT:                                │
│    │  ├─ Journey step (duplicate_cache)         │
│    │  └─ Return 200 OK (duplicate)              │
│    └─ Cache MISS: Check DB                      │
│  - DB: SELECT WHERE pr_hash_key = ?             │
│    ├─ DB HIT (duplicate):                       │
│    │  ├─ Write-back to Redis (TTL 72h)          │
│    │  ├─ Journey step (duplicate_db)            │
│    │  └─ Return 200 OK (duplicate)              │
│    └─ DB MISS (new webhook):                    │
│       ├─ Redis: SETEX idempotency:{hash} 259200 │
│       ├─ Journey step (idempotency_checked) ⬅️ NEW
│       └─ Continue to STEP 6                     │
│  - Fallback: Both Redis & DB DOWN → Continue    │
└────────┬─────────────────────────────────────────┘
         │ (not duplicate) ~2-20ms (cache hit/miss)
         ▼
┌──────────────────────────────────────────────────────────────┐
│  STEP 6: Transactional Write (Outbox Pattern)              │
│  - BEGIN TRANSACTION:                                       │
│    1. INSERT vcs_gateway_schema.inbound_event               │
│       - processing_status = 'pending'                       │
│       - raw_payload (JSONB)                                 │
│       - webhook_headers (JSONB)                             │
│    2. INSERT vcs_gateway_schema.outbox_event                │
│       - status = 'pending'                                  │
│       - dispatch_at = NOW() + 30 seconds  ⬅️ DEBOUNCE      │
│       - event_type = 'vcs.webhook.received'                 │
│       - payload (contains correlation_id, pr_hash_key, etc) │
│    3. Journey step (event_prepared)                         │
│  - COMMIT TRANSACTION                                       │
│  - IF DB DOWN:                                              │
│    ├─ Journey step (persistence_failed)                     │
│    └─ Return 500 Internal Server Error + Retry-After: 60   │
│  - Journey step (outbox_event_scheduled) ⬅️ NEW            │
│    → metadata: {outbox_id, dispatch_at, pr_version}         │
└────────┬─────────────────────────────────────────────────────┘
         │ ~10-20ms (transactional DB write)
         ▼
┌──────────────────────────────────────┐
│  STEP 7: Response (Fast Return)      │
│  - Return 202 Accepted               │
│  - {                                 │
│      "status": "accepted",           │
│      "correlation_id": "uuid",       │
│      "event_id": "uuid",             │
│      "message": "Webhook scheduled", │
│      "dispatch_in": "30s"            │
│    }                                 │
│  - Total response time: 35-50ms      │ ⬅️ 7x faster than 250-350ms
└──────────────────────────────────────┘

         ⏸️  HTTP Response sent to VCS provider

         ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
         ⏳ 30-Second Debounce Window
         (Outbox Dispatcher checks every 1 second)
         ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

┌──────────────────────────────────────────────────────────────┐
│  OUTBOX DISPATCHER (Background Worker)                      │
│  - Runs every 1 second                                      │
│  - Supports multiple worker instances (HA)                  │
│  - SELECT * FROM outbox_event                               │
│    WHERE status = 'pending'                                 │
│    AND dispatch_at <= NOW()                                 │
│    ORDER BY dispatch_at ASC                                 │
│    LIMIT 100                                                │
│    FOR UPDATE SKIP LOCKED;  ⬅️ Prevents race conditions    │
│                                                             │
│  ┌─────────────────────────────────────────────────┐       │
│  │ DEBOUNCE CHECK: Verify PR State Before Dispatch│       │
│  │ - Redis: GET pr_state:{pr_hash_key}             │       │
│  │ - IF pr_state = 'closed' OR 'merged':           │       │
│  │   ├─ UPDATE outbox_event SET status='cancelled' │       │
│  │   ├─ Journey step (outbox_event_cancelled)      │       │
│  │   └─ Skip dispatch (PR no longer active)        │       │
│  │ - IF newer PR version exists:                   │       │
│  │   ├─ UPDATE outbox_event SET status='superseded'│       │
│  │   ├─ Journey step (outbox_event_cancelled)      │       │
│  │   └─ Skip dispatch (newer version available)    │       │
│  └─────────────────────────────────────────────────┘       │
│                                                             │
│  ┌─────────────────────────────────────────────────┐       │
│  │ DISPATCH: Publish to RabbitMQ                   │       │
│  │ - Publish to vcs.webhook.received               │       │
│  │ - Message: {correlation_id, event_id,           │       │
│  │            tenant_id, repo_id, pr_id, etc}      │       │
│  │ - UPDATE outbox_event:                          │       │
│  │   SET status='dispatched', published_at=NOW()   │       │
│  │ - Journey step (outbox_event_dispatched)        │       │
│  │   → metadata: {outbox_id, event_type,           │       │
│  │                debounce_duration_ms, pr_version}│       │
│  └─────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────────┘

         ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
         Asynchronous Processing by VCS Worker Service
         (See vcs_worker_service.md for details)
         ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

┌──────────────────────────────────────┐
│  VCS Worker Service                  │
│  - Consumes from vcs.webhook.received│
│  - Processes webhook asynchronously  │
│  - See vcs_worker_service.md for     │
│    complete processing flow          │
└──────────────────────────────────────┘
```

---

## 📊 Performance Impact - Before vs After (Queue-Based Architecture)

| Metric | Before (Sync) | After (Queue + Worker) | Improvement |
|--------|---------------|------------------------|-------------|
| **Webhook Response Time** | 250-350ms | 35-50ms (cache hit) / 50-70ms (cache miss) | **7x faster** |
| **Tenant Config Lookup** | 20ms (DB) | 2ms (Redis cache hit) / 20ms (cache miss + write-back) | **10x faster (95% hit rate)** |
| **Duplicate Detection** | 50ms (DB query) | 2ms (Redis hit) / 20ms (DB lookup + write-back) | **25x faster (95% hit rate)** |
| **VCS Timeout Risk** | High (if > 10s) | None | **Eliminated** |
| **DB Load (reads)** | 100% DB queries | 5% DB queries (95% cache hit) | **20x reduction** |
| **Cache Write-Back** | N/A | Auto-populate Redis on DB hit | Self-healing cache |
| **Worker Processing** | Blocked webhook response | Handled by VCS Worker Service | Decoupled |

**Cache-Aside Performance:**
- **Cache Hit (95%)**: Redis only (2ms)
- **Cache Miss (5%)**: Redis + DB + Write-back (20ms)
- **Average Response**: (0.95 × 2ms) + (0.05 × 20ms) = **2.9ms** per cache lookup

---

## 🎯 HTTP Response Code Matrix

| STEP | Scenario | HTTP Code | Response Body | VCS Retry? |
|------|----------|-----------|---------------|------------|
| **STEP 1** | Tenant NOT FOUND | `404 Not Found` | `{"code": "TENANT_NOT_FOUND", "tenant_id": "..."}` | ❌ No |
| **STEP 1** | Tenant INACTIVE | `403 Forbidden` | `{"code": "TENANT_INACTIVE", "message": "Tenant suspended"}` | ❌ No |
| **STEP 2** | Journey init FAILED | `500 Internal Server Error` | `{"code": "JOURNEY_INIT_FAILED"}` | ✅ Yes |
| **STEP 3** | Signature INVALID | `401 Unauthorized` | `{"code": "INVALID_SIGNATURE"}` | ❌ No |
| **STEP 4** | Event type NOT ALLOWED | `200 OK` | `{"status": "ignored", "code": "EVENT_TYPE_NOT_ALLOWED", "event_type": "push"}` | ❌ No |
| **STEP 5** | Payload INVALID | `400 Bad Request` | `{"code": "INVALID_PAYLOAD", "errors": [...]}` | ❌ No |
| **STEP 5.5** | Duplicate webhook (Redis HIT) | `200 OK` | `{"status": "ignored", "code": "DUPLICATE_WEBHOOK", "correlation_id": "..."}` | ❌ No |
| **STEP 5.5** | Redis DOWN | Continue (log warning) | N/A (fallback to DB idempotency) | N/A |
| **STEP 6** | Database write FAILED | `500 Internal Server Error` | `{"code": "PERSISTENCE_FAILED", "retry_after": 60}` + Header: `Retry-After: 60` | ✅ Yes (after 1 min) |
| **STEP 7** | Queue publish FAILED | `500 Internal Server Error` | `{"code": "QUEUE_PUBLISH_FAILED", "retry_after": 60}` | ✅ Yes (after 1 min) |
| **STEP 8** | SUCCESS | `202 Accepted` | `{"status": "accepted", "correlation_id": "...", "event_id": "...", "message": "Webhook accepted and queued"}` | ❌ No |
| **VCS Worker** | Quota rejected (async) | N/A (already sent 202) | Worker sends rejection to vcs.rejection queue | N/A |
| **VCS Worker** | Processing failed | N/A (already sent 202) | Worker retries with exponential backoff, then DLQ | N/A |

---

## 🔴 Redis Cache Keys & TTLs (Cache-Aside Pattern)

| Cache Type | Key Pattern | Data Structure | TTL | Cache Pattern | Eviction Policy |
|------------|-------------|----------------|-----|---------------|-----------------|
| **Tenant Config** | `tenant:config:{tenant_id}` | String (JSON) | 300s (5 min) | Cache-Aside (Redis → DB) | Pub/sub invalidation + TTL |
| **Idempotency** | `idempotency:{pr_hash_key}` | String (JSON) | 259200s (72h) | Cache-Aside (Redis → DB → Write-back) | Expire after TTL |
| **PR Version** | `pr:version:{tenant_id}:{repo_id}:{pr_id}` | Integer | 2592000s (30 days) | Atomic increment (INCR) | Expire after TTL |

**Cache-Aside Pattern Behavior:**
- **Tenant Config**: Redis cache miss → DB lookup → Write to Redis (TTL 5 min)
- **Idempotency**: Redis cache miss → DB lookup → If found: Write-back to Redis (TTL 72h)
- **PR Version**: Atomic increment per PR, generates monotonic version numbers
- **Performance**: 95% cache hit rate (2ms), 5% cache miss (20ms DB lookup)

**IMPORTANT:** Redis is NOT used for webhook queue. Webhooks are persisted to PostgreSQL (`inbound_event` table) and published to RabbitMQ queue (`vcs.webhook.received`) for processing by VCS Worker Service.

---

## ✅ Key Benefits of New Flow:

1. ✅ **Webhook Timeout Eliminated:** 35-50ms response (vs 250-350ms) - 7x faster
2. ✅ **VCS Provider Happy:** No more timeout errors, no retry storms
3. ✅ **Cache-Aside Pattern:** Redis → DB lookup with write-back (industry standard)
4. ✅ **Tenant Config Cached:** 95% cache hit rate, 10x faster lookups (2ms vs 20ms)
5. ✅ **Idempotency Bulletproof:** Two-level check (Redis fast path + DB fallback)
6. ✅ **Duplicate Detection:** 2ms Redis cache hit, 20ms DB lookup on cache miss
7. ✅ **DB Load Reduced:** 95% fewer queries (both tenant config & idempotency)
8. ✅ **Decoupled Architecture:** VCS Worker Service handles all async processing
9. ✅ **Graceful Degradation:** Redis down → DB fallback (slower but works)
10. ✅ **All Webhooks Tracked:** Journey tracking at STEP 2 (before queue publish)
11. ✅ **Write-Back Cache:** DB hits populate Redis for next duplicate webhook
12. ✅ **Horizontal Scaling:** VCS Worker Service scales independently based on queue depth
13. ✅ **Queue-Based Reliability:** RabbitMQ ensures message durability and retry mechanisms

---

## ⚠️ Important Notes:

### Cache-Aside Pattern Implementation
- **Read Path**: Redis GET → (cache miss) → DB SELECT → Redis SETEX (write-back)
- **Write Path**: On new webhook, write to Redis immediately (TTL 72h for idempotency, 5min for tenant config)
- **Invalidation**: Pub/sub for tenant config updates, TTL expiration for idempotency
- **Consistency**: DB is source of truth, Redis is performance layer

### Redis Idempotency TTL (72 hours)
- After 72h TTL expires, duplicate webhook triggers Cache-Aside pattern:
  1. Redis cache miss
  2. DB lookup (finds existing event)
  3. Write-back to Redis (refresh 72h TTL)
  4. Return 200 OK (duplicate detected)
- **DB unique constraint** still prevents duplicate processing if both Redis & DB checks fail
- Acceptable trade-off: Redis = fast cache, DB = source of truth

### VCS Worker Service Responsibilities
- **Asynchronous Processing**: VCS Worker Service consumes from `vcs.webhook.received` queue
- **Business Logic**: Quota checks, repo onboarding, normalization handled by VCS Worker
- **Failure Handling**: VCS Worker handles retries with exponential backoff, sends to DLQ after max retries
- **Rejection Notifications**: Business errors (quota exceeded, etc.) sent back to VCS Gateway via `vcs.rejection` queue
- **See**: [vcs_worker_service.md](./vcs_worker_service.md) for complete worker architecture

### Redis Unavailable Scenarios
1. **Idempotency Check**: Skip Redis, query DB directly (20ms instead of 2ms) - still accurate
2. **Both Redis & DB Down**: Accept webhook, log error, continue processing (risk duplicate, DB unique constraint as last defense)
```

### Detailed Process Flow

#### Operation: Process GitHub Webhook

**Trigger:** GitHub sends webhook to `POST /webhooks/github`

**Official Documentation:** [GitHub Pull Request Webhook Events](https://docs.github.com/en/webhooks/webhook-events-and-payloads#pull_request)

**Input (Official GitHub Payload Example):**
```json
{
  "action": "opened",
  "number": 2,
  "pull_request": {
    "url": "https://api.github.com/repos/octocat/Hello-World/pulls/2",
    "id": 279147437,
    "title": "Update the README with new information",
    "user": {
      "login": "octocat",
      "id": 1,
      "avatar_url": "https://github.com/images/error/octocat_happy.gif"
    },
    "body": "This is a pretty simple change that we need to pull into master.",
    "created_at": "2019-05-15T15:20:30Z",
    "updated_at": "2019-05-15T15:20:30Z",
    "head": {
      "ref": "feature-branch",
      "sha": "c5b5b0..."
    },
    "base": {
      "ref": "main",
      "sha": "a10867..."
    },
    "mergeable": true,
    "html_url": "https://github.com/octocat/Hello-World/pull/2"
  },
  "repository": {
    "id": 1296269,
    "name": "Hello-World",
    "full_name": "octocat/Hello-World",
    "html_url": "https://github.com/octocat/Hello-World"
  },
  "sender": {
    "login": "octocat",
    "id": 1,
    "avatar_url": "https://github.com/images/error/octocat_happy.gif"
  }
}
```

**Steps:**

**1. Tenant Validation with Redis Cache (CRITICAL - Must be First)**

```python
async def process_github_webhook(request: Request, tenant_id: str, payload: dict):
    # ✅ STEP 1: Verify tenant_id from URL path (Redis Cache First)
    # Try Redis cache first for fast tenant validation (2ms vs 20ms DB query)

    tenant = None
    cache_key = f"tenant:config:{tenant_id}"

    try:
        # Try Redis cache
        cached_tenant = await redis_client.get(cache_key)
        if cached_tenant:
            tenant = json.loads(cached_tenant)
            logger.debug(f"Tenant config cache HIT for {tenant_id}")
    except (RedisError, ConnectionError) as e:
        # Redis unavailable, fallback to DB
        logger.warning(f"Redis cache unavailable, falling back to DB: {e}")

    # Cache miss or Redis down - query DB
    if not tenant:
        tenant = await db.execute(
            """
            SELECT tenant_id, is_active, webhook_secret, vcs_provider, repo_id, repo_name
            FROM shared_schema.tenant t
            LEFT JOIN shared_schema.tenant_vcs_config vc ON t.tenant_id = vc.tenant_id
            WHERE t.tenant_id = :tenant_id
            """,
            {"tenant_id": tenant_id}
        ).fetchone()

        if tenant:
            # Populate Redis cache for next request (best effort - ignore failures)
            try:
                await redis_client.setex(
                    cache_key,
                    300,  # 5 min TTL
                    json.dumps({
                        "tenant_id": tenant["tenant_id"],
                        "is_active": tenant["is_active"],
                        "webhook_secret": tenant["webhook_secret"],
                        "vcs_provider": tenant.get("vcs_provider"),
                        "repo_id": tenant.get("repo_id"),
                        "repo_name": tenant.get("repo_name")
                    })
                )
                logger.debug(f"Tenant config cached for {tenant_id}")
            except RedisError:
                pass  # Ignore cache write failures

    # Tenant validation
    if not tenant:
        raise HTTPException(
            status_code=404,
            detail={"code": "TENANT_NOT_FOUND", "tenant_id": tenant_id}
        )

    if not tenant.get("is_active"):
        raise HTTPException(
            status_code=403,
            detail={"code": "TENANT_INACTIVE", "message": "Tenant account is suspended"}
        )

    # ✅ STEP 2: Journey Initialization (RIGHT AFTER tenant validation)
    # ⚠️ CRITICAL: Initialize journey BEFORE any validation that might reject webhook
    # This ensures we track ALL webhooks (accepted AND rejected) for monitoring/debugging
    correlation_id = uuid.uuid7()

    # Extract basic PR info from payload (even if validation fails later)
    pr_id = str(payload.get("pull_request", {}).get("number", "unknown"))
    repo_id = str(payload.get("repository", {}).get("id", "unknown"))

    # Publish journey step event (Journey Service will auto-create journey on first step)
    await event_publisher.publish(
        exchange="journey.events",
        routing_key="journey.step.created",
        message={
            "correlation_id": str(correlation_id),
            "service_name": "vcs-gateway",
            "step_type": "webhook_received",
            "status": "completed",
            "tenant_id": str(tenant_id),
            "pr_id": pr_id,
            "repo_id": repo_id,
            "metadata": {"vcs": "github", "repo_id": repo_id, "pr_id": pr_id},
            "timestamp": datetime.utcnow().isoformat()
        }
    )

    # ✅ STEP 3: Verify webhook signature (tenant-specific secret)
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_github_signature(payload, signature, tenant.webhook_secret):
        # Publish journey step event: signature validation failed
        await event_publisher.publish(
            exchange="journey.events",
            routing_key="journey.step.created",
            message={
                "correlation_id": str(correlation_id),
                "service_name": "vcs-gateway",
                "step_type": "signature_failed",
                "status": "failed",
                "tenant_id": str(tenant_id),
                "pr_id": pr_id,
                "repo_id": repo_id,
                "metadata": {"reason": "invalid_signature"},
                "timestamp": datetime.utcnow().isoformat()
            }
        )

        raise HTTPException(status_code=401, detail="Invalid signature")

    # Signature verified successfully - publish journey event
    await event_publisher.publish(
        exchange="journey.events",
        routing_key="journey.step.created",
        message={
            "correlation_id": str(correlation_id),
            "service_name": "vcs-gateway",
            "step_type": "signature_verified",
            "status": "completed",
            "tenant_id": str(tenant_id),
            "pr_id": pr_id,
            "repo_id": repo_id,
            "metadata": {},
            "timestamp": datetime.utcnow().isoformat()
        }
    )

    # ✅ STEP 4: Event Type Filtering (Critical - prevents system overload)
    event_type = request.headers.get("X-GitHub-Event")
    action = payload.get("action")

    # Check if event is allowed (fetch from database whitelist)
    is_allowed = await is_event_allowed(
        vcs_provider="github",
        event_type=event_type,
        action=action
    )

    if not is_allowed:
        # Get allowed events for error response
        allowed_events = await get_allowed_events(vcs_provider="github")

        # Publish journey step event: event type not allowed
        await event_publisher.publish(
            exchange="journey.events",
            routing_key="journey.step.created",
            message={
                "correlation_id": str(correlation_id),
                "service_name": "vcs-gateway",
                "step_type": "event_rejected",
                "status": "completed",
                "tenant_id": str(tenant_id),
                "pr_id": pr_id,
                "repo_id": repo_id,
                "metadata": {
                    "received_event": event_type,
                    "received_action": action,
                    "allowed_events": allowed_events
                },
                "timestamp": datetime.utcnow().isoformat()
            }
        )

        # Return 200 OK (not 400) - webhook successfully received but ignored
        # VCS provider considers this success and won't retry
        return JSONResponse(
            status_code=200,
            content={
                "status": "ignored",
                "code": "EVENT_TYPE_NOT_ALLOWED",
                "message": "Event type not supported - only whitelisted PR events allowed",
                "event_type": event_type,
                "action": action,
                "allowed_events": allowed_events,
                "correlation_id": str(correlation_id)
            }
        )

    # Event type allowed - publish journey event
    await event_publisher.publish(
        exchange="journey.events",
        routing_key="journey.step.created",
        message={
            "correlation_id": str(correlation_id),
            "service_name": "vcs-gateway",
            "step_type": "event_type_validated",
            "status": "completed",
            "tenant_id": str(tenant_id),
            "pr_id": pr_id,
            "repo_id": repo_id,
            "metadata": {"event_type": event_type, "action": action},
            "timestamp": datetime.utcnow().isoformat()
        }
    )

    # ✅ STEP 5: Validate payload schema
    try:
        webhook_data = GitHubWebhookSchema(**payload)
    except ValidationError as e:
        # Publish journey step event: payload validation failed
        await event_publisher.publish(
            exchange="journey.events",
            routing_key="journey.step.created",
            message={
                "correlation_id": str(correlation_id),
                "service_name": "vcs-gateway",
                "step_type": "validation_failed",
                "status": "failed",
                "tenant_id": str(tenant_id),
                "pr_id": pr_id,
                "repo_id": repo_id,
                "metadata": {"validation_error": str(e)},
                "timestamp": datetime.utcnow().isoformat()
            }
        )

        raise HTTPException(status_code=400, detail=str(e))

    # Payload validation succeeded - publish journey event
    await event_publisher.publish(
        exchange="journey.events",
        routing_key="journey.step.created",
        message={
            "correlation_id": str(correlation_id),
            "service_name": "vcs-gateway",
            "step_type": "payload_validated",
            "status": "completed",
            "tenant_id": str(tenant_id),
            "pr_id": pr_id,
            "repo_id": repo_id,
            "metadata": {},
            "timestamp": datetime.utcnow().isoformat()
        }
    )
```

---

**2. Redis Idempotency Check (NEW - Fast Duplicate Detection)**

```python
    # ✅ STEP 5.5: Redis Idempotency Check (NEW - Fast duplicate detection)
    # Generate pr_hash_key for idempotency
    # Format: vcs_provider:tenant_id:repo_id:pr_id:vcs_instance_id:action:commit_sha

    vcs_provider = "github"
    vcs_instance_id = "github.com"  # or custom for GitHub Enterprise

    raw_key = (
        f"{vcs_provider}:"
        f"{tenant_id}:"
        f"{webhook_data.repository.id}:"  # repo_id
        f"{webhook_data.pull_request.number}:"  # pr_id
        f"{vcs_instance_id}:"
        f"{webhook_data.action}:"  # action (opened, synchronize, etc.)
        f"{webhook_data.pull_request.head.sha}"  # commit_sha
    )

    pr_hash_key = hashlib.sha256(raw_key.encode()).hexdigest()

    # ✅ Cache-Aside Pattern: Redis → DB Lookup for Idempotency
    # Check Redis cache first (fast), then fallback to DB (slower but complete)
    idempotency_key = f"idempotency:{pr_hash_key}"
    is_duplicate = False
    detection_method = None

    # STEP 1: Check Redis cache (fast path - 2ms)
    try:
        cached = await redis_client.get(idempotency_key)
        if cached:
            # Duplicate detected via Redis cache (fast path)
            is_duplicate = True
            detection_method = "redis_cache"
            logger.info(f"Duplicate webhook detected via Redis cache: {pr_hash_key}")
    except (RedisError, ConnectionError) as e:
        # Redis unavailable, will check DB instead
        logger.warning(f"Redis idempotency check failed, falling back to DB: {e}")

    # STEP 2: If not in Redis cache, check DB (Cache-Aside pattern)
    if not is_duplicate:
        try:
            # Query DB to check if this pr_hash_key already processed
            existing_event = await db.execute(
                """
                SELECT event_id, correlation_id, processing_status, created_at
                FROM vcs_gateway_schema.inbound_event
                WHERE pr_hash_key = :pr_hash_key
                LIMIT 1
                """,
                {"pr_hash_key": pr_hash_key}
            ).fetchone()

            if existing_event:
                # Duplicate detected via DB (cache miss, but exists in DB)
                is_duplicate = True
                detection_method = "db_lookup"
                logger.info(f"Duplicate webhook detected via DB lookup: {pr_hash_key}")

                # Populate Redis cache for next request (Cache-Aside write-back)
                try:
                    await redis_client.setex(
                        idempotency_key,
                        259200,  # 72 hours TTL
                        "1"
                    )
                    logger.debug(f"Idempotency key cached (write-back): {pr_hash_key}")
                except RedisError:
                    pass  # Ignore cache write failures

        except Exception as db_error:
            # DB query failed - log error but continue processing
            # Better to risk duplicate than lose webhook
            logger.error(f"DB idempotency check failed: {db_error}")
            # Continue processing (is_duplicate remains False)

    # STEP 3: If duplicate detected (Redis or DB), return 200 OK
    if is_duplicate:
        # Publish journey step event: duplicate webhook detected
        # Use different step_type based on detection method
        step_type = "duplicate_cache" if detection_method == "redis_cache" else "duplicate_db"

        await event_publisher.publish(
            exchange="journey.events",
            routing_key="journey.step.created",
            message={
                "correlation_id": str(correlation_id),
                "service_name": "vcs-gateway",
                "step_type": step_type,
                "status": "completed",
                "tenant_id": str(tenant_id),
                "pr_hash_key": pr_hash_key,
                "pr_id": pr_id,
                "repo_id": repo_id,
                "metadata": {
                    "pr_hash_key": pr_hash_key,
                    "detection_method": detection_method
                },
                "timestamp": datetime.utcnow().isoformat()
            }
        )

        # Return 200 OK (duplicate successfully detected)
        return JSONResponse(
            status_code=200,
            content={
                "status": "ignored",
                "code": "DUPLICATE_WEBHOOK",
                "message": "Webhook already processed",
                "pr_hash_key": pr_hash_key,
                "correlation_id": str(correlation_id),
                "detection_method": detection_method
            }
        )

    # STEP 4: Not a duplicate - mark in Redis cache (Cache-Aside write)
    # This prevents next duplicate webhook from hitting DB
    try:
        await redis_client.setex(
            idempotency_key,
            259200,  # 72 hours = 3 days
            "1"  # Value doesn't matter, just presence
        )
        logger.debug(f"Idempotency key set in Redis: {pr_hash_key}")
    except RedisError:
        # Ignore cache write failures - DB unique constraint will handle duplicates
        pass

    # Publish journey event: new webhook (not duplicate)
    await event_publisher.publish(
        exchange="journey.events",
        routing_key="journey.step.created",
        message={
            "correlation_id": str(correlation_id),
            "service_name": "vcs-gateway",
            "step_type": "idempotency_checked",
            "status": "completed",
            "tenant_id": str(tenant_id),
            "pr_hash_key": pr_hash_key,
            "pr_id": pr_id,
            "repo_id": repo_id,
            "metadata": {"pr_hash_key": pr_hash_key},
            "timestamp": datetime.utcnow().isoformat()
        }
    )
```

---

**3. Durable Persistence - Write to Database (NEW - No Data Loss)**

```python
    # ✅ STEP 6: Transactional Write (Outbox Pattern with Debounce)
    # CRITICAL: Both inbound_event and outbox_event must be written atomically
    # Background Outbox Dispatcher will handle debounce and publish after 30s

    webhook_data = GitHubWebhookSchema(**payload)

    try:
        # BEGIN TRANSACTION: Atomic write to both tables
        async with db.begin():
            # 1. Insert into inbound_event table
            event_id = await db.execute(
                """
                INSERT INTO vcs_gateway_schema.inbound_event (
                    event_id,
                    correlation_id,
                    tenant_id,
                    vcs_provider,
                    vcs_instance_id,
                    repo_id,
                    repo_name,
                    pr_id,
                    pr_title,
                    pr_author,
                    pr_url,
                    commit_sha,
                    action,
                    pr_hash_key,
                    raw_payload,
                    webhook_headers,
                    processing_status,
                    created_at,
                    updated_at
                ) VALUES (
                    gen_random_uuid(),
                    :correlation_id,
                    :tenant_id,
                    :vcs_provider,
                    :vcs_instance_id,
                    :repo_id,
                    :repo_name,
                    :pr_id,
                    :pr_title,
                    :pr_author,
                    :pr_url,
                    :commit_sha,
                    :action,
                    :pr_hash_key,
                    :raw_payload,
                    :webhook_headers,
                    'pending',
                    NOW(),
                    NOW()
                ) RETURNING event_id
                """,
                {
                    "correlation_id": correlation_id,
                    "tenant_id": tenant_id,
                    "vcs_provider": vcs_provider,
                    "vcs_instance_id": vcs_instance_id,
                    "repo_id": str(webhook_data.repository.id),
                    "repo_name": webhook_data.repository.full_name,
                    "pr_id": str(webhook_data.pull_request.number),
                    "pr_title": webhook_data.pull_request.title,
                    "pr_author": webhook_data.pull_request.user.login,
                    "pr_url": webhook_data.pull_request.html_url,
                    "commit_sha": webhook_data.pull_request.head.sha,
                    "action": webhook_data.action,
                    "pr_hash_key": pr_hash_key,
                    "raw_payload": json.dumps(payload),
                    "webhook_headers": json.dumps({
                        "X-GitHub-Event": request.headers.get("X-GitHub-Event"),
                        "X-Hub-Signature-256": request.headers.get("X-Hub-Signature-256"),
                        "X-GitHub-Delivery": request.headers.get("X-GitHub-Delivery")
                    })
                }
            ).scalar()

            # 2. Insert into outbox_event table (30-second debounce)
            outbox_id = await db.execute(
                """
                INSERT INTO vcs_gateway_schema.outbox_event (
                    outbox_id,
                    correlation_id,
                    event_type,
                    payload,
                    status,
                    dispatch_at,
                    created_at
                ) VALUES (
                    gen_random_uuid(),
                    :correlation_id,
                    'vcs.webhook.received',
                    :payload,
                    'pending',
                    NOW() + INTERVAL '30 seconds',
                    NOW()
                ) RETURNING outbox_id
                """,
                {
                    "correlation_id": correlation_id,
                    "payload": json.dumps({
                        "correlation_id": str(correlation_id),
                        "event_id": str(event_id),
                        "tenant_id": str(tenant_id),
                        "vcs_provider": vcs_provider,
                        "repo_id": str(webhook_data.repository.id),
                        "repo_name": webhook_data.repository.full_name,
                        "pr_id": str(webhook_data.pull_request.number),
                        "pr_hash_key": pr_hash_key,
                        "raw_payload": payload
                    })
                }
            ).scalar()

        # COMMIT TRANSACTION (atomic write completed)

        logger.info(
            "webhook_persisted_with_outbox",
            correlation_id=correlation_id,
            event_id=event_id,
            outbox_id=outbox_id,
            pr_hash_key=pr_hash_key,
            tenant_id=tenant_id,
            status="pending",
            dispatch_in="30s"
        )

        # Publish journey step event: webhook persisted to DB
        await event_publisher.publish(
            exchange="journey.events",
            routing_key="journey.step.created",
            message={
                "correlation_id": str(correlation_id),
                "service_name": "vcs-gateway",
                "step_type": "event_prepared",
                "status": "completed",
                "tenant_id": str(tenant_id),
                "pr_hash_key": pr_hash_key,
                "pr_id": pr_id,
                "repo_id": repo_id,
                "metadata": {
                    "event_id": str(event_id),
                    "outbox_id": str(outbox_id),
                    "processing_status": "pending"
                },
                "timestamp": datetime.utcnow().isoformat()
            }
        )

        # Publish journey step event: outbox scheduled
        await event_publisher.publish(
            exchange="journey.events",
            routing_key="journey.step.created",
            message={
                "correlation_id": str(correlation_id),
                "service_name": "vcs-gateway",
                "step_type": "outbox_event_scheduled",
                "status": "in_progress",
                "tenant_id": str(tenant_id),
                "metadata": {
                    "outbox_id": str(outbox_id),
                    "dispatch_at": (datetime.utcnow() + timedelta(seconds=30)).isoformat()
                },
                "timestamp": datetime.utcnow().isoformat()
            }
        )

    except Exception as db_error:
        # Database write failed - critical failure
        logger.error(f"Failed to persist webhook to database: {db_error}")

        # Publish journey step event: persistence failed
        await event_publisher.publish(
            exchange="journey.events",
            routing_key="journey.step.created",
            message={
                "correlation_id": str(correlation_id),
                "service_name": "vcs-gateway",
                "step_type": "webhook_persistence_failed",
                "status": "failed",
                "tenant_id": str(tenant_id),
                "pr_hash_key": pr_hash_key,
                "pr_id": pr_id,
                "repo_id": repo_id,
                "metadata": {"error": str(db_error)},
                "timestamp": datetime.utcnow().isoformat()
            }
        )

        # Return 500 Internal Server Error (retry-able)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "PERSISTENCE_FAILED",
                "message": "Failed to persist webhook, please retry",
                "retry_after": 60
            },
            headers={"Retry-After": "60"}
        )

    # ✅ STEP 7: Return 202 Accepted (Fast Response)
    # Webhook safely stored in DB with outbox event (30s debounce)
    # Outbox Dispatcher will handle dispatch to RabbitMQ asynchronously
    # Total time: 35-50ms (vs 250-350ms synchronous processing)
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "correlation_id": str(correlation_id),
            "event_id": str(event_id),
            "outbox_id": str(outbox_id),
            "message": "Webhook scheduled for processing",
            "dispatch_in": "30s"
        }
    )
```

**Output:**
```json
{
  "status": "accepted",
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "event_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "outbox_id": "8a1b2c3d-4e5f-6789-0abc-def012345678",
  "message": "Webhook scheduled for processing",
  "dispatch_in": "30s"
}
```

**Error Scenarios:**

| Error | HTTP Code | Response | Action |
|-------|-----------|----------|--------|
| Invalid signature | 401 | `{"error": "Invalid signature"}` | Reject, log security event |
| Invalid JSON schema | 400 | `{"error": "Validation failed", "details": [...]}` | Reject, return validation errors |
| Tenant not found | 404 | `{"error": "Tenant not found"}` | Reject, log warning |
| Duplicate PR | 200 | `{"status": "duplicate", "existing_analysis_id": "..."}` | Accept (idempotent), no processing |
| Quota exceeded | 200 | `{"status": "rejected", "reason": "quota_exceeded"}` | Accept (queued for retry) |
| Database error | 500 | `{"error": "Internal server error"}` | Rollback transaction, retry |

---

## 5. API Specification

### REST Endpoints

#### POST /webhooks/github/{tenant_id}

**Purpose:** Receive GitHub pull request webhooks for a specific tenant

**URL Pattern:** `https://api.yourdomain.com/webhooks/github/{tenant_id}`

**Path Parameters:**
- `tenant_id` (UUID, required): Tenant identifier - provided to customer during onboarding

**Authentication:** HMAC-SHA256 signature validation (`X-Hub-Signature-256` header)

**Rate Limit:** 100 requests/minute per tenant (enforced by Kong)

**Event Filtering:** Only accepts `pull_request` events with actions: `opened`, `synchronize`, `reopened`
- ⚠️ **Other events are rejected** (prevents system overload if customer misconfigures webhook)

**Request:**
```http
POST /webhooks/github/550e8400-e29b-41d4-a716-446655440000 HTTP/1.1
Host: api.yourdomain.com
Content-Type: application/json
X-Hub-Signature-256: sha256=abc123def456...
X-GitHub-Event: pull_request
X-GitHub-Delivery: 12345678-1234-1234-1234-123456789012

{
  "action": "opened",
  "pull_request": {
    "id": 123456789,
    "number": 42,
    "title": "Add user authentication",
    "user": {"login": "john.doe"},
    "head": {"sha": "abc123def456"},
    "diff_url": "https://github.com/org/repo/pull/42.diff"
  },
  "repository": {
    "id": 987654321,
    "full_name": "org/repo"
  }
}
```

**Request Schema:**
```typescript
interface GitHubWebhook {
  action: "opened" | "synchronize" | "reopened" | "edited";
  pull_request: {
    id: number;
    number: number;
    title: string;
    user: { login: string };
    head: { sha: string };
    diff_url: string;
  };
  repository: {
    id: number;
    full_name: string;
  };
}
```

**Response (Success - Accepted - 200 OK):**
```json
{
  "status": "accepted",
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "event_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "journey_id": "9f8d7c6b-5432-1098-7654-3210fedcba98",
  "message": "PR analysis queued for processing"
}
```

**Response (Success - Duplicate - 200 OK):**
```json
{
  "status": "duplicate",
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "existing_analysis_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "message": "PR already analyzed with this commit"
}
```

**Response (Success - Quota Rejected - 200 OK):**
```json
{
  "status": "rejected",
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "reason": "monthly_call_limit_exceeded",
  "message": "Quota limit exceeded, PR queued for retry when quota resets",
  "next_retry": "2025-02-01T00:00:00Z"
}
```

**Response (Error - Invalid Signature - 401 Unauthorized):**
```json
{
  "error": {
    "code": "INVALID_SIGNATURE",
    "message": "Webhook signature validation failed",
    "details": {}
  }
}
```

**Response (Error - Event Not Allowed - 400 Bad Request):**
```json
{
  "error": {
    "code": "EVENT_NOT_ALLOWED",
    "message": "Event type not supported - only PR events allowed",
    "details": {
      "received_event": "push",
      "allowed_events": ["pull_request"],
      "allowed_actions": ["opened", "synchronize", "reopened"]
    }
  }
}
```

**Response (Error - Validation Failed - 400 Bad Request):**
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid webhook payload",
    "details": {
      "field": "pull_request.id",
      "error": "Field required"
    }
  }
}
```

**Response (Error - Tenant Not Found - 404 Not Found):**
```json
{
  "error": {
    "code": "TENANT_NOT_FOUND",
    "message": "Tenant ID not found or inactive",
    "details": {
      "tenant_id": "550e8400-e29b-41d4-a716-446655440000"
    }
  }
}
```

**Response (Error - Internal Server Error - 500):**
```json
{
  "error": {
    "code": "INTERNAL_ERROR",
    "message": "An unexpected error occurred",
    "details": {}
  }
}
```

**Error Codes:**
- `INVALID_SIGNATURE` (401): Webhook signature validation failed
- `VALIDATION_ERROR` (400): Invalid webhook payload schema
- `TENANT_NOT_FOUND` (404): Repository not associated with any tenant
- `RATE_LIMIT_EXCEEDED` (429): Too many requests (enforced by Kong)
- `INTERNAL_ERROR` (500): Database error, service unavailable

---

#### POST /webhooks/gitlab/{tenant_id}

**Purpose:** Receive GitLab merge request webhooks for a specific tenant

**URL Pattern:** `https://api.yourdomain.com/webhooks/gitlab/{tenant_id}`

**Path Parameters:**
- `tenant_id` (UUID, required): Tenant identifier - provided to customer during onboarding

**Authentication:** Token validation (`X-Gitlab-Token` header)

**Rate Limit:** 100 requests/minute per tenant (enforced by Kong)

**Event Filtering:** Only accepts `Merge Request Hook` events with actions: `open`, `update`, `reopen`
- ⚠️ **Other events are rejected** (prevents system overload if customer misconfigures webhook)

**Official Documentation:** [GitLab Merge Request Webhook Events](https://docs.gitlab.com/user/project/integrations/webhooks/#merge-request-events)

**Request (Official GitLab Payload Example):**
```http
POST /webhooks/gitlab/550e8400-e29b-41d4-a716-446655440000 HTTP/1.1
Host: api.yourdomain.com
Content-Type: application/json
X-Gitlab-Token: secret-token-here
X-Gitlab-Event: Merge Request Hook

{
  "object_kind": "merge_request",
  "event_type": "merge_request",
  "user": {
    "name": "John Doe",
    "username": "johndoe",
    "avatar_url": "https://gitlab.example.com/uploads/-/system/user/avatar/1/avatar.png"
  },
  "project": {
    "id": 123,
    "name": "example-project",
    "description": "",
    "web_url": "https://gitlab.example.com/example-project",
    "avatar_url": null,
    "git_ssh_url": "git@gitlab.example.com:example-project.git",
    "git_http_url": "https://gitlab.example.com/example-project.git",
    "namespace": "example-group",
    "visibility_level": 0,
    "path_with_namespace": "example-group/example-project",
    "default_branch": "main"
  },
  "object_attributes": {
    "id": 99,
    "target_branch": "main",
    "source_branch": "feature-branch",
    "source_project_id": 123,
    "target_project_id": 123,
    "title": "Add new feature",
    "state": "opened",
    "merge_status": "unchecked",
    "url": "https://gitlab.example.com/example-project/-/merge_requests/1"
  }
}
```

**Response:** Same format as GitHub webhook

---

#### GET /health/live

**Purpose:** Kubernetes liveness probe - is the service running?

**Authentication:** None (internal endpoint)

**Response (Healthy - 200 OK):**
```json
{
  "status": "ok",
  "timestamp": "2024-12-23T10:30:00Z"
}
```

**Response (Unhealthy - 503 Service Unavailable):**
```json
{
  "status": "error",
  "error": "Service is shutting down"
}
```

---

#### GET /health/ready

**Purpose:** Kubernetes readiness probe - is the service ready to accept traffic?

**Authentication:** None (internal endpoint)

**Checks:**
- Database connection pool healthy
- RabbitMQ connection established
- Policy Engine Service reachable (optional - degraded mode if unavailable)

**Response (Ready - 200 OK):**
```json
{
  "status": "ready",
  "timestamp": "2024-12-23T10:30:00Z",
  "checks": {
    "database": "ok",
    "rabbitmq": "ok",
    "quota_service": "ok"
  }
}
```

**Response (Not Ready - 503 Service Unavailable):**
```json
{
  "status": "not_ready",
  "timestamp": "2024-12-23T10:30:00Z",
  "checks": {
    "database": "ok",
    "rabbitmq": "error",
    "quota_service": "ok"
  },
  "error": "RabbitMQ connection failed"
}
```

---

### Internal API Endpoints (Idempotency & Stale Detection)

These endpoints are **internal-only** and called by **other services** (Orchestrator, LLM Service, Response Processor, etc.) for duplicate/stale detection.

**NOT called by Policy Engine** - Policy Engine only handles quota management.

#### GET /internal/v1/events/check-duplicate

**Purpose:** Check if a webhook with the same pr_hash_key has already been processed (idempotency check)

**Called by:** Orchestrator, LLM Service, Response Processor, Delivery Service (any service that needs idempotency check)

**Authentication:** Internal service token (Kong internal routing)

**Query Parameters:**
- `pr_hash_key` (string, required): SHA256 hash of webhook idempotency key (64 chars)
- `correlation_id` (UUID, optional): Correlation ID for tracing

**Request:**
```http
GET /internal/v1/events/check-duplicate?pr_hash_key=abc123def456...&correlation_id=uuid HTTP/1.1
Host: vcs-gateway-service
X-Service-Token: internal-service-token
X-Correlation-ID: uuid-v7
```

**Response (Duplicate Found - 200 OK):**
```json
{
  "is_duplicate": true,
  "existing_event_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "existing_correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "processing_status": "completed",
  "last_processed_at": "2025-01-15T10:30:00Z",
  "pr_hash_key": "abc123def456...",
  "cache_hit": true
}
```

**Response (Not Duplicate - 200 OK):**
```json
{
  "is_duplicate": false,
  "pr_hash_key": "abc123def456...",
  "cache_hit": false
}
```

**Implementation (Cache-Aside Pattern):**
```python
@router.get("/internal/v1/events/check-duplicate")
async def check_duplicate(
    pr_hash_key: str = Query(..., min_length=64, max_length=64),
    correlation_id: Optional[UUID] = Query(None),
    redis: Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db)
):
    """
    Check if webhook with same pr_hash_key already processed.

    Called by: Orchestrator, LLM Service, Response Processor, etc.
    Use case: Idempotency check (exact duplicate webhook detection)

    Pattern: Cache-Aside (Redis → DB → Write-back)
    Performance: 2ms (cache hit) vs 15ms (cache miss)
    """
    cache_key = f"idempotency:pr_hash:{pr_hash_key}"

    # STEP 1: Try Redis cache first (Cache-Aside)
    try:
        cached_data = await redis.get(cache_key)
        if cached_data:
            duplicate_info = json.loads(cached_data)
            logger.debug(f"Duplicate check cache hit for {pr_hash_key}")

            return {
                "is_duplicate": True,
                "existing_event_id": duplicate_info["event_id"],
                "existing_correlation_id": duplicate_info["correlation_id"],
                "processing_status": duplicate_info["status"],
                "last_processed_at": duplicate_info["processed_at"],
                "pr_hash_key": pr_hash_key,
                "cache_hit": True
            }
    except RedisError as e:
        logger.warning(f"Redis cache miss: {e}, falling back to DB")

    # STEP 2: Cache miss → Query database
    existing_event = await db.execute(
        """
        SELECT event_id, correlation_id, processing_status, created_at
        FROM vcs_gateway_schema.inbound_event
        WHERE pr_hash_key = :pr_hash_key
        LIMIT 1
        """,
        {"pr_hash_key": pr_hash_key}
    ).fetchone()

    if existing_event:
        # STEP 3: Write-back to Redis cache (Cache-Aside write-back)
        duplicate_info = {
            "event_id": str(existing_event.event_id),
            "correlation_id": str(existing_event.correlation_id),
            "status": existing_event.processing_status,
            "processed_at": existing_event.created_at.isoformat()
        }

        try:
            # TTL: 72 hours (typical webhook retry window)
            await redis.setex(
                cache_key,
                259200,  # 72 hours = 3 days
                json.dumps(duplicate_info)
            )
            logger.debug(f"Duplicate check cache populated for {pr_hash_key}")
        except RedisError:
            pass  # Ignore cache write failures

        return {
            "is_duplicate": True,
            "existing_event_id": str(existing_event.event_id),
            "existing_correlation_id": str(existing_event.correlation_id),
            "processing_status": existing_event.processing_status,
            "last_processed_at": existing_event.created_at.isoformat(),
            "pr_hash_key": pr_hash_key,
            "cache_hit": False
        }

    # Not a duplicate (new webhook)
    return {
        "is_duplicate": False,
        "pr_hash_key": pr_hash_key,
        "cache_hit": False
    }
```

---

#### GET /internal/v1/events/check-stale

**Purpose:** Check if PR version is stale (older version when newer version exists). Prevents processing outdated PR versions.

**Called by:** Delivery Service (when Redis cache unavailable), Orchestrator, LLM Service, Response Processor (fallback when cannot access idempotency cache directly)

**Authentication:** Internal service token (Kong internal routing)

**Query Parameters:**
- `pr_hash_key` (string, required): SHA256 hash combining tenant_id, repo_id, pr_id, commit_sha (64 chars)
- `pr_version` (integer, required): PR version number to check (monotonic version, starts at 1)
- `correlation_id` (UUID, optional): Correlation ID for tracing

**Request:**
```http
GET /internal/v1/events/check-stale?pr_hash_key=abc123def456...&pr_version=2&correlation_id=uuid HTTP/1.1
Host: vcs-gateway-service
X-Service-Token: internal-service-token
X-Correlation-ID: uuid-v7
```

**Response (Stale - Newer Version Exists - 200 OK):**
```json
{
  "is_stale": true,
  "provided_version": 2,
  "latest_version": 5,
  "existing_event_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "existing_correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "processing_status": "completed",
  "last_processed_at": "2025-01-15T10:30:00Z",
  "pr_hash_key": "abc123def456...",
  "reason": "newer_version_exists",
  "cache_hit": true
}
```

**Response (Not Stale - Version Matches - 200 OK):**
```json
{
  "is_stale": false,
  "provided_version": 5,
  "latest_version": 5,
  "existing_event_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "existing_correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "processing_status": "completed",
  "pr_hash_key": "abc123def456...",
  "reason": "version_matches",
  "cache_hit": true
}
```

**Response (Not Stale - New PR - 200 OK):**
```json
{
  "is_stale": false,
  "pr_hash_key": "abc123def456...",
  "cache_hit": false
}
```

**Implementation (Cache-Aside Pattern with Version Comparison):**
```python
@router.get("/internal/v1/events/check-stale")
async def check_stale(
    pr_hash_key: str = Query(..., min_length=64, max_length=64),
    pr_version: int = Query(..., ge=1),
    correlation_id: Optional[UUID] = Query(None),
    redis: Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db)
):
    """
    Check if PR version is stale (older version already processed).

    Called by: Delivery Service (when Redis cache unavailable), other services
    Use case: Prevent processing older PR versions when newer version exists

    Pattern: Idempotency Cache → DB fallback
    Performance: 2ms (cache hit) vs 15ms (cache miss)

    Algorithm:
    1. Check idempotency cache for pr_hash_key
    2. If cache hit: Compare cached pr_version with provided pr_version
    3. If cache miss: Query DB for MAX(pr_version) with same pr_hash_key
    4. If versions match: Not stale (OK to process)
    5. If versions differ: Stale (newer version exists)

    Note: All services can directly access idempotency cache.
          This API is a fallback when cache is unavailable (e.g., Delivery Service).
    """
    # STEP 1: Try idempotency cache first
    idempotency_key = f"idempotency:{pr_hash_key}"

    try:
        cached_data = await redis.get(idempotency_key)
        if cached_data:
            cached_event = json.loads(cached_data)
            cached_version = cached_event.get("pr_version")

            if cached_version is not None:
                logger.debug(f"Stale check cache hit for {pr_hash_key}")

                is_stale = (pr_version != cached_version)

                return {
                    "is_stale": is_stale,
                    "provided_version": pr_version,
                    "latest_version": cached_version,
                    "existing_event_id": cached_event.get("event_id"),
                    "existing_correlation_id": cached_event.get("correlation_id"),
                    "processing_status": cached_event.get("processing_status"),
                    "pr_hash_key": pr_hash_key,
                    "reason": "newer_version_exists" if is_stale else "version_matches",
                    "cache_hit": True
                }
    except RedisError as e:
        logger.warning(f"Redis cache unavailable: {e}, falling back to DB")

    # STEP 2: Cache miss → Query database for MAX pr_version
    result = await db.execute(
        """
        SELECT MAX(pr_version) as max_version,
               event_id,
               correlation_id,
               processing_status,
               created_at
        FROM vcs_gateway_schema.inbound_event
        WHERE pr_hash_key = :pr_hash_key
          AND processing_status IN ('accepted', 'processing', 'completed')
        GROUP BY event_id, correlation_id, processing_status, created_at
        ORDER BY max_version DESC
        LIMIT 1
        """,
        {"pr_hash_key": pr_hash_key}
    ).fetchone()

    if result and result.max_version is not None:
        max_version = result.max_version
        is_stale = (pr_version != max_version)

        return {
            "is_stale": is_stale,
            "provided_version": pr_version,
            "latest_version": max_version,
            "existing_event_id": str(result.event_id),
            "existing_correlation_id": str(result.correlation_id),
            "processing_status": result.processing_status,
            "last_processed_at": result.created_at.isoformat(),
            "pr_hash_key": pr_hash_key,
            "reason": "newer_version_exists" if is_stale else "version_matches",
            "cache_hit": False
        }

    # No existing event found (new PR)
    return {
        "is_stale": False,
        "pr_hash_key": pr_hash_key,
        "cache_hit": False
    }
```

**Performance:**
- Cache hit: ~2ms (95%+ hit ratio expected)
- Cache miss + DB query: ~15ms (indexed on pr_hash_key)
- Cache write-back: ~1ms (async, non-blocking)

**Cache Strategy:**
- Key pattern: `stale:pr_hash:{pr_hash_key}`
- TTL: 72 hours (typical PR lifecycle)
- Invalidation: Automatic via TTL (no manual invalidation needed)
- Memory: ~200 bytes per cached entry

**Security:**
- ✅ Internal-only endpoint (not exposed to internet)
- ✅ Kong internal routing with service token authentication
- ✅ Read-only operation (no data modification)

---

## 6. Database Schema

### Tables Referenced by This Service

#### Table: `tenant_vcs_config` (Read-Write Access in `shared_schema`)

**Purpose:** Stores tenant-specific VCS configuration and auto-onboards repositories on first webhook

**Schema:**
```sql
-- This table is owned by shared_schema, VCS Gateway has READ-WRITE access for auto-onboarding
CREATE TABLE shared_schema.tenant_vcs_config (
    config_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES shared_schema.tenant(tenant_id),
    vcs_provider VARCHAR(50) NOT NULL,  -- 'github', 'gitlab', 'bitbucket'
    repo_id VARCHAR(255) NOT NULL,      -- VCS-specific repo identifier (GitHub: repo.id, GitLab: project.id)
    repo_name VARCHAR(500),             -- 'org/repo' or 'group/project' format
    repo_url VARCHAR(1000),             -- Full repository URL (e.g., https://github.com/org/repo)
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Ensure one config per tenant+repo combination
    CONSTRAINT unique_tenant_vcs_repo UNIQUE (tenant_id, vcs_provider, repo_id)
);

-- Index for fast auto-onboarding lookup (check if repo exists)
CREATE INDEX idx_tenant_vcs_config_lookup ON shared_schema.tenant_vcs_config(tenant_id, vcs_provider, repo_id)
WHERE is_active = TRUE;

CREATE INDEX idx_tenant_vcs_config_tenant ON shared_schema.tenant_vcs_config(tenant_id);
```

**Usage in VCS Gateway:**
```python
# Auto-onboarding: Check if repository already registered
vcs_config = await db.execute(
    """
    SELECT config_id
    FROM shared_schema.tenant_vcs_config
    WHERE tenant_id = :tenant_id
      AND vcs_provider = :vcs_provider
      AND repo_id = :repo_id
    """,
    {"tenant_id": tenant_id, "vcs_provider": "github", "repo_id": "987654321"}
).fetchone()

if not vcs_config:
    # First webhook from this repo - auto-register
    await db.execute(
        """
        INSERT INTO shared_schema.tenant_vcs_config (
            config_id, tenant_id, vcs_provider, repo_id,
            repo_name, repo_url, is_active, created_at
        ) VALUES (
            gen_random_uuid(), :tenant_id, :vcs_provider, :repo_id,
            :repo_name, :repo_url, TRUE, NOW()
        )
        """,
        {
            "tenant_id": tenant_id,
            "vcs_provider": "github",
            "repo_id": "987654321",
            "repo_name": "octocat/Hello-World",
            "repo_url": "https://github.com/octocat/Hello-World"
        }
    )
```

**Access Control:**
- **VCS Gateway User:** `SELECT` + `INSERT` (auto-onboarding on first webhook)
- **Admin API:** Full CRUD access

---

### Tables Owned by This Service

All tables in `vcs_gateway_schema`:

#### Table: `vcs_event_whitelist` (Configuration Table)

**Purpose:** Stores allowed event types and actions for each VCS provider (GitHub, GitLab, Bitbucket)

TODO : 
internal event name ekle asagidaki gibi. dolayisiyla farkli vcs eventlerini kendi sistemimizde normallestirebiliriz.
(vcs_provider='github',
 event_type='pull_request',
 event_action='opened',
 internal_event_name='PR_OPENED')

**Schema:**
```sql
CREATE TABLE shared_schema.vcs_event_whitelist (
    whitelist_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vcs_provider VARCHAR(50) NOT NULL,  -- 'github', 'gitlab', 'bitbucket'
    event_type VARCHAR(100) NOT NULL,   -- 'pull_request', 'Merge Request Hook', etc.
    event_action VARCHAR(100),          -- 'opened', 'synchronize', 'reopened', 'open', 'update', etc.
    is_active BOOLEAN DEFAULT TRUE,
    description TEXT,                   -- Human-readable description
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Ensure unique combination
    CONSTRAINT unique_vcs_event_action UNIQUE (vcs_provider, event_type, event_action)
);

-- Index for fast lookup during webhook validation
CREATE INDEX idx_vcs_event_whitelist_lookup ON shared_schema.vcs_event_whitelist(vcs_provider, event_type, event_action)
WHERE is_active = TRUE;
```

**Seed Data:**
```sql
-- GitHub allowed events
INSERT INTO shared_schema.vcs_event_whitelist (vcs_provider, event_type, event_action, description) VALUES
('github', 'pull_request', 'opened', 'PR opened - new PR created'),
('github', 'pull_request', 'synchronize', 'PR synchronized - new commits pushed'),
('github', 'pull_request', 'reopened', 'PR reopened - previously closed PR reopened');

-- GitLab allowed events
INSERT INTO shared_schema.vcs_event_whitelist (vcs_provider, event_type, event_action, description) VALUES
('gitlab', 'Merge Request Hook', 'open', 'MR opened - new merge request created'),
('gitlab', 'Merge Request Hook', 'update', 'MR updated - new commits pushed'),
('gitlab', 'Merge Request Hook', 'reopen', 'MR reopened - previously closed MR reopened');

-- Bitbucket allowed events (future)
INSERT INTO shared_schema.vcs_event_whitelist (vcs_provider, event_type, event_action, description) VALUES
('bitbucket', 'pullrequest:created', NULL, 'PR created'),
('bitbucket', 'pullrequest:updated', NULL, 'PR updated');
```

**Usage in VCS Gateway:**
```python
# Event validation: Check if event_type and action are allowed
async def is_event_allowed(vcs_provider: str, event_type: str, action: str) -> bool:
    result = await db.execute(
        """
        SELECT whitelist_id
        FROM shared_schema.vcs_event_whitelist
        WHERE vcs_provider = :vcs_provider
          AND event_type = :event_type
          AND (event_action = :action OR event_action IS NULL)
          AND is_active = TRUE
        """,
        {
            "vcs_provider": vcs_provider,
            "event_type": event_type,
            "action": action
        }
    ).fetchone()

    return result is not None

# Get all allowed events for error message
async def get_allowed_events(vcs_provider: str) -> dict:
    results = await db.execute(
        """
        SELECT DISTINCT event_type,
               ARRAY_AGG(event_action) FILTER (WHERE event_action IS NOT NULL) as actions
        FROM shared_schema.vcs_event_whitelist
        WHERE vcs_provider = :vcs_provider
          AND is_active = TRUE
        GROUP BY event_type
        """,
        {"vcs_provider": vcs_provider}
    ).fetchall()

    return {row.event_type: row.actions for row in results}
```

**Access Control:**
- **VCS Gateway User:** `SELECT` only (read-only)
- **Admin API:** Full CRUD access (add/remove allowed events)

**Benefits:**
- ✅ **Flexible Configuration:** Add/remove allowed events without code deploy
- ✅ **Multi-Provider Support:** Different rules for GitHub, GitLab, Bitbucket
- ✅ **Audit Trail:** Track when event types are enabled/disabled
- ✅ **Emergency Response:** Quickly disable problematic event types

---

#### Table: `inbound_event`

**Purpose:** Records every webhook received (accepted, rejected, or duplicate)

**Schema:**
```sql
CREATE SCHEMA IF NOT EXISTS vcs_gateway_schema;

CREATE TABLE vcs_gateway_schema.inbound_event (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    correlation_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    vcs_provider VARCHAR(50) NOT NULL,  -- 'github', 'gitlab', 'bitbucket'
    pr_id VARCHAR(255) NOT NULL,
    repo_id VARCHAR(255) NOT NULL,
    commit_sha VARCHAR(64) NOT NULL,    -- NEW: PR commit SHA for stale detection
    action VARCHAR(50) NOT NULL,        -- NEW: Webhook action (opened, synchronize, etc.)
    pr_hash_key VARCHAR(64) NOT NULL,   -- NEW: Idempotency key (SHA256 hash)
    pr_version INTEGER NOT NULL DEFAULT 1,  -- NEW: Monotonic version (increments per PR update)
    processing_status VARCHAR(50) NOT NULL,  -- 'accepted', 'rejected', 'duplicate'
    rejection_reason VARCHAR(255),
    raw_payload JSONB NOT NULL,
    normalized_payload JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Idempotency: Prevent duplicate webhook processing
    CONSTRAINT unique_correlation_id UNIQUE (correlation_id),
    CONSTRAINT unique_pr_hash_key UNIQUE (pr_hash_key)  -- NEW: Idempotency constraint
);

-- Indexes
CREATE INDEX idx_inbound_event_tenant ON vcs_gateway_schema.inbound_event(tenant_id, created_at DESC);
CREATE INDEX idx_inbound_event_correlation ON vcs_gateway_schema.inbound_event(correlation_id);
CREATE INDEX idx_inbound_event_pr ON vcs_gateway_schema.inbound_event(tenant_id, pr_id);
CREATE INDEX idx_inbound_event_status ON vcs_gateway_schema.inbound_event(processing_status, created_at DESC);
CREATE INDEX idx_inbound_event_hash ON vcs_gateway_schema.inbound_event(pr_hash_key);  -- NEW: Idempotency lookup
```

**Indexes:**
- `PRIMARY KEY (event_id)`: Fast lookup by event ID
- `UNIQUE (correlation_id)`: Request-level idempotency (prevents duplicate correlation_id)
- `UNIQUE (pr_hash_key)`: Webhook-level idempotency (prevents exact duplicate webhooks)
- `INDEX (tenant_id, created_at DESC)`: Query recent events per tenant
- `INDEX (correlation_id)`: Lookup by correlation_id for journey tracking
- `INDEX (tenant_id, pr_id)`: Lookup events for specific PR
- `INDEX (processing_status, created_at)`: Query by status (rejected, duplicate)
- `INDEX (pr_hash_key)`: Fast idempotency check (internal Cache-Aside pattern)

**Constraints:**
- `NOT NULL` on critical fields (tenant_id, vcs_provider, pr_id, commit_sha, action, pr_hash_key)
- `UNIQUE (correlation_id)`: Request-level idempotency
- `UNIQUE (pr_hash_key)`: Webhook-level idempotency (industry-standard pattern)
- `CHECK (processing_status IN ('accepted', 'rejected', 'duplicate'))`

**Data Retention:**
- **Active Data:** Retained for 90 days
- **Archived Data:** Moved to cold storage after 90 days
- **Deleted Data:** Permanent deletion after 1 year

---

#### ~~Table: `pr_journey`~~ (MOVED TO JOURNEY SERVICE)

**⚠️ IMPORTANT:** Journey tables are now managed by **Journey Service** (event-driven).

**VCS Gateway no longer writes directly to journey tables.**

Instead, VCS Gateway publishes `journey.step.created` events to RabbitMQ, and Journey Service consumes these events.

**See:** [Journey Service Documentation](./journey_service.md) for complete schema and API.

**Event Publishing (VCS Gateway):**
```python
# Publish journey step event to RabbitMQ
await event_publisher.publish(
    exchange="journey.events",
    routing_key="journey.step.created",
    message={
        "correlation_id": correlation_id,
        "service_name": "vcs-gateway",
        "step_type": "webhook_received",
        "status": "completed",
        "tenant_id": tenant_id,
        "pr_hash_key": pr_hash_key,
        "metadata": {"vcs": "github", "pr_id": "42"}
    }
)
```

**Benefits:**
- ✅ Loose coupling: VCS Gateway doesn't need journey database access
- ✅ Journey Service is single source of truth for journey data
- ✅ Easier schema evolution (only Journey Service changes)
- ✅ Better separation of concerns

**Migration Notes:**
- Journey tables moved from `vcs_gateway_schema` to `shared_schema`
- Journey Service auto-creates journey on first step event (correlation_id based)
- Query journeys via Journey Service API: `GET /journey/{correlation_id}`

---

#### Table: `outbox_event`

**Purpose:** Outbox pattern - ensures transactional event publishing

**Schema:**
```sql
CREATE TABLE vcs_gateway_schema.outbox_event (
    outbox_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type VARCHAR(100) NOT NULL,  -- 'repo.change.detected'
    correlation_id UUID NOT NULL,
    pr_hash_key VARCHAR(64),           -- Idempotency key reference
    pr_version INTEGER,                -- Monotonic version (increments per PR update)
    payload JSONB NOT NULL,
    headers JSONB NOT NULL,            -- RabbitMQ message headers (traceparent, pr_hash_key, pr_version, etc.)
    status VARCHAR(20) NOT NULL DEFAULT 'SCHEDULED',  -- SCHEDULED, CANCELLED, DISPATCHED, FAILED
    dispatch_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW() + INTERVAL '30 seconds',  -- Debounce: dispatch after 30s
    cancel_reason VARCHAR(50),         -- SUPERSEDED_BY_NEW_COMMIT, PR_CLOSED, PR_MERGED, etc.
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    next_retry_at TIMESTAMP WITH TIME ZONE,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    published_at TIMESTAMP WITH TIME ZONE,

    -- Deprecated: Use status instead
    published BOOLEAN GENERATED ALWAYS AS (status = 'DISPATCHED') STORED
);

CREATE INDEX idx_outbox_scheduled ON vcs_gateway_schema.outbox_event(status, dispatch_at)
WHERE status = 'SCHEDULED';
```

**Outbox Dispatcher (Background Worker with Debounce):**
```python
# Runs every 1 second, dispatches scheduled events after debounce window
async def outbox_dispatcher():
    """
    Background worker that:
    1. Waits for 30-second debounce window
    2. Checks Redis for stale/closed PRs before dispatch
    3. Cancels events invalidated during debounce
    4. Dispatches valid events to RabbitMQ
    """
    while True:
        # Get scheduled events ready for dispatch (status = 'SCHEDULED' AND dispatch_at <= NOW())
        # FOR UPDATE SKIP LOCKED prevents race conditions with multiple workers
        events = await db.execute(
            """
            SELECT * FROM vcs_gateway_schema.outbox_event
            WHERE status = 'SCHEDULED' AND dispatch_at <= NOW()
            ORDER BY created_at ASC
            LIMIT 100
            FOR UPDATE SKIP LOCKED
            """
        ).fetchall()

        for event in events:
            try:
                # DEBOUNCE CHECK: Verify PR state in Redis before dispatch
                redis_key = f"idempotency:{event.pr_hash_key}"
                cached_data = await redis.get(redis_key)

                if cached_data:
                    data = json.loads(cached_data)

                    # Check 1: Is PR closed/merged?
                    if data.get("processing_status") in ("CLOSED", "MERGED", "FINAL"):
                        await db.execute(
                            """
                            UPDATE vcs_gateway_schema.outbox_event
                            SET status = 'CANCELLED',
                                cancel_reason = 'PR_CLOSED',
                                updated_at = NOW()
                            WHERE outbox_id = :outbox_id
                            """,
                            {"outbox_id": event.outbox_id}
                        )

                        # Publish journey event for FE UI visibility
                        await publish_journey_event(
                            correlation_id=event.correlation_id,
                            event_type="outbox_event_cancelled",
                            status="info",
                            message=f"Event cancelled: PR closed/merged",
                            metadata={
                                "outbox_id": str(event.outbox_id),
                                "cancel_reason": "PR_CLOSED",
                                "pr_version": event.pr_version,
                                "processing_status": data.get("processing_status")
                            }
                        )

                        logger.info(f"Cancelled outbox event {event.outbox_id}: PR closed/merged")
                        continue

                    # Check 2: Is this event stale (newer version exists)?
                    if data.get("pr_version", 0) > event.pr_version:
                        await db.execute(
                            """
                            UPDATE vcs_gateway_schema.outbox_event
                            SET status = 'CANCELLED',
                                cancel_reason = 'SUPERSEDED_BY_NEW_COMMIT',
                                updated_at = NOW()
                            WHERE outbox_id = :outbox_id
                            """,
                            {"outbox_id": event.outbox_id}
                        )

                        # Publish journey event for FE UI visibility
                        await publish_journey_event(
                            correlation_id=event.correlation_id,
                            event_type="outbox_event_cancelled",
                            status="info",
                            message=f"Event cancelled: Superseded by newer commit",
                            metadata={
                                "outbox_id": str(event.outbox_id),
                                "cancel_reason": "SUPERSEDED_BY_NEW_COMMIT",
                                "old_pr_version": event.pr_version,
                                "new_pr_version": data.get("pr_version", 0)
                            }
                        )

                        logger.info(f"Cancelled outbox event {event.outbox_id}: Superseded by pr_version {data['pr_version']}")
                        continue

                # Event is valid, dispatch to RabbitMQ
                await rabbitmq.publish(
                    exchange="events",
                    routing_key=event.event_type,
                    message=event.payload,
                    headers=event.headers
                )

                # Mark as DISPATCHED
                await db.execute(
                    """
                    UPDATE vcs_gateway_schema.outbox_event
                    SET status = 'DISPATCHED',
                        published_at = NOW(),
                        updated_at = NOW()
                    WHERE outbox_id = :outbox_id
                    """,
                    {"outbox_id": event.outbox_id}
                )

                # Publish journey event for FE UI visibility
                await publish_journey_event(
                    correlation_id=event.correlation_id,
                    event_type="outbox_event_dispatched",
                    status="success",
                    message=f"Event dispatched to RabbitMQ after 30s debounce",
                    metadata={
                        "outbox_id": str(event.outbox_id),
                        "event_type": event.event_type,
                        "pr_version": event.pr_version,
                        "debounce_duration_ms": int((datetime.now() - event.created_at).total_seconds() * 1000)
                    }
                )

                logger.info(f"Dispatched outbox event {event.outbox_id} to queue")

            except Exception as e:
                logger.error(f"Failed to dispatch event {event.outbox_id}: {e}")

                # Increment retry count
                retry_count = event.retry_count + 1

                if retry_count >= event.max_retries:
                    # Mark as FAILED (max retries exceeded)
                    await db.execute(
                        """
                        UPDATE vcs_gateway_schema.outbox_event
                        SET status = 'FAILED',
                            error_message = :error,
                            updated_at = NOW()
                        WHERE outbox_id = :outbox_id
                        """,
                        {"outbox_id": event.outbox_id, "error": str(e)}
                    )
                else:
                    # Retry with exponential backoff
                    next_retry = datetime.now() + timedelta(seconds=2 ** retry_count)
                    await db.execute(
                        """
                        UPDATE vcs_gateway_schema.outbox_event
                        SET retry_count = :retry_count,
                            next_retry_at = :next_retry,
                            dispatch_at = :next_retry,
                            updated_at = NOW()
                        WHERE outbox_id = :outbox_id
                        """,
                        {
                            "outbox_id": event.outbox_id,
                            "retry_count": retry_count,
                            "next_retry": next_retry
                        }
                    )

        await asyncio.sleep(1)
```


---

## 7. Queue Integration

### Published Queues

#### Queue: `repo.change.detected`

**Purpose:** Notify Orchestrator that a new PR event is ready for analysis

**Message Format (Fat Message - includes full PR data):**

**HEADERS (RabbitMQ Message Headers):**
```json
{
  "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
  "tracestate": "rojo=00f067aa0ba902b7",
  "correlation-id": "550e8400-e29b-41d4-a716-446655440000",
  "pr_hash_key": "abc123def456...",  // SHA256 hash (idempotency)
  "pr_version": "1",  // Monotonic version (increments per PR update)
  "tenant-id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "content-type": "application/json",
  "message-type": "repo.change.detected",
  "timestamp": "2025-12-27T10:30:00Z",
  "service-name": "vcs-gateway",
  "service-version": "1.0.0"
}
```

**Header Fields:**
- `traceparent`: W3C Trace Context (OpenTelemetry distributed tracing)
- `tracestate`: W3C Trace State (vendor-specific trace info)
- `correlation-id`: Request correlation ID (end-to-end journey tracking)
- `pr_hash_key`: SHA256 hash for idempotency (prevents duplicate processing)
- `pr_version`: Monotonic version number (increments with each PR update: 1, 2, 3, ...)
- `tenant-id`: Tenant UUID (multi-tenancy isolation)
- `content-type`: Message content type
- `message-type`: Event type (for routing and filtering)
- `timestamp`: Event creation timestamp (ISO 8601)
- `service-name`: Source service name (for debugging)
- `service-version`: Source service version (for compatibility)
- `vcs-provider` : github/gitlab/ ...



PAYLOAD
```json
{
  "event_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "timestamp": "2025-12-27T10:30:00Z",
  "data": {
    "pr_id": "42",
    "repo_id": "987654321",
    "repo_name": "org/repo",
    "author": "john.doe",
    "author_email": "john.doe@example.com",
    "commit_sha": "abc123def456",
    "pr_title": "Add user authentication",
    "pr_url": "github.com",
    "diff_url": "github.com.diff",
    "vcs_provider": "github",
    "pr_action": "opened"
  }
}

```

**Publisher Configuration:**
- **Exchange:** `events` (topic exchange)
- **Routing Key:** `repo.change.detected`
- **Message Persistence:** Durable (survives broker restart)
- **Publisher Confirms:** Enabled (wait for broker ACK)
- **Publishing Pattern:** Outbox Pattern (transactional)

**RabbitMQ Configuration:**
```python
# Declare exchange
await channel.declare_exchange(
    "events",
    exchange_type="topic",
    durable=True
)

# Publish message
await channel.default_exchange.publish(
    aio_pika.Message(
        body=json.dumps(message).encode(),
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT
    ),
    routing_key="repo.change.detected"
)
```

---

### No Consumed Queues

VCS Gateway Service **does not consume any queues** - it is purely a webhook ingestion service that publishes events.

---

## 7. Journey Tracking

VCS Gateway publishes journey events to track webhook processing lifecycle.

### Journey Events Published

VCS Gateway publishes the following journey events to `journey.events` exchange:

| Event Step Type | Status | When Published | Metadata |
|----------------|--------|----------------|----------|
| **Fast Path (Webhook Handler - Steps 1-7)** | | | |
| `webhook_received` | in_progress | STEP 2: Journey tracking initialized | `vcs_provider`, `repo_id`, `pr_id`, `event_type` |
| `signature_verified` | completed | STEP 3: Signature validation passed | `validation_time_ms` |
| `signature_failed` | failed | STEP 3: Invalid signature | `error`, `reason` |
| `event_type_validated` | completed | STEP 4: Event type allowed (PR event) | `event_type` |
| `event_rejected` | completed | STEP 4: Event type not allowed (ignored) | `event_type`, `reason` |
| `payload_validated` | completed | STEP 5: Payload schema validation passed | `schema_validation_time_ms` |
| `validation_failed` | failed | STEP 5: Invalid payload schema | `error`, `validation_errors` |
| `idempotency_checked` | completed | STEP 5.5: New webhook (not duplicate) | `pr_hash_key` |
| `duplicate_cache` | completed | STEP 5.5: Duplicate detected (Redis cache hit) | `pr_hash_key`, `cache_ttl_remaining` |
| `duplicate_db` | completed | STEP 5.5: Duplicate detected (DB hit) | `pr_hash_key`, `existing_event_id` |
| `webhook_persisted` | completed | STEP 6: Webhook written to database | `event_id`, `processing_status='pending'` |
| `persistence_failed` | failed | STEP 6: Database write failed | `error`, `retry_after` |
| **Background Worker (Steps 8-13)** | | | |
| `repo_onboarded` | completed | STEP 8: New repository auto-onboarded | `repo_id`, `repo_name` |
| `repo_config_loaded` | completed | STEP 8: Existing repository config loaded | `repo_id` |
| `idempotency_validated` | completed | STEP 9: Not duplicate/stale (background check) | `pr_hash_key` |
| `duplicate/stale` | completed | STEP 9: Duplicate or stale PR detected | `reason`, `existing_event_id` |
| `quota_approved` | completed | STEP 10: Quota check approved | `remaining_calls`, `remaining_cost_usd` |
| `quota_rejected` | failed | STEP 10: Quota exceeded | `reason`, `limit_exceeded` |
| `payload_normalized` | completed | STEP 11: VCS payload normalized | `normalized_format` |
| `event_prepared` | completed | STEP 12: Outbox event prepared (transactional) | `outbox_event_id` |
| `outbox_event_scheduled` | in_progress | STEP 12: Outbox event scheduled for dispatch (30s debounce) | `outbox_id`, `dispatch_at`, `pr_version` |
| `queue_published` | completed | STEP 13: Event published to RabbitMQ | `queue_name`, `routing_key` |
| **Outbox Dispatcher (Debounce Worker)** | | | |
| `outbox_event_cancelled` | info | Debounce window: Event cancelled (PR closed/merged or superseded) | `outbox_id`, `cancel_reason`, `pr_version` |
| `outbox_event_dispatched` | success | Debounce window: Event dispatched to RabbitMQ after 30s | `outbox_id`, `event_type`, `pr_version`, `debounce_duration_ms` |

### Event Schema

```json
{
  "correlation_id": "uuid-v7",
  "service_name": "vcs-gateway",
  "step_type": "webhook_received",
  "status": "in_progress",
  "tenant_id": "uuid",
  "metadata": {
    "vcs_provider": "github",
    "repo_id": "123456789",
    "pr_id": "42",
    "event_type": "pull_request.synchronize",
    "commit_sha": "abc123def456"
  },
  "timestamp": "2025-12-28T10:30:00Z"
}
```

### Event Publishing Service

```python
class JourneyService:
    def __init__(self, rabbitmq_channel):
        self.channel = rabbitmq_channel
        self.exchange = "journey.events"
        self.routing_key = "journey.step.created"

    async def publish_event(
        self,
        correlation_id: str,
        service_name: str,
        step_type: str,
        status: str,
        metadata: dict
    ):
        """
        Publish journey tracking event.

        Fire-and-forget pattern: Don't block webhook processing on journey failures.
        """
        message = {
            "correlation_id": correlation_id,
            "service_name": service_name,
            "step_type": step_type,
            "status": status,
            "metadata": metadata,
            "timestamp": datetime.utcnow().isoformat()
        }

        try:
            await self.channel.basic_publish(
                exchange=self.exchange,
                routing_key=self.routing_key,
                body=json.dumps(message),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # persistent
                    content_type="application/json",
                    correlation_id=correlation_id
                )
            )
            logger.debug(f"Journey event published: {step_type}")
        except Exception as e:
            logger.error(f"Failed to publish journey event: {e}")
            # Fire-and-forget: don't block webhook processing
```

### Journey Event Flow Example

**Successful Webhook Processing (with 30s Debounce):**
```
1. webhook_received (in_progress)
   → correlation_id: abc-123
   → metadata: {vcs_provider: "github", pr_id: "42"}

2. webhook_validated (completed)
   → correlation_id: abc-123
   → metadata: {validation_time_ms: 15}

3. outbox_event_scheduled (in_progress)
   → correlation_id: abc-123
   → metadata: {outbox_id: "uuid", dispatch_at: "2025-12-31T10:30:30Z", pr_version: 1}

[... 30 seconds debounce window ...]

4. outbox_event_dispatched (success)
   → correlation_id: abc-123
   → metadata: {outbox_id: "uuid", event_type: "vcs.webhook.received", pr_version: 1, debounce_duration_ms: 30012}
```

**Event Cancelled During Debounce (Superseded by New Commit):**
```
1. webhook_received (in_progress) - First commit
   → correlation_id: def-456
   → metadata: {pr_id: "42", pr_version: 1}

2. outbox_event_scheduled (in_progress)
   → correlation_id: def-456
   → metadata: {outbox_id: "uuid-1", dispatch_at: "2025-12-31T10:30:30Z", pr_version: 1}

[... 15 seconds later, new commit pushed ...]

3. webhook_received (in_progress) - Second commit
   → correlation_id: ghi-789
   → metadata: {pr_id: "42", pr_version: 2}

4. outbox_event_scheduled (in_progress)
   → correlation_id: ghi-789
   → metadata: {outbox_id: "uuid-2", dispatch_at: "2025-12-31T10:30:45Z", pr_version: 2}

5. outbox_event_cancelled (info) - First event cancelled
   → correlation_id: def-456
   → metadata: {outbox_id: "uuid-1", cancel_reason: "SUPERSEDED_BY_NEW_COMMIT", old_pr_version: 1, new_pr_version: 2}

[... 30 seconds from second event ...]

6. outbox_event_dispatched (success) - Second event dispatched
   → correlation_id: ghi-789
   → metadata: {outbox_id: "uuid-2", event_type: "vcs.webhook.received", pr_version: 2, debounce_duration_ms: 30008}
```

**Event Cancelled During Debounce (PR Closed):**
```
1. webhook_received (in_progress)
   → correlation_id: jkl-012
   → metadata: {pr_id: "42", pr_version: 3, action: "synchronize"}

2. outbox_event_scheduled (in_progress)
   → correlation_id: jkl-012
   → metadata: {outbox_id: "uuid-3", dispatch_at: "2025-12-31T10:31:00Z", pr_version: 3}

[... 10 seconds later, PR closed via UI ...]

3. webhook_received (in_progress) - Close event
   → correlation_id: mno-345
   → metadata: {pr_id: "42", pr_version: 4, action: "closed"}

4. outbox_event_cancelled (info) - Previous event cancelled
   → correlation_id: jkl-012
   → metadata: {outbox_id: "uuid-3", cancel_reason: "PR_CLOSED", pr_version: 3, processing_status: "CLOSED"}
```

**Duplicate Webhook:**
```
1. webhook_received (in_progress)
   → correlation_id: pqr-678

2. webhook_validated (completed)
   → correlation_id: pqr-678

3. duplicate_detected (completed)
   → correlation_id: pqr-678
   → metadata: {existing_event_id: "7c9e6679-...", pr_hash_key: "sha256..."}
```

**Quota Exceeded:**
```
1. webhook_received (in_progress)
   → correlation_id: stu-901

2. webhook_validated (completed)
   → correlation_id: stu-901

3. quota_check_failed (failed)
   → correlation_id: stu-901
   → metadata: {reason: "monthly_call_limit_exceeded", limit: 1000}
```

### RabbitMQ Configuration

```python
RABBITMQ_HOST = "rabbitmq-service"
RABBITMQ_PORT = 5672
RABBITMQ_VHOST = "/"
RABBITMQ_USER = "vcs_gateway"
RABBITMQ_PASSWORD = "secure-password"

JOURNEY_EXCHANGE = "journey.events"
JOURNEY_ROUTING_KEY = "journey.step.created"
```

### Resilience

- ✅ **Fire-and-forget**: Journey publishing never blocks webhook processing
- ✅ **Async publishing**: Non-blocking RabbitMQ calls
- ✅ **Error logging**: Failed publishes logged but ignored
- ✅ **Persistent messages**: `delivery_mode=2` ensures durability
- ✅ **Correlation ID**: Propagated through all events for tracing

---

## 8. External Dependencies

### Infrastructure Dependencies

#### Dependency: Redis Cache (NEW - MVP Critical for Performance)

**Purpose:** Centralized cache for tenant configuration, webhook secrets, and background webhook queue

**Integration Type:** TCP connection (Redis protocol)

**Use Cases:**

1. **Tenant Configuration Cache (Primary Use Case)**
   - Cache `tenant_vcs_config` lookups (tenant validation + webhook secret)
   - **Performance Impact:** Reduces DB query from 20ms → 2ms (10x faster)
   - **Cache Key Pattern:** `tenant:config:{tenant_id}`
   - **TTL:** 5 minutes (300 seconds)
   - **Cache Invalidation:** On tenant config update via Redis pub/sub

2. **Internal Idempotency Cache (Production)**
   - Cache duplicate/stale detection results using Cache-Aside pattern
   - **Cache Key Pattern:** `idempotency:pr_hash:{pr_hash_key}`
   - **TTL:** 72 hours (259200 seconds) - matches webhook retry window
   - **Benefit:** Fast duplicate detection without DB queries (95%+ cache hit ratio)

**Connection Configuration:**
```python
# Redis Client Configuration
redis_client = Redis(
    host=os.getenv("REDIS_HOST", "redis"),
    port=int(os.getenv("REDIS_PORT", 6379)),
    db=0,  # VCS Gateway uses DB 0
    password=os.getenv("REDIS_PASSWORD", None),
    decode_responses=True,  # Auto-decode bytes to strings
    socket_connect_timeout=1,  # 1 second connection timeout
    socket_timeout=2,  # 2 second read/write timeout
    retry_on_timeout=True,
    max_connections=50,  # Connection pool size
    health_check_interval=30  # Health check every 30s
)
```

**Cache Data Structures:**

1. **Tenant Config Cache (String):**
```json
# Key: tenant:config:{tenant_id}
# Value: JSON string
{
  "tenant_id": "uuid",
  "vcs_provider": "github",
  "webhook_secret": "secret123",
  "is_active": true,
  "repo_id": "987654321",
  "repo_name": "myorg/myrepo"
}
# TTL: 300 seconds (5 minutes)
```

2. **Idempotency Cache (String):**
```json
# Key: idempotency:{pr_hash_key}
# Value: JSON string with event metadata
{
  "event_id": "550e8400-e29b-41d4-a716-446655440000",
  "correlation_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "pr_hash_key": "abc123def456...",
  "pr_version": 1,
  "tenant_id": "uuid",
  "repo_id": "987654321",
  "pr_id": "42",
  "commit_sha": "abc123",
  "processing_status": "accepted",
  "cached_at": "2025-12-30T10:30:00Z"
}
# TTL: 72 hours (259200 seconds)
```

3. **PR Version Counter (Integer):**
```redis
# Key: pr:version:{tenant_id}:{repo_id}:{pr_id}
# Value: Integer (monotonic counter)
# Example: 1, 2, 3, 4, ...
# TTL: 30 days (2592000 seconds)
#
# Redis Commands:
# INCR pr:version:{tenant_id}:{repo_id}:{pr_id}  → Returns: 1 (first webhook)
# INCR pr:version:{tenant_id}:{repo_id}:{pr_id}  → Returns: 2 (second webhook)
# INCR pr:version:{tenant_id}:{repo_id}:{pr_id}  → Returns: 3 (third webhook)
# EXPIRE pr:version:{tenant_id}:{repo_id}:{pr_id} 2592000
```

4. **Policy Validation Cache (String - Future):**
```json
# Key: policy:pr_validation:{pr_hash_key}
# Value: JSON string
{
  "status": "duplicate",
  "should_process": false,
  "cached_at": "2025-12-27T10:30:00Z"
}
# TTL: 600 seconds (10 minutes)
```

**Circuit Breaker Configuration:**
- **Connection Timeout:** 1 second (fast fail if Redis down)
- **Operation Timeout:** 2 seconds (read/write)
- **Retry Strategy:** 3 retries with 100ms delay
- **Fallback Strategy:** If Redis unavailable, query DB directly (graceful degradation)

**Fallback Strategy:**
```python
def get_tenant_config(tenant_id: str) -> dict:
    try:
        # Try cache first
        cached = redis_client.get(f"tenant:config:{tenant_id}")
        if cached:
            return json.loads(cached)
    except (RedisError, ConnectionError) as e:
        logger.warning(f"Redis cache miss, falling back to DB: {e}")

    # Fallback to DB
    config = db.query(TenantVCSConfig).filter_by(tenant_id=tenant_id).first()

    # Try to populate cache (best effort)
    try:
        redis_client.setex(
            f"tenant:config:{tenant_id}",
            300,  # 5 min TTL
            json.dumps(config)
        )
    except RedisError:
        pass  # Ignore cache write failures

    return config
```

**Cache Invalidation Strategy:**
- **On Tenant Config Update:** Publish Redis pub/sub event `tenant:config:updated:{tenant_id}`
- **All VCS Gateway Instances:** Subscribe to pub/sub, evict local cache
- **Manual Invalidation:** Admin API endpoint `/admin/cache/invalidate/{tenant_id}`

**Monitoring Metrics:**
- **Cache Hit Rate:** Target > 95% (tenant config queries)
- **Cache Miss Rate:** Alert if > 10% (may indicate cache issues)
- **Redis Connection Errors:** Alert if > 1% of requests
- **Webhook Queue Depth:** Alert if > 1000 items (worker lag)
- **Webhook Queue Processing Time:** p95 < 100ms per item

**SLA:**
- **Expected Uptime:** 99.9% (Redis cluster HA)
- **Expected Latency:** p95 < 2ms (cache GET operations)

---

### Redis Cache Usage Examples

#### Example 1: Generate PR Version (Monotonic Counter)

**Scenario:** New webhook received for PR #42, need to generate version number

**Redis Operations:**
```redis
# Generate version for first webhook
INCR pr:version:tenant-uuid:repo-123:pr-42
# → Returns: 1

EXPIRE pr:version:tenant-uuid:repo-123:pr-42 2592000
# → Set 30-day TTL

# Second webhook for same PR (synchronize)
INCR pr:version:tenant-uuid:repo-123:pr-42
# → Returns: 2

# Third webhook for same PR (synchronize)
INCR pr:version:tenant-uuid:repo-123:pr-42
# → Returns: 3
```

**Usage in Code:**
```text
1. Receive webhook for PR #42
2. Generate pr_hash_key: SHA256(tenant_id:repo_id:pr_id:commit_sha:action)
3. Call Redis INCR to get monotonic version → pr_version = 1
4. Store in DB: inbound_event (pr_version = 1)
5. Publish to queue with header: "pr_version": "1"
```

---

#### Example 2: Idempotency Check with pr_version

**Scenario:** Check if webhook already processed

**Redis Operations:**
```redis
# Check if duplicate
GET idempotency:abc123def456...
# → Returns: {"event_id": "uuid", "pr_version": 1, "processing_status": "accepted", ...}
# → Duplicate detected! Return 200 OK

# If cache miss, query DB
SELECT * FROM inbound_event WHERE pr_hash_key = 'abc123def456...'
# → If found: Write-back to Redis
SET idempotency:abc123def456... '{"event_id": "uuid", "pr_version": 1, ...}'
EXPIRE idempotency:abc123def456... 259200
```

**Cache Write-Back (After DB Insert):**
```redis
# After successful webhook processing, cache the event
SET idempotency:abc123def456... '{
  "event_id": "550e8400-e29b-41d4-a716-446655440000",
  "correlation_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "pr_hash_key": "abc123def456...",
  "pr_version": 1,
  "tenant_id": "tenant-uuid",
  "repo_id": "987654321",
  "pr_id": "42",
  "commit_sha": "abc123",
  "processing_status": "accepted",
  "cached_at": "2025-12-30T10:30:00Z"
}'
EXPIRE idempotency:abc123def456... 259200
```

---

#### Example 3: Tenant Config Cache

**Scenario:** Validate webhook signature, need tenant secret

**Redis Operations:**
```redis
# Try cache first
GET tenant:config:tenant-uuid
# → Returns: {"tenant_id": "uuid", "webhook_secret": "secret123", ...}
# → Cache hit! Use secret for HMAC validation

# If cache miss
GET tenant:config:tenant-uuid
# → Returns: nil
# → Query DB, then write-back
SET tenant:config:tenant-uuid '{"tenant_id": "uuid", "webhook_secret": "secret123", ...}'
EXPIRE tenant:config:tenant-uuid 300
```

---

#### Example 4: Complete Webhook Processing Flow

**Step-by-step with Redis:**

```text
1️⃣ STEP 1: Tenant Config Lookup
   Redis: GET tenant:config:tenant-uuid
   → Cache hit (2ms) → Use config

2️⃣ STEP 2: Generate pr_hash_key
   SHA256(github:tenant-uuid:repo-123:pr-42:github.com:synchronize:abc123)
   → pr_hash_key = "def456..."

3️⃣ STEP 3: Idempotency Check
   Redis: GET idempotency:def456...
   → Cache miss → Query DB
   → Not found (new webhook)

4️⃣ STEP 4: Generate PR Version
   Redis: INCR pr:version:tenant-uuid:repo-123:pr-42
   → Returns: 2 (second webhook for this PR)
   Redis: EXPIRE pr:version:tenant-uuid:repo-123:pr-42 2592000

5️⃣ STEP 5: Insert to DB
   INSERT INTO inbound_event (..., pr_version = 2, pr_hash_key = 'def456...')

6️⃣ STEP 6: Cache Write-Back
   Redis: SET idempotency:def456... '{"event_id": "...", "pr_version": 2, ...}'
   Redis: EXPIRE idempotency:def456... 259200

7️⃣ STEP 7: Publish to Queue
   Publish to vcs.webhook.received
   Headers: {"pr_hash_key": "def456...", "pr_version": "2", ...}

8️⃣ STEP 8: Return 202 Accepted
```
- **Queue Processing:** p95 < 100ms (background webhook processing)

**High Availability:**
- **Redis Cluster:** 3-node cluster (1 master + 2 replicas)
- **Sentinel:** Redis Sentinel for automatic failover
- **Persistence:** RDB snapshots + AOF (Append-Only File)
- **Backup:** Daily backups to S3 (cache can be rebuilt from DB if lost)

**Security:**
- **Authentication:** Redis password required (REDIS_PASSWORD env var)
- **Network:** Redis accessible only from VCS Gateway pods (NetworkPolicy)
- **Encryption:** TLS enabled for Redis connections (optional, recommended for prod)

---

### Service Dependencies

#### Dependency: Policy Engine Service

**Purpose:** Pre-flight quota checks to prevent cost overruns

**Integration Type:** REST API (synchronous HTTP call)

**Endpoints Used:**
- `POST /quota/check`: Verify tenant has available quota before processing PR

> **IMPORTANT:** VCS Gateway Service performs idempotency and stale detection **internally** using Cache-Aside pattern (Redis → DB). Policy Engine Service is NOT involved in duplicate/stale checks.

**Request Example (Quota Check):**
```json
{
  "tenant_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "request_type": "pr_analysis",
  "estimated_cost_usd": 0.001
}
```

**Response Example (Quota Check):**
```json
{
  "status": "approved",
  "quota_available": true,
  "remaining_calls": 150,
  "remaining_cost_usd": 45.50
}
```

**Circuit Breaker Configuration:**
- **Failure Threshold:** 5 failures in 10 seconds
- **Timeout:** 3 seconds (fast response required)
- **Half-Open Retry:** After 60 seconds

**Fallback Strategy:**
- **If Policy Engine Service is down:** Accept webhook anyway, log warning
- **Reason:** Better to process and potentially exceed quota than lose webhook
- **Mitigation:** Monitor quota service health, alert on-call

**SLA:**
- **Expected Uptime:** 99.9%
- **Expected Latency:** p95 < 50ms (in-memory quota checks)

---

## 9. Configuration

### Environment Variables

| Variable | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `DATABASE_URL` | string | Yes | - | PostgreSQL connection string (schema: vcs_gateway_schema) |
| `RABBITMQ_HOST` | string | Yes | - | RabbitMQ broker host |
| `RABBITMQ_PORT` | integer | No | 5672 | RabbitMQ broker port |
| `RABBITMQ_USER` | string | Yes | - | RabbitMQ username |
| `RABBITMQ_PASSWORD` | string | Yes | - | RabbitMQ password |
| `QUOTA_SERVICE_URL` | string | Yes | - | Policy Engine Service API base URL (http://quota-service:8080) |
| `QUOTA_SERVICE_TIMEOUT` | integer | No | 3 | Policy Engine Service API timeout (seconds) |
| `REDIS_HOST` | string | Yes | - | Redis host for caching (tenant config + idempotency) |
| `REDIS_PORT` | integer | No | 6379 | Redis port |
| `REDIS_PASSWORD` | string | No | - | Redis authentication password (required for prod) |
| `REDIS_DB` | integer | No | 0 | Redis database number (VCS Gateway uses DB 0) |
| `REDIS_SOCKET_TIMEOUT` | integer | No | 2 | Redis socket timeout (seconds) |
| `REDIS_SOCKET_CONNECT_TIMEOUT` | integer | No | 1 | Redis connection timeout (seconds) |
| `CACHE_TTL_SECONDS` | integer | No | 300 | Cache TTL for tenant_vcs_config (5 minutes) |
| `WEBHOOK_QUEUE_ENABLED` | boolean | No | true | Enable Fire-and-Forget webhook queue (Database-based) |
| `API_PORT` | integer | No | 8000 | HTTP server port |
| `LOG_LEVEL` | string | No | INFO | Logging level (DEBUG, INFO, WARN, ERROR) |
| `ENABLE_TRACING` | boolean | No | false | Enable OpenTelemetry tracing |
| `ENABLE_PR_DEDUPLICATION` | boolean | No | true | Enable PR hash deduplication |
| `ENABLE_QUOTA_CHECK` | boolean | No | true | Enable quota pre-flight check |

**Example `.env` file:**
```env
# Database
DATABASE_URL=postgresql://vcs_gateway_user:password@postgres:5432/devgrowth

# RabbitMQ
RABBITMQ_HOST=rabbitmq
RABBITMQ_PORT=5672
RABBITMQ_USER=guest
RABBITMQ_PASSWORD=guest

# Policy Engine Service
QUOTA_SERVICE_URL=http://quota-service:8080
QUOTA_SERVICE_TIMEOUT=3

# Redis Cache (REQUIRED - For idempotency cache only)
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=your_redis_password_here
REDIS_DB=0
REDIS_SOCKET_TIMEOUT=2
REDIS_SOCKET_CONNECT_TIMEOUT=1
REDIS_IDEMPOTENCY_TTL_HOURS=72

# Feature Flags
ENABLE_PR_DEDUPLICATION=true
ENABLE_QUOTA_CHECK=true

# Observability
LOG_LEVEL=INFO
ENABLE_TRACING=true
```

**Important Notes:**
- ⚠️ **Webhook secrets are NOT stored in .env** - They are stored per-tenant in `shared_schema.tenant_vcs_config` table
- Each tenant can have multiple repositories with different webhook secrets
- Secrets are looked up from database during webhook validation
- Optional: Redis caching reduces database lookups for tenant VCS configs

---

## 10. Error Handling

### Error Categories

#### 1. Validation Errors (4xx)

**Cause:** Invalid webhook payload or signature

**Handling:**
- Return 400 Bad Request with detailed error message
- Log at WARN level (not ERROR - expected in production)
- Do NOT retry

**Examples:**
```json
// Invalid signature
{
  "error": {
    "code": "INVALID_SIGNATURE",
    "message": "Webhook signature validation failed"
  }
}

// Missing required field
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid webhook payload",
    "details": {"field": "pull_request.id", "error": "Field required"}
  }
}
```

---

#### 2. Transient Errors (5xx)

**Cause:** Database timeout, RabbitMQ connection failure

**Handling:**
- Retry 3 times with exponential backoff (1s, 2s, 4s)
- Return 503 Service Unavailable
- Log at ERROR level
- Alert if sustained (> 5 minutes)

**Examples:**
- Database connection pool exhausted
- RabbitMQ broker unreachable
- Policy Engine Service timeout

---

#### 3. Dependency Errors

**Cause:** Policy Engine Service unavailable

**Handling:**
- Circuit breaker trips after 5 failures
- **Fallback:** Accept webhook, log warning (process later)
- Return 200 OK (graceful degradation)
- Alert immediately

**Reasoning:** Better to process and potentially exceed quota than lose webhook

---

### Error Logging Format

```json
{
  "timestamp": "2024-12-23T10:30:00.123Z",
  "level": "ERROR",
  "service": "vcs-gateway",
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "error": {
    "type": "DatabaseConnectionError",
    "message": "Connection pool exhausted",
    "stack_trace": "...",
    "context": {
      "operation": "insert_inbound_event",
      "pool_size": 20,
      "active_connections": 20
    }
  }
}
```

---

## 11. Monitoring & Observability

### Key Metrics

#### Application Metrics

| Metric | Type | Description | Alert Threshold |
|--------|------|-------------|-----------------|
| `webhook_requests_total` | Counter | Total webhook requests by VCS provider | - |
| `webhook_requests_duration_seconds` | Histogram | Webhook processing latency (p50, p95, p99) | p95 > 500ms |
| `webhook_validation_errors_total` | Counter | Invalid signature or payload errors | - |
| `pr_duplicates_detected_total` | Counter | Duplicate PR commits detected | > 50% of total |
| `quota_rejections_total` | Counter | PR events rejected due to quota | > 20% of total |
| `outbox_events_pending` | Gauge | Unpublished outbox events | > 100 |
| `quota_service_errors_total` | Counter | Policy Engine Service API failures | > 10/min |
| `database_query_duration_seconds` | Histogram | Database query latency | p95 > 100ms |

#### Business Metrics

| Metric | Type | Description | Alert Threshold |
|--------|------|-------------|-----------------|
| `pr_events_accepted_total` | Counter | PR events accepted for processing | - |
| `pr_events_rejected_total` | Counter | PR events rejected (quota, validation) | - |
| `active_tenants_gauge` | Gauge | Number of active tenants in last hour | - |

---

### Health Checks

#### Liveness Probe
**Endpoint:** `GET /health/live`

**Purpose:** Is the service running? (Kubernetes will restart if fails)

**Implementation:**
```python
@app.get("/health/live")
async def liveness():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
```

---

#### Readiness Probe
**Endpoint:** `GET /health/ready`

**Purpose:** Is the service ready to accept traffic?

**Checks:**
1. **Database Connection:** Can execute `SELECT 1`
2. **RabbitMQ Connection:** Channel is open
3. **Policy Engine Service:** Reachable (optional - degraded mode if down)

**Implementation:**
```python
@app.get("/health/ready")
async def readiness():
    checks = {}

    # Check database
    try:
        await db.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = "error"
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "checks": checks, "error": str(e)}
        )

    # Check RabbitMQ
    try:
        if rabbitmq.channel.is_open:
            checks["rabbitmq"] = "ok"
        else:
            raise Exception("Channel closed")
    except Exception as e:
        checks["rabbitmq"] = "error"
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "checks": checks, "error": str(e)}
        )

    # Check Policy Engine Service (optional)
    try:
        response = await httpx.get(f"{QUOTA_SERVICE_URL}/health", timeout=1)
        checks["quota_service"] = "ok" if response.status_code == 200 else "degraded"
    except:
        checks["quota_service"] = "degraded"  # Not critical

    return {"status": "ready", "checks": checks, "timestamp": datetime.utcnow().isoformat()}
```

---

### Logging Strategy

**Log Levels:**
- **DEBUG:** Request/response payloads (disabled in production)
- **INFO:** Webhook received, accepted, duplicate detected
- **WARN:** Quota rejected, Policy Engine Service unavailable (fallback mode)
- **ERROR:** Database errors, RabbitMQ publish failures
- **FATAL:** Service cannot start (missing config, DB unreachable)

**Structured Logging Example:**
```json
{
  "timestamp": "2024-12-23T10:30:00.123Z",
  "level": "INFO",
  "service": "vcs-gateway",
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "message": "Webhook accepted for processing",
  "context": {
    "vcs_provider": "github",
    "pr_id": "42",
    "repo_name": "org/repo",
    "processing_status": "accepted",
    "duration_ms": 45
  }
}
```

---

### Distributed Tracing

**Trace Spans:**
1. **webhook_processing** (root span)
   - Duration: Full webhook processing time
   - Attributes: vcs_provider, tenant_id, pr_id

2. **signature_validation**
   - Duration: HMAC verification time
   - Attributes: vcs_provider, valid (true/false)

3. **pr_deduplication_check**
   - Duration: Database lookup time
   - Attributes: duplicate (true/false), pr_hash

4. **quota_service_call**
   - Duration: API call time
   - Attributes: status (approved/rejected), timeout (true/false)

5. **database_transaction**
   - Duration: Transaction commit time
   - Attributes: tables_inserted (inbound_event, outbox_event)

6. **rabbitmq_publish**
   - Duration: Message publish time
   - Attributes: routing_key, message_size_bytes

**Example Trace:**
```
Trace ID: abc123def456
├─ webhook_processing [0-850ms] ⬅️ Root span
   ├─ signature_validation [0-5ms]
   ├─ pr_deduplication_check [10-25ms]
   ├─ quota_service_call [30-75ms] ⚠️ Slow!
   ├─ database_transaction [80-120ms]
   │  ├─ INSERT inbound_event [85-95ms]
   │  └─ INSERT outbox_event [100-110ms]
   └─ rabbitmq_publish [800-845ms]
```

---

## 12. Performance Considerations

### Expected Load

| Metric | Value | Notes |
|--------|-------|-------|
| **Peak Webhooks/sec** | 1000 | During business hours (8am-6pm) |
| **Average Webhooks/sec** | 100 | Off-peak hours |
| **Average Response Time** | 50ms | p50 (median) |
| **p95 Response Time** | 200ms | Including Policy Engine Service call |
| **p99 Response Time** | 500ms | Including database spikes |

### Scaling Strategy

**Horizontal Scaling:**
- **Trigger:** CPU > 70% for 5 minutes
- **Target:** 50-70% CPU utilization
- **Min Replicas:** 2 (high availability)
- **Max Replicas:** 20 (cost limit)

**Database Connection Pooling:**
```python
# SQLAlchemy AsyncEngine
engine = create_async_engine(
    DATABASE_URL,
    pool_size=10,  # Min connections per pod
    max_overflow=10,  # Max additional connections (total 20)
    pool_timeout=30,  # Wait 30s for connection
    pool_recycle=3600,  # Recycle connections every hour
    pool_pre_ping=True  # Verify connection before use
)
```

- **Total Connections (20 pods):** 400 connections max
- **PostgreSQL Max Connections:** 500 (buffer for other services)

**RabbitMQ Connection Pooling:**
```python
# Single connection per pod, multiple channels
connection = await aio_pika.connect_robust(
    RABBITMQ_URL,
    heartbeat=60,
    connection_attempts=3,
    retry_delay=5
)

# Channel per request (lightweight)
channel = await connection.channel()
```

---

### Caching Strategy

**Cache Layer:** Redis (optional - not critical for MVP)

**Cached Data:**
1. **Tenant Lookup by Repository ID** (TTL: 1 hour)
   ```python
   # Cache key: tenant:repo:{repo_id}
   # Value: {tenant_id: "uuid", webhook_secret: "..."}
   cache.set(f"tenant:repo:{repo_id}", tenant_data, ttl=3600)
   ```

2. **Duplicate PR Hash Checks** (TTL: 24 hours)
   ```python
   # Cache key: pr:hash:{pr_hash}
   # Value: {analysis_id: "uuid", analyzed_at: "2024-12-23T10:30:00Z"}
   cache.set(f"pr:hash:{pr_hash}", analysis_data, ttl=86400)
   ```

**Cache Invalidation:**
- **Tenant changes:** Explicit invalidation on tenant config update
- **PR re-analysis:** Explicit invalidation when user requests re-analysis
- **TTL expiration:** Automatic after 1-24 hours

**Cache Miss Handling:**
- If Redis unavailable: Fall back to database lookup (slower but functional)
- Log cache misses for monitoring

---

### Performance Optimizations

1. **Database Query Optimization**
   - Use indexes on `(tenant_id, pr_commit_hash)` for deduplication
   - Use partial index on `outbox_event WHERE published = FALSE`
   - Prepare statements for frequently-used queries

2. **Async I/O**
   - All database, HTTP, and queue operations are async (non-blocking)
   - Use `asyncio.gather()` for parallel operations (if applicable)

3. **Payload Size Optimization**
   - Store only normalized payload in `inbound_event.normalized_payload` (not full diff)
   - Full PR diff fetched later by LLM Service from `diff_url`
   - Reduces database storage and queue message size

4. **Outbox Publisher Batching**
   - Publish up to 100 events per batch (every 1 second)
   - Reduces RabbitMQ round-trips

---

## 13. Security

### Authentication & Authorization

**Webhook Authentication:**

1. **GitHub: HMAC-SHA256 Signature**
   ```python
   def verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
       expected_signature = hmac.new(
           secret.encode(),
           payload,
           hashlib.sha256
       ).hexdigest()
       return hmac.compare_digest(f"sha256={expected_signature}", signature)
   ```

2. **GitLab: Token Validation**
   ```python
   def verify_gitlab_token(token: str, expected_token: str) -> bool:
       return hmac.compare_digest(token, expected_token)
   ```

3. **Bitbucket: HMAC-SHA256 Signature**
   (Similar to GitHub)

---

### Data Security

**Sensitive Data Fields:**
- Webhook secrets (encrypted in Kubernetes Secrets)
- Raw webhook payloads may contain email addresses (PII)

**Encryption:**
- **At Rest:** Kubernetes Secrets (base64 encoded - TODO: Use Vault for encryption)
- **In Transit:** TLS 1.3 for all webhook ingress (Kong terminates SSL)

**Data Masking:**
```python
# Log sanitization
def sanitize_payload(payload: dict) -> dict:
    """Mask email addresses in logs"""
    sanitized = payload.copy()
    if "user" in sanitized and "email" in sanitized["user"]:
        email = sanitized["user"]["email"]
        sanitized["user"]["email"] = f"{email[0]}***@***.{email.split('.')[-1]}"
    return sanitized

logger.info("Webhook received", payload=sanitize_payload(raw_payload))
```

---

### Security Best Practices

- ✅ **Input validation:** Pydantic models for all webhook schemas
- ✅ **SQL injection prevention:** SQLAlchemy ORM (parameterized queries)
- ✅ **Rate limiting:** Kong plugin (100 req/min per tenant)
- ✅ **CORS:** Not applicable (webhook-only, no browser clients)
- ✅ **Security headers:** Kong response-transformer plugin (HSTS, X-Frame-Options)
- ✅ **Dependency scanning:** `safety check` in CI/CD
- ✅ **Secrets in Vault:** **TODO (Post-MVP)** - Currently in Kubernetes Secrets

---

## 14. Testing Strategy

### Unit Tests

**Coverage Target:** 85%+ line coverage

**Test Categories:**
- Signature validation (GitHub, GitLab, Bitbucket)
- Payload normalization (VCS-specific → platform format)
- PR hash calculation
- Error handling (invalid signature, missing fields)

**Example:**
```python
# tests/unit/test_signature_validation.py
import pytest
from services.vcs_gateway.validators import GitHubSignatureValidator

def test_github_signature_valid():
    # Given
    payload = b'{"pr_id": 123}'
    secret = "test-secret"
    validator = GitHubSignatureValidator(secret)

    # Calculate expected signature
    import hmac, hashlib
    expected_sig = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()

    # When
    result = validator.verify(payload, expected_sig)

    # Then
    assert result is True

def test_github_signature_invalid():
    # Given
    payload = b'{"pr_id": 123}'
    secret = "test-secret"
    validator = GitHubSignatureValidator(secret)

    # When
    result = validator.verify(payload, "sha256=invalid")

    # Then
    assert result is False
```

---

### Integration Tests

**Test Categories:**
- Full webhook processing flow (signature → DB → queue)
- PR deduplication (database lookup)
- Policy Engine Service integration (mocked API)
- Outbox pattern (transaction commit + queue publish)

**Example:**
```python
# tests/integration/test_webhook_flow.py
import pytest
from httpx import AsyncClient
from main import app

@pytest.mark.asyncio
async def test_github_webhook_accepted(test_db, test_queue):
    """Test GitHub webhook accepted flow"""
    # Given
    webhook_payload = {
        "action": "opened",
        "pull_request": {"id": 123, "number": 42, ...},
        "repository": {"id": 456, "full_name": "org/repo"}
    }

    signature = generate_github_signature(webhook_payload, WEBHOOK_SECRET)

    async with AsyncClient(app=app, base_url="http://test") as client:
        # When
        response = await client.post(
            "/webhooks/github",
            json=webhook_payload,
            headers={"X-Hub-Signature-256": signature}
        )

    # Then
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"

    # Verify database insert
    event = await test_db.get_inbound_event(data["event_id"])
    assert event.pr_id == "42"
    assert event.processing_status == "accepted"

    # Verify queue message
    message = await test_queue.receive("repo.change.detected", timeout=5)
    assert message["data"]["pr_id"] == "42"

@pytest.mark.asyncio
async def test_duplicate_pr_detected(test_db):
    """Test PR deduplication"""
    # Given - Insert existing PR analysis
    pr_hash = "abc123def456"
    await test_db.insert_pr_analysis(pr_commit_hash=pr_hash, tenant_id="tenant-1")

    webhook_payload = {...}  # Same PR

    async with AsyncClient(app=app, base_url="http://test") as client:
        # When
        response = await client.post("/webhooks/github", json=webhook_payload)

    # Then
    assert response.status_code == 200
    assert response.json()["status"] == "duplicate"
```

---

### End-to-End Tests

**Test Scenarios:**
- Complete flow: Webhook → Queue → Orchestrator receives event
- Error scenarios: Invalid signature, quota exceeded
- Circuit breaker: Policy Engine Service down, webhook still accepted

**Example:**
```python
@pytest.mark.e2e
@pytest.mark.asyncio
async def test_webhook_to_orchestrator_flow():
    """
    E2E test: Webhook received → Event published → Orchestrator consumes

    Services running in Docker Compose:
    - vcs-gateway
    - quota-service
    - postgres
    - rabbitmq
    """
    async with AsyncClient(base_url="http://localhost:8000") as client:
        # Step 1: Send webhook
        response = await client.post(
            "/webhooks/github",
            json=SAMPLE_GITHUB_WEBHOOK,
            headers={"X-Hub-Signature-256": "..."}
        )

        assert response.status_code == 200
        correlation_id = response.json()["correlation_id"]

        # Step 2: Verify Orchestrator received event (check journey)
        for _ in range(30):  # Wait up to 30 seconds
            journey_response = await client.get(f"/api/v1/journey/{correlation_id}")
            steps = journey_response.json()["steps"]

            if any(step["service_name"] == "orchestrator" for step in steps):
                break

            await asyncio.sleep(1)
        else:
            pytest.fail("Orchestrator did not process event in 30 seconds")

        # Verify journey steps
        assert steps[0]["step_type"] == "webhook_received"
        assert steps[1]["step_type"] == "orchestration_started"
```

---

## 15. Deployment

### Deployment Architecture

**Environment:** Kubernetes

**Deployment Strategy:** Rolling Update (zero-downtime)

**Replicas:**
- **Development:** 1 pod
- **Staging:** 2 pods
- **Production:** Min 3 pods, Max 20 pods (HPA)

---

### Deployment Manifest

```yaml
# kubernetes/vcs-gateway-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vcs-gateway
  namespace: devgrowth-production
  labels:
    app: vcs-gateway
    version: v1.0.0
spec:
  replicas: 3
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0  # Zero-downtime deployments
  selector:
    matchLabels:
      app: vcs-gateway
  template:
    metadata:
      labels:
        app: vcs-gateway
        version: v1.0.0
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8000"
        prometheus.io/path: "/metrics"
    spec:
      containers:
      - name: vcs-gateway
        image: registry.gitlab.com/yourorg/vcs-gateway:v1.0.0
        ports:
        - containerPort: 8000
          name: http
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: vcs-gateway-secrets
              key: database-url
        - name: RABBITMQ_HOST
          value: "rabbitmq-service"
        - name: QUOTA_SERVICE_URL
          value: "http://quota-service:8080"
        - name: GITHUB_WEBHOOK_SECRET
          valueFrom:
            secretKeyRef:
              name: vcs-gateway-secrets
              key: github-webhook-secret
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /health/live
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
          timeoutSeconds: 5
          failureThreshold: 3
        readinessProbe:
          httpGet:
            path: /health/ready
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
          timeoutSeconds: 3
          failureThreshold: 2
        securityContext:
          runAsNonRoot: true
          runAsUser: 1000
          readOnlyRootFilesystem: true
          allowPrivilegeEscalation: false
---
apiVersion: v1
kind: Service
metadata:
  name: vcs-gateway-service
  namespace: devgrowth-production
spec:
  selector:
    app: vcs-gateway
  ports:
  - port: 80
    targetPort: 8000
    protocol: TCP
  type: ClusterIP
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: vcs-gateway-hpa
  namespace: devgrowth-production
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: vcs-gateway
  minReplicas: 3
  maxReplicas: 20
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
```

---

### Deployment Process

**GitLab CI Pipeline:**

```yaml
# .gitlab-ci.yml
deploy:production:
  stage: deploy
  image: bitnami/kubectl:latest
  script:
    # Run database migrations
    - kubectl run migration-$CI_COMMIT_SHORT_SHA --image=registry.gitlab.com/yourorg/vcs-gateway:$CI_COMMIT_SHA --command -- alembic upgrade head -n devgrowth-production

    # Update deployment
    - kubectl set image deployment/vcs-gateway vcs-gateway=registry.gitlab.com/yourorg/vcs-gateway:$CI_COMMIT_SHA -n devgrowth-production

    # Wait for rollout
    - kubectl rollout status deployment/vcs-gateway -n devgrowth-production --timeout=10m

    # Verify health
    - kubectl exec deployment/vcs-gateway -n devgrowth-production -- curl -f http://localhost:8000/health/ready || kubectl rollout undo deployment/vcs-gateway -n devgrowth-production
  environment:
    name: production
    url: https://api.devgrowth.io
  when: manual  # Requires approval
  only:
    - main
```

---

### Rollback Strategy

**Automatic Rollback Triggers:**
- Error rate > 5% for 2 minutes
- p95 latency > 2 seconds for 2 minutes
- Readiness probe failures > 50%

**Manual Rollback:**
```bash
# View rollout history
kubectl rollout history deployment/vcs-gateway -n devgrowth-production

# Rollback to previous version
kubectl rollout undo deployment/vcs-gateway -n devgrowth-production

# Rollback to specific revision
kubectl rollout undo deployment/vcs-gateway --to-revision=3 -n devgrowth-production
```

---

## 16. Runbook

### Common Issues & Solutions

#### Issue 1: High Latency (p95 > 500ms)

**Symptoms:**
- Slow webhook responses
- Queue backlog growing (outbox_event unpublished > 100)

**Diagnosis:**
```bash
# Check database connection pool exhaustion
kubectl logs -l app=vcs-gateway -n devgrowth-production | grep "pool exhausted"

# Check slow queries
kubectl exec -it postgres-pod -- psql -U postgres -d devgrowth -c "
  SELECT query, mean_exec_time
  FROM pg_stat_statements
  WHERE query LIKE '%inbound_event%'
  ORDER BY mean_exec_time DESC
  LIMIT 10;
"

# Check Policy Engine Service latency
kubectl logs -l app=vcs-gateway | grep "quota_service_call" | jq '.duration_ms'
```

**Resolution:**
- **Short-term:** Increase connection pool size, scale up replicas
  ```bash
  kubectl scale deployment vcs-gateway --replicas=10 -n devgrowth-production
  ```
- **Long-term:** Optimize slow queries, add missing indexes, cache tenant lookups in Redis

---

#### Issue 2: Policy Engine Service Unavailable (Circuit Breaker Open)

**Symptoms:**
- Logs show "Quota service circuit breaker open"
- All webhooks accepted (fallback mode)
- Alert: "Quota checks bypassed"

**Diagnosis:**
```bash
# Check Policy Engine Service health
kubectl get pods -l app=quota-service -n devgrowth-production
kubectl logs -l app=quota-service -n devgrowth-production --tail=100

# Check network connectivity
kubectl exec -it vcs-gateway-pod -- curl http://quota-service:8080/health
```

**Resolution:**
- **Root cause:** Policy Engine Service pod crashed or overwhelmed
- **Action:** Restart Policy Engine Service, investigate crash
  ```bash
  kubectl rollout restart deployment/quota-service -n devgrowth-production
  ```
- **Mitigation:** VCS Gateway continues accepting webhooks (graceful degradation)

---

#### Issue 3: RabbitMQ Publish Failures (Outbox Backlog)

**Symptoms:**
- `outbox_events_pending` metric > 100
- Webhooks accepted but events not reaching Orchestrator

**Diagnosis:**
```bash
# Check outbox backlog
kubectl exec -it postgres-pod -- psql -U postgres -d devgrowth -c "
  SELECT COUNT(*) FROM vcs_gateway_schema.outbox_event WHERE published = FALSE;
"

# Check RabbitMQ connection
kubectl logs -l app=vcs-gateway | grep "RabbitMQ connection"

# Check RabbitMQ queue depth
kubectl exec -it rabbitmq-pod -- rabbitmqctl list_queues name messages
```

**Resolution:**
- **Short-term:** Restart VCS Gateway pods (re-establish RabbitMQ connection)
  ```bash
  kubectl rollout restart deployment/vcs-gateway -n devgrowth-production
  ```
- **Long-term:** Implement RabbitMQ connection retry logic with exponential backoff

---

### Operational Procedures

#### Procedure: Add New VCS Provider (e.g., Azure DevOps)

**Steps:**

1. **Create Normalizer Class**
   ```python
   # services/vcs_gateway/normalizers/azure_devops.py
   class AzureDevOpsNormalizer(WebhookNormalizer):
       def normalize(self, payload: dict) -> dict:
           return {
               "pr_id": str(payload["resource"]["pullRequestId"]),
               "repo_id": str(payload["resource"]["repository"]["id"]),
               ...
           }
   ```

2. **Register in Factory**
   ```python
   # services/vcs_gateway/normalizers/factory.py
   class WebhookNormalizerFactory:
       @staticmethod
       def create(vcs_provider: str) -> WebhookNormalizer:
           if vcs_provider == "azure_devops":
               return AzureDevOpsNormalizer()
           ...
   ```

3. **Add Webhook Endpoint**
   ```python
   @app.post("/webhooks/azure-devops")
   async def azure_devops_webhook(request: Request):
       # Validate signature
       # Process webhook
       ...
   ```

4. **Add Tests**
   ```python
   # tests/unit/test_azure_devops_normalizer.py
   def test_azure_devops_normalization():
       ...
   ```

5. **Deploy**
   ```bash
   git commit -m "Add Azure DevOps support"
   git push origin main
   # Deploy via GitLab CI
   ```

---

#### Procedure: Database Migration (Schema Change)

**When:** Adding new column to `inbound_event` table

**Steps:**

1. **Create Migration (Alembic)**
   ```bash
   # In development environment
   alembic revision -m "Add rejection_details column to inbound_event"
   ```

2. **Edit Migration File**
   ```python
   # alembic/versions/001_add_rejection_details.py
   def upgrade():
       op.add_column(
           'inbound_event',
           sa.Column('rejection_details', sa.JSONB, nullable=True),
           schema='vcs_gateway_schema'
       )

   def downgrade():
       op.drop_column('inbound_event', 'rejection_details', schema='vcs_gateway_schema')
   ```

3. **Test Migration Locally**
   ```bash
   alembic upgrade head  # Apply migration
   alembic downgrade -1  # Test rollback
   alembic upgrade head  # Re-apply
   ```

4. **Deploy to Staging**
   ```bash
   kubectl run migration-staging --image=registry.gitlab.com/yourorg/vcs-gateway:latest --command -- alembic upgrade head -n devgrowth-staging
   ```

5. **Deploy to Production (Maintenance Window)**
   ```bash
   # Production migration runs in GitLab CI before deployment
   # See .gitlab-ci.yml deploy:production job
   ```

---

### Emergency Contacts

| Role | Name | Contact | Backup |
|------|------|---------|--------|
| On-call Engineer | [Name] | [Email/Phone] | [Backup Name] |
| Service Owner | [Name] | [Email] | - |
| Database Admin | [Name] | [Email/Phone] | [Backup Name] |

---

## Appendix

### Glossary

| Term | Definition |
|------|------------|
| **Correlation ID** | Unique identifier tracking a PR event across all services |
| **Outbox Pattern** | Ensures atomic database write + queue publish (transactional) |
| **PR Hash** | SHA256 hash of `repo_id + pr_id + commit_sha` for deduplication |
| **Fat Message** | Queue message with full payload (reduces downstream DB reads) |
| **Thin Message** | Queue message with only IDs (consumer fetches data from DB) |
| **Circuit Breaker** | Pattern preventing cascading failures from dependency outages |

### References

- [Data Flow V2](../Architecture/DATA_FLOW_V2.md)
- [Tech Stack](./TECH_STACK.md)
- [Policy Engine Service Documentation](./quota_service.md) (TODO)
- [Orchestrator Service Documentation](./orchestrator_service.md) (TODO)

---

## Change Log

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.0 | 2024-12-23 | Platform Team | Initial documentation with MVP features (PR dedup, quota checks, idempotency) |

---

**Document Status:** ✅ Ready for Implementation
**Next Review Date:** 2025-01-23

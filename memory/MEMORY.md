# VCS Gateway — Service Memory

## Service Identity
- **Service:** VCS Gateway
- **Pipeline position:** [External VCS Providers] → **VCS GATEWAY** → VCS Worker
- **Port:** 8001
- **Queues consumed:** None (API-only service, no queue consumption)
- **Queues published:**
  - `vcs.webhook.received` — via Outbox pattern (reliable, transactional)
  - `journey.events` — via BasePublisher (fire-and-forget, journey tracking)
- **DB schema:** `vcs_gateway_schema`

## Architecture Decisions
- Cache-Aside idempotency: Redis first → DB miss → write-back (TTL 72h)
- Outbox pattern for `vcs.webhook.received` (critical — must not lose)
- Fire-and-forget for journey events (loss acceptable, loose coupling)
- worker.py exists but is NOT used — this is an API-only service
- Tenant webhook secrets stored per-tenant in `tenant` table (not global env var)

## Key Tables
- `tenant` — tenant_id (UUID PK), name, is_active, webhook_secret, plan_type
- `inbound_event` — id, tenant_id, vcs_provider, repo_id, pr_id, commit_sha, pr_hash_key (UNIQUE), correlation_id, raw_payload, status, created_at
- `outbox_event` — standard outbox table

## PR Hash Key Formula
`SHA256(f"{vcs_provider}:{tenant_id}:{repo_id}:{pr_id}:{vcs_instance_id}:{action}:{commit_sha}")`

## Webhook Signature Validation
- GitHub: `X-Hub-Signature-256` header, HMAC-SHA256 of body with tenant webhook_secret
- GitLab: `X-Gitlab-Token` header, compare directly with tenant webhook_secret
- Bitbucket: `X-Hub-Signature` header (same as GitHub pattern)

## Known Patterns
<!-- Claude will populate as implementation progresses -->

## Debugging Insights
<!-- Issues encountered and how they were resolved -->

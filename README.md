# [VCS Gateway]


> Part of the **Developer Growth Intelligence Platform** microservice architecture.

## What This Service Does

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


**Pipeline position:** [Previous Service] → **[THIS SERVICE]** → [Next Service]

---

## Quick Start

```bash
# 1. Clone and setup
git clone <repo-url>
cd vcs-gateway
bash scripts/dev-setup.sh

# 2. Run API server
uv run uvicorn vcs_gateway.main:app --reload

# 3. Run queue worker (if applicable)
uv run python -m vcs_gateway.worker

# 4. Check health
bash scripts/check-health.sh
```

## Common Commands

| Command | Description |
|---------|-------------|
| `uv sync` | Install dependencies |
| `uv run pytest` | Run all tests |
| `uv run pytest -m unit` | Unit tests only |
| `uv run pytest -m integration` | Integration tests only |
| `uv run ruff check src/ --fix` | Lint and auto-fix |
| `uv run mypy src/` | Type check |
| `uv run alembic upgrade head` | Apply DB migrations |
| `docker compose up -d` | Start infrastructure |

## Architecture

See [service documentation](docs/) and [DATA_FLOW_V2.md](../ai_dev_performance/NEW_MICROSERVICES/focus/SERVICE_DETAILS/Architecture/DATA_FLOW_V2.md).

## Environment Variables

Copy `.env.template` to `.env.local` and fill in values.
See `.env.template` for full documentation of all variables.

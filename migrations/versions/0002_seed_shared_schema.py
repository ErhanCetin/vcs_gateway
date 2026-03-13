"""Seed shared_schema tables for local development.

In production, shared_schema is bootstrapped by the platform migration pipeline.
All DDL uses IF NOT EXISTS and all INSERTs use ON CONFLICT DO NOTHING,
so this migration is safe to run in any environment.

Revision ID: 0002
Revises: 0001
Create Date: 2026-01-01 00:00:00.000001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS shared_schema")

    # ------------------------------------------------------------------
    # customer
    # ------------------------------------------------------------------
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

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_customer_email
        ON shared_schema.customer (contact_email)
    """)

    # ------------------------------------------------------------------
    # tenant
    # ------------------------------------------------------------------
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

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tenant_customer
        ON shared_schema.tenant (customer_id)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tenant_slug
        ON shared_schema.tenant (slug)
    """)

    # ------------------------------------------------------------------
    # tenant_vcs_config — owned by VCS Gateway (upsert on first webhook)
    # ------------------------------------------------------------------
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

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tenant_vcs_config_tenant
        ON shared_schema.tenant_vcs_config (tenant_id)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tenant_vcs_config_lookup
        ON shared_schema.tenant_vcs_config (tenant_id, vcs_provider, repo_id)
        WHERE is_active = TRUE
    """)

    # ------------------------------------------------------------------
    # vcs_event_whitelist
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Seed: event whitelist (GitHub + GitLab)
    # ------------------------------------------------------------------
    op.execute("""
        INSERT INTO shared_schema.vcs_event_whitelist (vcs_provider, event_type, event_action)
        VALUES
            ('github', 'pull_request',       'opened'),
            ('github', 'pull_request',       'synchronize'),
            ('github', 'pull_request',       'reopened'),
            ('gitlab', 'Merge Request Hook', 'open'),
            ('gitlab', 'Merge Request Hook', 'update'),
            ('gitlab', 'Merge Request Hook', 'reopen')
        ON CONFLICT (vcs_provider, event_type, event_action) DO NOTHING
    """)

    # ------------------------------------------------------------------
    # Seed: 1 test customer + 1 test tenant (local dev only)
    # ------------------------------------------------------------------
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


def downgrade() -> None:
    # Drop only for full local teardown — never run in production
    op.execute("DROP TABLE IF EXISTS shared_schema.tenant_vcs_config")
    op.execute("DROP TABLE IF EXISTS shared_schema.vcs_event_whitelist")
    op.execute("DROP TABLE IF EXISTS shared_schema.tenant")
    op.execute("DROP TABLE IF EXISTS shared_schema.customer")

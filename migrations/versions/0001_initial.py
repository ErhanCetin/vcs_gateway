"""Initial schema — vcs_gateway_schema tables (outbox_event + inbound_event).

Revision ID: 0001
Revises:
Create Date: 2026-01-01 00:00:00.000000
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "vcs_gateway_schema"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # ------------------------------------------------------------------
    # outbox_event — debounce-aware outbox (VCS Gateway specific)
    # ------------------------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.outbox_event (
            outbox_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            event_type      TEXT        NOT NULL,
            correlation_id  UUID        NOT NULL,
            pr_hash_key     VARCHAR(64),
            pr_version      INTEGER,
            payload         JSONB       NOT NULL,
            headers         JSONB       NOT NULL DEFAULT '{{}}',
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
    """)

    op.execute(f"""
        CREATE INDEX idx_outbox_scheduled
        ON {SCHEMA}.outbox_event (dispatch_at)
        WHERE status = 'SCHEDULED'
    """)

    # ------------------------------------------------------------------
    # inbound_event — every accepted webhook stored here
    # ------------------------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.inbound_event (
            event_id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            correlation_id    UUID         NOT NULL,
            tenant_id         UUID         NOT NULL,
            vcs_provider      VARCHAR(50)  NOT NULL,
            vcs_instance_id   VARCHAR(255) NOT NULL DEFAULT 'github.com',
            repo_id           VARCHAR(255) NOT NULL,
            repo_name         VARCHAR(500),
            pr_id             VARCHAR(255) NOT NULL,
            pr_title          TEXT,
            pr_author         VARCHAR(255),
            pr_url            VARCHAR(1000),
            commit_sha        VARCHAR(64)  NOT NULL,
            action            VARCHAR(50)  NOT NULL,
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
    """)

    op.execute(f"""
        CREATE UNIQUE INDEX uq_inbound_event_correlation_id
        ON {SCHEMA}.inbound_event (correlation_id)
    """)

    op.execute(f"""
        CREATE UNIQUE INDEX uq_inbound_event_pr_hash_key
        ON {SCHEMA}.inbound_event (pr_hash_key)
    """)

    op.execute(f"""
        CREATE INDEX idx_inbound_event_tenant_created
        ON {SCHEMA}.inbound_event (tenant_id, created_at DESC)
    """)

    op.execute(f"""
        CREATE INDEX idx_inbound_event_tenant_pr
        ON {SCHEMA}.inbound_event (tenant_id, pr_id)
    """)

    op.execute(f"""
        CREATE INDEX idx_inbound_event_status_created
        ON {SCHEMA}.inbound_event (processing_status, created_at DESC)
    """)


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.inbound_event")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.outbox_event")
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")

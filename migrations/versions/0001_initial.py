"""Initial schema — outbox_event table.

Revision ID: 0001
Revises:
Create Date: 2026-01-01 00:00:00.000000
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Replace VCS Gateway_SCHEMA with your actual schema name
SCHEMA = "vcs_gateway_schema"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.outbox_event (
            outbox_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            exchange       TEXT NOT NULL,
            routing_key    TEXT NOT NULL,
            payload        JSONB NOT NULL,
            correlation_id TEXT NOT NULL,
            status         TEXT NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending', 'published', 'failed')),
            retry_count    INT  NOT NULL DEFAULT 0,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            published_at   TIMESTAMPTZ
        )
    """)

    op.execute(f"""
        CREATE INDEX idx_outbox_status_created
        ON {SCHEMA}.outbox_event (status, created_at)
        WHERE status = 'pending'
    """)

    # Add your service-specific tables here


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.outbox_event")
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")

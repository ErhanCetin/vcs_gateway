"""TenantVcsConfig repository — auto-onboarding of repos in shared_schema."""

from uuid import UUID

import asyncpg

from vcs_gateway.db.repository import BaseRepository


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
        Must be called within an existing transaction (conn passed explicitly).
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
            tenant_id,
            vcs_provider,
            vcs_instance_id,
            repo_id,
            repo_name,
            repo_url,
        )

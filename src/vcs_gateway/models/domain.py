"""
Domain models — typed representations of DB rows and business objects.

No ORM — asyncpg returns asyncpg.Record objects.
Use these Pydantic v2 models to parse Records into typed Python objects.

Example:
    row = await repo.fetchrow(QUERY, id)
    obj = MyDomainModel.model_validate(dict(row))
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class BaseDomainModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


# ---------------------------------------------------------------------------
# EXAMPLE — replace with actual domain models for your service
# ---------------------------------------------------------------------------


class TenantRecord(BaseDomainModel):
    """Minimal tenant info needed by most services."""
    tenant_id: UUID
    tenant_name: str
    is_active: bool
    created_at: datetime

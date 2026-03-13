"""
Queue message models — Pydantic v2.

Convention:
  *Message  → messages this service CONSUMES (incoming)
  *Event    → events this service PUBLISHES (outgoing)

All messages inherit BaseMessage which enforces the envelope fields.
Replace the example models below with your service-specific ones.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class BaseMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: UUID = Field(default_factory=uuid4)
    event_type: str
    correlation_id: UUID
    tenant_id: UUID
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# EXAMPLE — replace with actual message models for your service
# ---------------------------------------------------------------------------


class ExampleConsumedMessage(BaseMessage):
    """Message consumed from example.queue.name."""
    event_type: str = "example.event.consumed"
    # Add your fields here


class ExamplePublishedEvent(BaseMessage):
    """Event published to example.queue.published."""
    event_type: str = "example.event.published"
    # Add your fields here


class JourneyStepEvent(BaseMessage):
    """Published to journey.step.created for all lifecycle steps."""
    event_type: str = "journey.step.created"
    vcs_gateway: str
    step_type: str
    status: str  # in_progress | completed | failed | skipped
    pr_hash_key: str | None = None
    metadata: dict | None = None

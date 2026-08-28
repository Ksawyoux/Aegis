"""Persistent, versionable envelopes for operational incident runs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from aegis.agent.summary import IncidentSummary


class DeliveryOutcome(BaseModel):
    """The one recorded result of a best-effort delivery attempt."""

    attempted: bool
    ok: bool
    status_code: int | None = None
    error: str | None = None


class IncidentRecord(BaseModel):
    """The complete JSONB shape persisted for one investigation run."""

    run_id: str
    summary: IncidentSummary | None = None
    trace: list[dict[str, Any]]
    delivery: DeliveryOutcome | None = None

    def as_json(self) -> dict[str, Any]:
        """Return the JSON-safe representation stored in ``incidents.summary_json``."""
        return self.model_dump(mode="json")

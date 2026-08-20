"""Incident summaries and their run-scoped provenance validation.

The validator proves that every citation in a summary was returned by a tool
during this run, so a fabricated identifier aborts the run.  It does not prove
that a cited row supports the sentence containing it; semantic support remains
the responsibility of scenario evaluation or a human reviewer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from aegis.mcp_server import citations


class Claim(BaseModel):
    """A natural-language assertion with the evidence identifiers it names."""

    statement: str
    cites: list[str] = Field(min_length=1)


class TimelineEntry(BaseModel):
    """One incident event with evidence identifiers returned during the run."""

    at: datetime
    what: str
    cites: list[str] = Field(min_length=1)


class IncidentSummary(BaseModel):
    """The agent's final, evidence-labelled incident assessment."""

    service: str
    root_cause: Claim
    confidence: Literal["high", "medium", "low"]
    timeline: list[TimelineEntry]
    ruled_out: list[Claim] = Field(default_factory=list)
    similar_incidents: list[Claim] = Field(default_factory=list)
    recommended_action: str


CLAIM_BEARING_FIELDS = ("root_cause", "timeline", "ruled_out", "similar_incidents")
"""The complete, deliberately explicit list of summary fields with citations."""


class ProvenanceError(ValueError):
    """Raised when a summary refers to malformed or uncaptured evidence."""


def validate_provenance(summary: IncidentSummary, captured: set[str]) -> None:
    """Ensure every summary citation was returned by a tool during this run.

    This rejects fabricated citations, including syntactically valid identifiers
    that were not captured in ``captured``.  It does not verify that cited
    evidence supports the surrounding sentence; semantic support requires
    scenario evaluation or human review.
    """
    for field_name in CLAIM_BEARING_FIELDS:
        value = getattr(summary, field_name)
        for cite in _field_citations(field_name, value):
            if not citations.is_wellformed(cite):
                raise ProvenanceError(f"malformed citation in {field_name}: {cite!r}")
            if cite not in captured:
                raise ProvenanceError(f"uncaptured citation in {field_name}: {cite!r}")


def _field_citations(field_name: str, value: object) -> list[str]:
    """Return citations for one declared claim-bearing summary field only."""
    if field_name == "root_cause":
        assert isinstance(value, Claim)
        return value.cites
    if field_name == "timeline":
        assert isinstance(value, list)
        cites: list[str] = []
        for entry in value:
            assert isinstance(entry, TimelineEntry)
            cites.extend(entry.cites)
        return cites
    if field_name in {"ruled_out", "similar_incidents"}:
        assert isinstance(value, list)
        cites = []
        for claim in value:
            assert isinstance(claim, Claim)
            cites.extend(claim.cites)
        return cites
    raise RuntimeError(f"unhandled claim-bearing field: {field_name}")


__all__ = [
    "CLAIM_BEARING_FIELDS",
    "Claim",
    "IncidentSummary",
    "ProvenanceError",
    "TimelineEntry",
    "validate_provenance",
]

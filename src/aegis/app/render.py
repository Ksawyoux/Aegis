"""Human-readable rendering for validated incident summaries."""

from __future__ import annotations

from collections.abc import Sequence

from aegis.agent.summary import IncidentSummary


def render_markdown(summary: IncidentSummary) -> str:
    """Render the summary so every claim shows the evidence supporting it.

    The markdown is what a human actually reads, so a claim rendered without
    its citations is an unsupported assertion no matter how complete the JSON
    beside it is. ``ruled_out`` carries the reasoning that distinguishes a
    diagnosis from a guess -- why the other candidate was rejected -- so it is
    rendered even though it is optional in the model.
    """
    lines = [
        f"# Investigation: {summary.service}",
        "",
        "## Root cause",
        summary.root_cause.statement,
        _evidence_line(summary.root_cause.cites),
        "",
        f"Confidence: {summary.confidence}",
        "",
        "## Recommended action",
        summary.recommended_action,
    ]
    if summary.timeline:
        lines.extend(["", "## Timeline"])
        for entry in summary.timeline:
            lines.append(f"- {entry.at.isoformat()}: {entry.what}")
            lines.append(f"  {_evidence_line(entry.cites)}")
    for heading, claims in (
        ("Ruled out", summary.ruled_out),
        ("Similar incidents", summary.similar_incidents),
    ):
        if not claims:
            continue
        lines.extend(["", f"## {heading}"])
        for claim in claims:
            lines.append(f"- {claim.statement}")
            lines.append(f"  {_evidence_line(claim.cites)}")
    return "\n".join(lines)


def _evidence_line(cites: Sequence[str]) -> str:
    """Render citation identifiers verbatim, in the order the agent gave them."""
    return "Evidence: " + ", ".join(f"`{cite}`" for cite in cites)

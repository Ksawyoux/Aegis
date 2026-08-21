"""Stored investigation-run inspection (Part 4 §7).

Reads exactly the ``incidents.summary_json`` envelope Part 3's ``IncidentRecord``
(``src/aegis/app/records.py``) owns and writes. Part 3 has not landed in this
worktree at the time this module was written, so every reference to it is a
deferred, function-local import rather than a module-level one -- this module
must still import cleanly, and its own tests must still be collectible, in a
tree where ``app/records.py`` does not exist yet.

The viewer is read-only and never queries current evidence rows: a rollup can
legitimately change after later ingest, and the point of this view is what the
agent actually saw during its own run, not what is true right now. It also
does not claim a cited row supports the sentence it is attached to -- only
that the citation was returned by a tool during this run. That is the same
provenance boundary ``agent.summary.validate_provenance`` states.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    # aegis.app.records is Part 3's IncidentRecord envelope model. It has not
    # landed in this worktree; the ignore below must be removed once it has,
    # or `mypy --strict` will flag it as an unused ignore under that setting.
    from aegis.app.records import IncidentRecord

_CITATION_FIELD_NAMES = frozenset({"cite", "source_cites", "baseline_cites", "resolution_cite"})


class RunNotFoundError(LookupError):
    """Raised when no persisted incident carries the requested ``run_id``."""


class TraceIntegrityError(ValueError):
    """Stored trace and stored summary disagree or do not match the v0.3 envelope."""


@dataclass(frozen=True)
class StoredRun:
    """One persisted investigation, together with its validated envelope."""

    incident_id: int
    dedup_key: str
    incident_status: str
    record: IncidentRecord


def load_stored_run(session: Session, *, run_id: str) -> StoredRun:
    """Look up and validate the one incident carrying ``run_id``.

    Raises :class:`RunNotFoundError` for zero matches and
    :class:`TraceIntegrityError` if more than one incident claims the same
    ``run_id`` -- ``run_id`` is a UUID4 hex correlation key, so a duplicate
    indicates corrupted or hand-edited storage, not a legitimate collision.
    """
    rows = session.execute(
        text(
            """
            SELECT id, dedup_key, status, summary_json
            FROM incidents
            WHERE summary_json->>'run_id' = :run_id
            ORDER BY id
            LIMIT 2
            """
        ),
        {"run_id": run_id},
    ).mappings().all()

    # A run_id that matches nothing, or matches more than one incident, is
    # detected by the query alone -- it does not require Part 3's envelope
    # model, so those two outcomes work even before Part 3 has landed.
    if not rows:
        raise RunNotFoundError(f"no persisted incident carries run_id {run_id!r}")
    if len(rows) > 1:
        raise TraceIntegrityError("run_id is not unique")

    row = rows[0]
    summary_json = row["summary_json"]
    if not isinstance(summary_json, dict):
        raise TraceIntegrityError(f"incident {row['id']}: summary_json is not an object")

    from aegis.app.records import IncidentRecord  # noqa: PLC0415

    record = IncidentRecord.model_validate(summary_json)
    if record.run_id != run_id:
        raise TraceIntegrityError(
            f"incident {row['id']}: stored run_id {record.run_id!r} does not match "
            f"the requested {run_id!r}"
        )

    return StoredRun(
        incident_id=int(row["id"]),
        dedup_key=str(row["dedup_key"]),
        incident_status=str(row["status"]),
        record=record,
    )


def validate_trace_integrity(run: StoredRun) -> None:
    """Validate the loaded envelope's internal consistency (Part 4 §7.4).

    Checks, in order: at most one terminal event; a summarized incident has a
    non-null summary and a completed terminal event; a failed incident has a
    null summary and a failed terminal event; every citation in the stored
    summary occurs in an earlier ``tool_result``; delivery, when present,
    forms a valid outcome.
    """
    record = run.record
    trace = record.trace

    terminal_events = [event for event in trace if _event_kind(event) == "terminal"]
    if len(terminal_events) > 1:
        raise TraceIntegrityError(f"run {record.run_id}: more than one terminal event")

    terminal_status = _event_payload(terminal_events[0]).get("status") if terminal_events else None

    if run.incident_status == "summarized":
        if record.summary is None:
            raise TraceIntegrityError(
                f"run {record.run_id}: status=summarized but no summary is stored"
            )
        if terminal_status != "completed":
            raise TraceIntegrityError(
                f"run {record.run_id}: status=summarized but terminal status is "
                f"{terminal_status!r}, expected 'completed'"
            )
    elif run.incident_status == "failed":
        if record.summary is not None:
            raise TraceIntegrityError(
                f"run {record.run_id}: status=failed but a summary is stored"
            )
        if terminal_status != "failed":
            raise TraceIntegrityError(
                f"run {record.run_id}: status=failed but terminal status is "
                f"{terminal_status!r}, expected 'failed'"
            )

    if record.summary is not None:
        captured = _captured_citations(trace)
        missing = [cite for cite in _summary_citations(record.summary) if cite not in captured]
        if missing:
            raise TraceIntegrityError(
                f"run {record.run_id}: citations not found in any captured tool result: {missing}"
            )

    if record.delivery is not None:
        import aegis.agent.slack as _slack  # noqa: PLC0415

        if not isinstance(record.delivery, _slack.DeliveryOutcome):
            _slack.DeliveryOutcome.model_validate(
                record.delivery
                if isinstance(record.delivery, dict)
                else record.delivery.model_dump()
            )


def render_trace(run: StoredRun, *, include_payloads: bool = False) -> str:
    """Render a stored run's trace, always succeeding even on integrity defects.

    A missing citation is rendered inline as ``MISSING FROM TRACE`` rather
    than aborting the render -- the CLI still shows the rest of the trace and
    reports the integrity failure separately with exit code 2.
    """
    record = run.record
    lines: list[str] = []
    lines.append(f"Run {record.run_id}")
    lines.append(f"Incident {run.incident_id} · {run.dedup_key} · {run.incident_status}")
    lines.append(f"Delivery: {_render_delivery(record.delivery)}")
    lines.append("")

    for index, event in enumerate(record.trace):
        lines.append(f"{index:03d} {_render_event_line(event)}")
        if include_payloads:
            lines.append(f"    payload: {_event_payload(event)!r}")

    if record.summary is not None:
        captured_by_event = _citations_by_event(record.trace)
        lines.append("")
        lines.extend(_render_summary_citations(record.summary, captured_by_event))

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Event rendering
# --------------------------------------------------------------------------


def _render_event_line(event: Any) -> str:
    kind = _event_kind(event)
    payload = _event_payload(event)
    if kind == "agent_turn":
        return f"agent_turn turn={payload.get('turn')} stop_reason={payload.get('stop_reason')}"
    if kind == "tool_result":
        tool = payload.get("tool")
        cites = len(_citation_strings_in(payload.get("result")))
        return f"tool_result {tool} cites={cites}"
    if kind == "error":
        return f"error tool={payload.get('tool')} tool_use_id={payload.get('tool_use_id')}"
    if kind == "terminal":
        status = payload.get("status")
        error_type = payload.get("error_type")
        suffix = f" error_type={error_type}" if error_type else ""
        return f"terminal status={status}{suffix}"
    # Additive future event kinds are rendered generically rather than dropped.
    fields = " ".join(f"{key}={value}" for key, value in sorted(payload.items()))
    return f"{kind} {fields}".rstrip()


def _render_delivery(delivery: Any) -> str:
    if delivery is None:
        return "not attempted"
    outcome = delivery.model_dump() if hasattr(delivery, "model_dump") else dict(delivery)
    if not outcome.get("attempted"):
        if outcome.get("error"):
            return f"not attempted ({outcome.get('error')})"
        return "not attempted"
    if outcome.get("ok"):
        return f"delivered (status {outcome.get('status_code')})"
    return f"attempted, failed (status={outcome.get('status_code')}, error={outcome.get('error')})"


def _render_summary_citations(
    summary: Any, captured_by_event: dict[str, int]
) -> list[str]:
    lines: list[str] = []

    def _cite_lines(cites: list[str]) -> list[str]:
        rendered = []
        for cite in cites:
            location = captured_by_event.get(cite)
            if location is not None:
                marker = f"captured at event {location:03d}"
            else:
                marker = "MISSING FROM TRACE"
            rendered.append(f"  {cite:<32} {marker}")
        return rendered

    lines.append(f"Root cause: {summary.root_cause.statement}")
    lines.extend(_cite_lines(summary.root_cause.cites))

    if summary.ruled_out:
        lines.append("")
        lines.append("Ruled out:")
        for claim in summary.ruled_out:
            lines.append(f"  - {claim.statement}")
            lines.extend(f"  {line}" for line in _cite_lines(claim.cites))

    if summary.timeline:
        lines.append("")
        lines.append("Timeline:")
        for entry in summary.timeline:
            lines.append(f"  {entry.at.isoformat()}: {entry.what}")
            lines.extend(f"  {line}" for line in _cite_lines(entry.cites))

    if summary.similar_incidents:
        lines.append("")
        lines.append("Similar incidents:")
        for claim in summary.similar_incidents:
            lines.append(f"  - {claim.statement}")
            lines.extend(f"  {line}" for line in _cite_lines(claim.cites))

    return lines


# --------------------------------------------------------------------------
# Citation harvesting
# --------------------------------------------------------------------------


def _event_kind(event: Any) -> str:
    return str(event.get("kind") if isinstance(event, dict) else getattr(event, "kind"))


def _event_payload(event: Any) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event, dict) else getattr(event, "payload", {})
    return dict(payload) if isinstance(payload, dict) else {}


def _captured_citations(trace: list[Any]) -> set[str]:
    captured: set[str] = set()
    for event in trace:
        if _event_kind(event) == "tool_result":
            captured.update(_citation_strings_in(_event_payload(event).get("result")))
    return captured


def _citations_by_event(trace: list[Any]) -> dict[str, int]:
    """Map each citation to the index of the first event that captured it."""
    by_event: dict[str, int] = {}
    for index, event in enumerate(trace):
        if _event_kind(event) != "tool_result":
            continue
        for cite in _citation_strings_in(_event_payload(event).get("result")):
            by_event.setdefault(cite, index)
    return by_event


def _citation_strings_in(value: object) -> set[str]:
    """Walk a stored tool result's plain JSON, harvesting named citation fields."""
    captured: set[str] = set()

    def visit(node: object) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if key in _CITATION_FIELD_NAMES:
                    captured.update(_leaf_strings(child))
                visit(child)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(value)
    return captured


def _leaf_strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str)}
    return set()


def _summary_citations(summary: Any) -> list[str]:
    cites: list[str] = list(summary.root_cause.cites)
    for claim in summary.ruled_out:
        cites.extend(claim.cites)
    for entry in summary.timeline:
        cites.extend(entry.cites)
    for claim in summary.similar_incidents:
        cites.extend(claim.cites)
    return cites


__all__ = [
    "RunNotFoundError",
    "StoredRun",
    "TraceIntegrityError",
    "load_stored_run",
    "render_trace",
    "validate_trace_integrity",
]

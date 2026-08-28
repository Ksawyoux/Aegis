"""Synchronous background work for one persisted incident."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from sqlalchemy import Engine

from aegis.agent.summary import IncidentSummary
from aegis.app.investigate import InvestigationRequest, investigate
from aegis.app.records import DeliveryOutcome
from aegis.app.run_context import DatabaseSink, RunContext
from aegis.config import Settings

Deliverer = Callable[[IncidentSummary, str, Settings], DeliveryOutcome]


def run_incident(
    incident_id: int,
    request: InvestigationRequest,
    settings: Settings,
    engine: Engine,
    *,
    deliver: Deliverer | None = None,
) -> None:
    """Investigate, persist every outcome, and never leak a background exception.

    This must remain synchronous: Starlette runs sync background work in a
    threadpool, where ``investigate`` may safely call ``asyncio.run``. Failures
    are persisted rather than re-raised because Starlette would only log and
    discard a task exception.
    """
    run_id = uuid4().hex
    sink = DatabaseSink(engine, incident_id, run_id)
    sink.flush(status="investigating")
    context = RunContext(run_id, sink)
    try:
        summary = investigate(request, context, settings)
    except BaseException:
        sink.flush(status="failed")
        return

    sink.flush(status="summarized", summary=summary)
    active_deliver = deliver
    if active_deliver is None:
        from aegis.agent.slack import post_summary  # noqa: PLC0415

        active_deliver = post_summary
    try:
        outcome = active_deliver(summary, run_id, settings)
    except BaseException as exc:
        outcome = DeliveryOutcome(attempted=True, ok=False, error=f"{type(exc).__name__}: {exc}")
    sink.flush(status="summarized", summary=summary, delivery=outcome)

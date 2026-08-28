"""Shared five-scenario evaluation packaging (Part 4 §3).

One paid OpenAI model call per scenario, never more. ``evaluate_case`` is called
exactly once per collected :class:`EvaluationCase`, and every semantic
assertion in ``test_scenario.py`` reads the resulting single
:class:`EvaluationResult` rather than re-invoking the model. There is no
automatic retry and no "best of N" selection: a scenario that fails, fails.

``EXPECTED_SCENARIO_COUNT`` is fixed at five because that is the milestone's
falsifiable claim (Part 4 spec §3.2, §10.5). ``load_evaluation_cases`` generalizes
to however many manifests are actually present, so a missing or extra manifest
only surfaces under strict demo mode (``AEGIS_REQUIRE_LIVE_EVAL=1``), where the
count mismatch fails loudly rather than silently accepting fewer scenarios as if
they were the whole claim.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import yaml
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from aegis.agent.summary import IncidentSummary
from aegis.app.investigate import InvestigationRequest, build_investigation_request, investigate
from aegis.app.run_context import InMemorySink, RunContext
from aegis.config import Settings
from aegis.db.models import Service

ROOT = Path(__file__).parents[2]
SCENARIO_DIR = ROOT / "corpus" / "scenarios"

EXPECTED_SCENARIO_COUNT = 5

STRICT_LIVE_EVAL_ENV = "AEGIS_REQUIRE_LIVE_EVAL"
DEMO_MODE_ENV = "AEGIS_DEMO_MODE"


def strict_live_eval_required() -> bool:
    """Whether ``make demo`` mode is active: skips become failures."""
    return os.environ.get(STRICT_LIVE_EVAL_ENV, "") == "1"


def demo_mode_active() -> bool:
    """Whether evaluations must persist a real incident row (Part 4 §3.3)."""
    return os.environ.get(DEMO_MODE_ENV, "") == "1"


@dataclass(frozen=True)
class EvaluationCase:
    """One collected scenario manifest, parsed into a model-safe request."""

    name: str
    path: Path
    request: InvestigationRequest
    expect: dict[str, Any]


@dataclass(frozen=True)
class EvaluationResult:
    """The single result of the one paid run for one :class:`EvaluationCase`."""

    case: EvaluationCase
    incident_id: int | None
    run_id: str
    summary: IncidentSummary
    captured_cites: frozenset[str]


class DemoPersistenceUnavailable(RuntimeError):
    """Raised when ``persist=True`` is requested but Part 3's runner is absent.

    Part 3 (``src/aegis/app/runner.py``, ``run_incident``) owns evaluation
    incident persistence. Until it lands, demo-mode packaging can be built and
    tested around, but it cannot actually persist a run.
    """


def load_evaluation_cases(directory: Path = SCENARIO_DIR) -> tuple[EvaluationCase, ...]:
    """Return one :class:`EvaluationCase` per sorted scenario manifest."""
    cases = []
    for path in sorted(directory.glob("*.yaml")):
        scenario = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(scenario, dict):
            raise ValueError(f"{path}: scenario manifest must be a YAML mapping")
        name = scenario.get("name")
        expect = scenario.get("expect")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{path}: scenario manifest requires a non-empty 'name'")
        if not isinstance(expect, dict):
            raise ValueError(f"{path}: scenario manifest requires an 'expect' mapping")
        cases.append(
            EvaluationCase(
                name=name,
                path=path,
                request=build_investigation_request(scenario),
                expect=cast(dict[str, Any], expect),
            )
        )
    _require_unique_names(cases)
    return tuple(cases)


def _require_unique_names(cases: list[EvaluationCase]) -> None:
    seen: dict[str, Path] = {}
    for case in cases:
        prior = seen.get(case.name)
        if prior is not None:
            raise ValueError(f"duplicate scenario name {case.name!r}: {prior} and {case.path}")
        seen[case.name] = case.path


def evaluate_case(
    case: EvaluationCase,
    *,
    settings: Settings,
    engine: Engine,
    persist: bool,
) -> EvaluationResult:
    """Run exactly one paid investigation for ``case`` and return its result.

    ``persist=True`` is the ``make demo`` path (Part 4 §3.3): a real
    ``incidents`` row is created and ``run_incident`` (Part 3) drives the
    investigation, so the run is later inspectable with ``aegis trace``.
    ``persist=False`` is the ordinary opt-in developer path: an in-memory
    trace sink, no database row, matching v0.1's eval fixture.
    """
    if persist:
        return _evaluate_persisted(case, settings=settings, engine=engine)
    return _evaluate_in_memory(case, settings=settings)


def _evaluate_in_memory(case: EvaluationCase, *, settings: Settings) -> EvaluationResult:
    del settings  # accepted for interface symmetry with the persisted path
    run_id = uuid4().hex
    run_context = RunContext(run_id, InMemorySink())
    summary = investigate(case.request, run_context)
    return EvaluationResult(
        case=case,
        incident_id=None,
        run_id=run_id,
        summary=summary,
        captured_cites=frozenset(run_context.captured_cites),
    )


def _evaluate_persisted(
    case: EvaluationCase, *, settings: Settings, engine: Engine
) -> EvaluationResult:
    try:
        from aegis.app.runner import run_incident  # noqa: PLC0415
    except ImportError as exc:
        raise DemoPersistenceUnavailable(
            "AEGIS_DEMO_MODE=1 requires aegis.app.runner.run_incident (Part 3), "
            "which is not present in this tree"
        ) from exc

    incident_id = _insert_demo_incident(engine, case)
    # Slack must never fire as a side effect of an evaluation run, even if the
    # operator's own .env carries a real webhook URL (Part 4 §3.3).
    run_settings = settings.model_copy(update={"slack_webhook_url": None})
    run_incident(incident_id, case.request, run_settings, engine)
    return _read_persisted_result(engine, case, incident_id)


def _insert_demo_incident(engine: Engine, case: EvaluationCase) -> int:
    dedup_key = f"demo-eval:{case.name}"
    with Session(engine) as session, session.begin():
        service_id = session.scalar(
            select(Service.id).where(Service.name == case.request.service)
        )
        incident_id = session.execute(
            _demo_incident_insert(),
            {
                "dedup_key": dedup_key,
                "service_id": service_id,
                "opened_at": case.request.fired_at,
                "window_start": case.request.window_start,
                "window_end": case.request.window_end,
                "alert_payload": _alert_payload_json(case),
            },
        ).scalar_one()
    return cast(int, incident_id)


def _demo_incident_insert() -> Any:
    from sqlalchemy import text  # noqa: PLC0415

    return text(
        """
        INSERT INTO incidents (
            dedup_key, service_id, opened_at, window_start, window_end,
            alert_payload, status, created_at
        ) VALUES (
            :dedup_key, :service_id, :opened_at, :window_start, :window_end,
            CAST(:alert_payload AS jsonb), 'open', now()
        )
        RETURNING id
        """
    )


def _alert_payload_json(case: EvaluationCase) -> str:
    import json  # noqa: PLC0415

    return json.dumps(case.request.model_dump(mode="json"), sort_keys=True)


def _read_persisted_result(
    engine: Engine, case: EvaluationCase, incident_id: int
) -> EvaluationResult:
    from sqlalchemy import text  # noqa: PLC0415

    with Session(engine) as session:
        row = session.execute(
            text("SELECT status, summary_json FROM incidents WHERE id = :id"),
            {"id": incident_id},
        ).one()
    status, summary_json = row
    if status != "summarized" or not isinstance(summary_json, dict):
        raise AssertionError(
            f"{case.name}: run_incident left status={status!r}, "
            f"expected 'summarized' with a persisted envelope"
        )
    run_id = summary_json.get("run_id")
    summary_payload = summary_json.get("summary")
    trace = summary_json.get("trace")
    if not isinstance(run_id, str) or not run_id:
        raise AssertionError(f"{case.name}: persisted envelope has no run_id")
    if not isinstance(summary_payload, dict):
        raise AssertionError(f"{case.name}: persisted envelope has no summary")
    if not isinstance(trace, list) or not trace:
        raise AssertionError(f"{case.name}: persisted envelope has an empty trace")
    if not any(_is_completed_terminal_event(event) for event in trace):
        raise AssertionError(f"{case.name}: persisted trace has no completed terminal event")

    summary = IncidentSummary.model_validate(summary_payload)
    captured = frozenset(_harvest_trace_citations(trace))
    return EvaluationResult(
        case=case,
        incident_id=incident_id,
        run_id=run_id,
        summary=summary,
        captured_cites=captured,
    )


def _is_completed_terminal_event(event: object) -> bool:
    return (
        isinstance(event, dict)
        and event.get("kind") == "terminal"
        and isinstance(event.get("payload"), dict)
        and event["payload"].get("status") == "completed"
    )


_CITATION_FIELD_NAMES = frozenset({"cite", "source_cites", "baseline_cites", "resolution_cite"})


def _harvest_trace_citations(trace: list[object]) -> set[str]:
    """Re-harvest citations from a stored trace using the same named fields.

    Mirrors ``aegis.app.run_context._citations_in_model`` but walks plain JSON
    (dict/list) rather than Pydantic models, because this reads the envelope
    back from storage rather than from the live run.
    """
    captured: set[str] = set()

    def visit(value: object, in_citation_field: bool) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_is_citation_field = key in _CITATION_FIELD_NAMES
                if child_is_citation_field:
                    captured.update(_citation_strings(child))
                visit(child, child_is_citation_field)
        elif isinstance(value, list):
            for item in value:
                visit(item, in_citation_field)

    for event in trace:
        visit(event, False)
    return captured


def _citation_strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str)}
    return set()


__all__ = [
    "DEMO_MODE_ENV",
    "EXPECTED_SCENARIO_COUNT",
    "STRICT_LIVE_EVAL_ENV",
    "DemoPersistenceUnavailable",
    "EvaluationCase",
    "EvaluationResult",
    "SCENARIO_DIR",
    "demo_mode_active",
    "evaluate_case",
    "load_evaluation_cases",
    "strict_live_eval_required",
]

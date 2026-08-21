from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from aegis.agent.summary import Claim, IncidentSummary, TimelineEntry
from aegis.agent.trace_view import RunNotFoundError, StoredRun, TraceIntegrityError
from aegis.app.investigate import build_investigation_request
from aegis.app.render import render_markdown
from aegis.cli import app

runner = CliRunner()


def _scenario() -> dict[str, object]:
    return {
        "alert": {
            "service": "checkout-api",
            "alert_name": "HighErrorRate",
            "fired_at": "2026-08-20T10:00:00Z",
            "payload": {"severity": "critical"},
            "window_start": "2026-08-20T09:00:00Z",
            "window_end": "2026-08-20T10:00:00Z",
        },
        "expect": {"canary": "GROUND-TRUTH-CANARY-DO-NOT-LEAK"},
        "reachability": {"tool": "get_error_telemetry"},
    }


def _summary() -> IncidentSummary:
    return IncidentSummary(
        service="checkout-api",
        root_cause=Claim(statement="A timeout caused the spike.", cites=["commit:" + "a" * 40]),
        confidence="high",
        timeline=[],
        recommended_action="Restore the timeout.",
    )


def test_investigate_renders_markdown_and_json(
    monkeypatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    scenario = tmp_path / "scenario.yaml"
    scenario.write_text(
        """alert:
  service: checkout-api
  alert_name: HighErrorRate
  fired_at: 2026-08-20T10:00:00Z
  payload: {severity: critical}
  window_start: 2026-08-20T09:00:00Z
  window_end: 2026-08-20T10:00:00Z
"""
    )
    monkeypatch.setattr("aegis.cli.run_investigation", lambda *_args: _summary())

    result = runner.invoke(app, ["investigate", "--scenario", str(scenario)])

    assert result.exit_code == 0
    assert "# Investigation: checkout-api" in result.output
    assert '"root_cause"' in result.output


def test_brief_allowlist_excludes_expect_and_reachability() -> None:
    brief = build_investigation_request(_scenario()).brief()

    assert "GROUND-TRUTH-CANARY-DO-NOT-LEAK" not in brief
    assert "reachability" not in brief
    assert '"service":"checkout-api"' in brief


def test_investigate_command_exits_nonzero_when_application_raises(
    monkeypatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    scenario = tmp_path / "scenario.yaml"
    scenario.write_text(
        """alert:
  service: checkout-api
  alert_name: HighErrorRate
  fired_at: 2026-08-20T10:00:00Z
  payload: {severity: critical}
  window_start: 2026-08-20T09:00:00Z
  window_end: 2026-08-20T10:00:00Z
"""
    )

    def fail(*_args: object) -> IncidentSummary:
        raise RuntimeError("transport failed")

    monkeypatch.setattr("aegis.cli.run_investigation", fail)

    result = runner.invoke(app, ["investigate", "--scenario", str(scenario)])

    assert result.exit_code != 0


def test_serve_api_binds_to_loopback_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, object] = {}

    def fake_run(application: object, *, host: str, port: int) -> None:
        captured.update(application=application, host=host, port=port)

    monkeypatch.setattr("aegis.cli.uvicorn.run", fake_run)
    result = runner.invoke(app, ["serve-api"])

    assert result.exit_code == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8000


def _rich_summary() -> IncidentSummary:
    """A summary populating every claim-bearing field, including the optional ones."""
    rollup = "rollup:checkout-api/2026-08-19T14:03:00Z/5xx/error/" + "0" * 32
    return IncidentSummary(
        service="checkout-api",
        root_cause=Claim(
            statement="Deploy 4 lowered the upstream timeout.",
            cites=["commit:" + "a" * 40, "deploy:4", rollup],
        ),
        confidence="high",
        timeline=[
            TimelineEntry(
                at=datetime(2026, 8, 19, 14, 1, 30, tzinfo=UTC),
                what="Deploy 4 shipped.",
                cites=["deploy:4"],
            ),
            TimelineEntry(
                at=datetime(2026, 8, 19, 14, 3, tzinfo=UTC),
                what="5xx rate rose to 12/min.",
                cites=[rollup],
            ),
        ],
        ruled_out=[
            Claim(statement="Deploy 3 is not the cause.", cites=["deploy:3", "commit:" + "b" * 40])
        ],
        similar_incidents=[
            Claim(statement="A prior timeout regression.", cites=["postmortem:x#0"])
        ],
        recommended_action="Restore the previous timeout.",
    )


def _all_citations(summary: IncidentSummary) -> list[str]:
    """Collect citations by walking the dump, not by naming fields.

    Naming fields here would let a claim-bearing field added later go
    unrendered while this test still passed -- which is the exact defect the
    test exists to prevent.
    """
    found: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "cites":
                    assert isinstance(item, list)
                    found.extend(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(summary.model_dump(mode="json"))
    return found


def test_rendered_markdown_shows_every_citation() -> None:
    """A claim rendered without its evidence is an unsupported assertion."""
    summary = _rich_summary()
    markdown = render_markdown(summary)

    citations = _all_citations(summary)
    assert len(citations) == 8
    for cite in citations:
        assert cite in markdown, f"citation missing from rendered markdown: {cite}"


def test_rendered_markdown_includes_ruled_out_and_similar_incidents() -> None:
    """The distractor reasoning is what the scenario exists to exercise."""
    markdown = render_markdown(_rich_summary())

    assert "## Ruled out" in markdown
    assert "Deploy 3 is not the cause." in markdown
    assert "## Similar incidents" in markdown
    assert "A prior timeout regression." in markdown


def test_rendered_markdown_omits_empty_optional_sections() -> None:
    markdown = render_markdown(_summary())

    assert "## Ruled out" not in markdown
    assert "## Similar incidents" not in markdown
    assert "## Timeline" not in markdown
@dataclass
class _FakeRecord:
    run_id: str
    summary: Any = None
    trace: list[dict[str, Any]] | None = None
    delivery: Any = None

    def __post_init__(self) -> None:
        if self.trace is None:
            self.trace = [{"kind": "terminal", "payload": {"status": "failed"}}]

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        del mode
        return {
            "run_id": self.run_id,
            "summary": self.summary,
            "trace": self.trace,
            "delivery": self.delivery,
        }


class _FakeEngine:
    def dispose(self) -> None:
        return None


def _patch_engine(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("aegis.cli.create_database_engine", lambda *_a, **_k: _FakeEngine())


def test_trace_command_exits_1_when_run_id_is_not_found(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_engine(monkeypatch)

    def _raise(*_args: object, **_kwargs: object) -> StoredRun:
        raise RunNotFoundError("no persisted incident carries run_id 'missing'")

    monkeypatch.setattr("aegis.cli.load_stored_run", _raise)

    result = runner.invoke(app, ["trace", "--run-id", "missing"])

    assert result.exit_code == 1
    assert "no persisted incident" in result.output


def test_trace_command_exits_2_when_run_id_is_not_unique(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_engine(monkeypatch)

    def _raise(*_args: object, **_kwargs: object) -> StoredRun:
        raise TraceIntegrityError("run_id is not unique")

    monkeypatch.setattr("aegis.cli.load_stored_run", _raise)

    result = runner.invoke(app, ["trace", "--run-id", "dup"])

    assert result.exit_code == 2
    assert "run_id is not unique" in result.output


def test_trace_command_renders_then_exits_2_on_a_poisoned_summary(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    _patch_engine(monkeypatch)
    run = StoredRun(
        incident_id=1,
        dedup_key="demo-eval:x",
        incident_status="summarized",
        record=_FakeRecord(run_id="poisoned-run"),  # type: ignore[arg-type]
    )
    monkeypatch.setattr("aegis.cli.load_stored_run", lambda *_a, **_k: run)

    def _raise_integrity(*_args: object, **_kwargs: object) -> None:
        raise TraceIntegrityError("citations not found in any captured tool result")

    monkeypatch.setattr("aegis.cli.validate_trace_integrity", _raise_integrity)

    result = runner.invoke(app, ["trace", "--run-id", "poisoned-run"])

    assert result.exit_code == 2
    # The trace is still rendered before the integrity failure is reported.
    assert "Run poisoned-run" in result.output
    assert "citations not found" in result.output


def test_trace_command_json_output_emits_the_stored_envelope(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    _patch_engine(monkeypatch)
    run = StoredRun(
        incident_id=7,
        dedup_key="demo-eval:y",
        incident_status="failed",
        record=_FakeRecord(run_id="json-run"),  # type: ignore[arg-type]
    )
    monkeypatch.setattr("aegis.cli.load_stored_run", lambda *_a, **_k: run)
    monkeypatch.setattr("aegis.cli.validate_trace_integrity", lambda *_a, **_k: None)

    result = runner.invoke(app, ["trace", "--run-id", "json-run", "--json"])

    assert result.exit_code == 0
    assert '"run_id": "json-run"' in result.output

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from aegis.agent.summary import Claim, IncidentSummary
from aegis.app.investigate import build_investigation_request
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

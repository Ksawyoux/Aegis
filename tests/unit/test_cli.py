from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from aegis.agent.summary import Claim, IncidentSummary, TimelineEntry
from aegis.app.investigate import build_investigation_request
from aegis.cli import _render_markdown, app

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
    markdown = _render_markdown(summary)

    citations = _all_citations(summary)
    assert len(citations) == 8
    for cite in citations:
        assert cite in markdown, f"citation missing from rendered markdown: {cite}"


def test_rendered_markdown_includes_ruled_out_and_similar_incidents() -> None:
    """The distractor reasoning is what the scenario exists to exercise."""
    markdown = _render_markdown(_rich_summary())

    assert "## Ruled out" in markdown
    assert "Deploy 3 is not the cause." in markdown
    assert "## Similar incidents" in markdown
    assert "A prior timeout regression." in markdown


def test_rendered_markdown_omits_empty_optional_sections() -> None:
    markdown = _render_markdown(_summary())

    assert "## Ruled out" not in markdown
    assert "## Similar incidents" not in markdown
    assert "## Timeline" not in markdown

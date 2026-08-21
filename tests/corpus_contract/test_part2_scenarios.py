from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import yaml  # type: ignore[import-untyped]

PART2_SCENARIOS = {
    "payments-pool-exhaustion",
    "search-oom-crashloop",
    "auth-token-expiry",
    "cdn-cache-miss-storm",
}


def _window(path: Path) -> tuple[datetime, datetime]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    alert = value["alert"]
    return (
        datetime.fromisoformat(str(alert["window_start"]).replace("Z", "+00:00")),
        datetime.fromisoformat(str(alert["window_end"]).replace("Z", "+00:00")),
    )


def test_part2_scenario_windows_are_separated_by_six_hours() -> None:
    scenarios = sorted(Path("corpus/scenarios").glob("*.yaml"))
    windows = [(path.name, *_window(path)) for path in scenarios]
    for index, (name, start, end) in enumerate(windows):
        for other_name, other_start, other_end in windows[index + 1 :]:
            separated = (
                end <= other_start - timedelta(hours=6)
                or other_end <= start - timedelta(hours=6)
            )
            assert separated, (
                name,
                other_name,
            )


def test_scenario_evidence_horizons_do_not_overlap() -> None:
    scenarios = sorted(Path("corpus/scenarios").glob("*.yaml"))
    horizons = [
        (path.name, start - timedelta(minutes=60), end)
        for path in scenarios
        for start, end in [_window(path)]
    ]
    for index, (name, start, end) in enumerate(horizons):
        for other_name, other_start, other_end in horizons[index + 1 :]:
            assert end <= other_start or other_end <= start, (name, other_name)


def test_part2_scenarios_declare_falsifiable_expectations() -> None:
    scenarios = {
        path.stem: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in Path("corpus/scenarios").glob("*.yaml")
        if path.stem in PART2_SCENARIOS
    }

    assert set(scenarios) == PART2_SCENARIOS
    for name, scenario in scenarios.items():
        expect = scenario["expect"]
        assert expect["must_cite"], f"{name} has no required evidence citations"
        assert all(isinstance(cite, str) for cite in expect["must_cite"])
        assert expect["reachability"], f"{name} has no pre-agent reachability checks"
        assert expect["forbidden_root_cause"], f"{name} has no negative control"
        assert expect["ruled_out_contains"], f"{name} has no distractor assertion"
        assert all(isinstance(value, str) for value in expect["forbidden_root_cause"])
        assert all(isinstance(value, str) for value in expect["ruled_out_contains"])
        assert set(expect["forbidden_root_cause"]) & set(expect["ruled_out_contains"])
        assert expect["min_confidence"] in {"low", "medium", "high"}
        assert expect["canary"] == "GROUND-TRUTH-CANARY-DO-NOT-LEAK"


def test_every_log_artifact_has_a_manifest_declaration() -> None:
    manifest = yaml.safe_load(Path("corpus/logs/manifest.yaml").read_text(encoding="utf-8"))
    assert isinstance(manifest, dict)
    for path in Path("corpus/logs").glob("*.log"):
        declaration = manifest[path.name]
        assert set(declaration) >= {"service", "format", "timezone"}

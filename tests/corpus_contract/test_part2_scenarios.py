from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import yaml  # type: ignore[import-untyped]


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


def test_every_log_artifact_has_a_manifest_declaration() -> None:
    manifest = yaml.safe_load(Path("corpus/logs/manifest.yaml").read_text(encoding="utf-8"))
    assert isinstance(manifest, dict)
    for path in Path("corpus/logs").glob("*.log"):
        declaration = manifest[path.name]
        assert set(declaration) >= {"service", "format", "timezone"}

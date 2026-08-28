from __future__ import annotations

from pathlib import Path

import pytest

from aegis.cli import _log_manifest


def test_log_manifest_requires_file_name_to_object_mapping(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"
    path.write_text("events.log: payments-api\n", encoding="utf-8")

    with pytest.raises(ValueError, match="entries"):
        _log_manifest(path)


def test_log_manifest_returns_declarations(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"
    path.write_text("events.log: {service: payments-api, format: logfmt, timezone: UTC}\n")

    assert _log_manifest(path)["events.log"]["format"] == "logfmt"

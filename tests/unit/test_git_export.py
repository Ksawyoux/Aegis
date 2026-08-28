from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from aegis.config import Settings
from aegis.ingest.git import GitExport, files_changed_for_commit, load_git_export


def test_git_export_schema_round_trips(tmp_path: Path) -> None:
    payload = _export_payload()
    export_path = tmp_path / "git-export.json"
    export_path.write_text(json.dumps(payload), encoding="utf-8")

    export = load_git_export(export_path)

    assert export.model_dump(mode="json") == payload


def test_git_export_rejects_duplicate_file_path_within_one_commit() -> None:
    payload = _export_payload()
    payload["commits"][0]["files_changed"].append(
        {
            "path": "src/checkout.py",
            "status": "modified",
            "additions": 1,
            "deletions": 0,
            "hunks": "@@ -1 +1 @@\n+changed",
        }
    )

    with pytest.raises(ValidationError, match="files_changed paths must be unique"):
        GitExport.model_validate(payload)


def test_hunk_budget_precedence_keeps_whole_files_and_orders_paths() -> None:
    payload = _export_payload()
    payload["commits"][0]["files_changed"] = [
        {
            "path": "tie-b.py",
            "status": "modified",
            "additions": 10,
            "deletions": 0,
            "hunks": "@@ -1 +1 @@\n+b",
        },
        {
            "path": "largest.py",
            "status": "modified",
            "additions": 20,
            "deletions": 10,
            "hunks": "@@ -1 +1 @@\n+a\n@@ -2 +2 @@\n+b\n@@ -3 +3 @@\n+c\n@@ -4 +4 @@\n+d",
        },
        {
            "path": "tie-a.py",
            "status": "modified",
            "additions": 10,
            "deletions": 0,
            "hunks": "@@ -1 +1 @@\n+a",
        },
    ]
    commit = GitExport.model_validate(payload).commits[0]

    files = files_changed_for_commit(
        commit,
        Settings(hunk_max_files=2, hunk_max_hunks_per_file=3, hunk_max_lines_per_file=20),
    )

    assert [file["path"] for file in files] == ["largest.py", "tie-a.py", "tie-b.py"]
    assert files[0]["hunks"] is None
    assert files[0]["hunks_omitted"] == "budget"
    assert files[1]["hunks"] == "@@ -1 +1 @@\n+a"
    assert files[1]["hunks_omitted"] is None
    assert files[2]["hunks"] is None
    assert files[2]["hunks_omitted"] == "budget"


def test_webhook_absence_outranks_budget_even_past_hunk_max_files() -> None:
    """A push payload supplies changed paths but never patch content.

    Every file from that payload must carry ``hunks_omitted: "webhook"``, never
    ``"budget"`` -- including files ranked outside the top ``hunk_max_files`` by
    size, where the old precedence mislabeled the omission reason because it
    only checked ``hunks is None`` after the budget/position branch already won.
    """
    payload = _export_payload()
    payload["commits"][0]["files_changed"] = [
        {
            "path": f"webhook-file-{index:02d}.py",
            "status": "modified",
            "additions": index,
            "deletions": 0,
            "hunks": None,
        }
        for index in range(20)
    ]
    commit = GitExport.model_validate(payload).commits[0]

    files = files_changed_for_commit(
        commit,
        Settings(hunk_max_files=15, hunk_max_hunks_per_file=3, hunk_max_lines_per_file=20),
    )

    assert len(files) == 20
    assert all(file["hunks"] is None for file in files)
    assert all(file["hunks_omitted"] == "webhook" for file in files)


def _export_payload() -> dict[str, object]:
    timestamp = datetime(2025, 1, 1, 12, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
    sha = "a" * 40
    return {
        "repo": "acme/checkout",
        "commits": [
            {
                "sha": sha,
                "author": "Ada Lovelace",
                "authored_at": timestamp,
                "committed_at": timestamp,
                "message": "Handle checkout timeout",
                "pr_number": None,
                "files_changed": [
                    {
                        "path": "src/checkout.py",
                        "status": "modified",
                        "additions": 3,
                        "deletions": 3,
                        "hunks": "@@ -1 +1 @@\n-timeout\n+retry_timeout",
                    }
                ],
            }
        ],
        "deploys": [
            {
                "commit_sha": sha,
                "environment": "production",
                "started_at": timestamp,
                "finished_at": timestamp,
                "status": "success",
            }
        ],
    }

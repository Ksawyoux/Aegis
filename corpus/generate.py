"""Generate the deterministic Part 1 checkout incident corpus."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CAUSAL_SHA = "a" * 40
DISTRACTOR_SHA = "b" * 40
_BASELINE_START = datetime(2026, 8, 19, 13, 3, tzinfo=UTC)
_CURRENT_START = datetime(2026, 8, 19, 13, 38, tzinfo=UTC)
_SPIKE_START = datetime(2026, 8, 19, 14, 3, tzinfo=UTC)


def generate(output_dir: Path = ROOT) -> None:
    """Write corpus evidence under ``output_dir`` with a fixed random stream."""
    rng = random.Random(1337)
    _write_json_lines(output_dir / "logs" / "checkout-api.log", _log_events(rng))
    _write_json(output_dir / "git" / "checkout.json", _git_export())


def _log_events(rng: random.Random) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for minute in range(35):
        baseline_timestamp = _BASELINE_START + timedelta(minutes=minute, seconds=10)
        current_timestamp = _CURRENT_START + timedelta(minutes=minute, seconds=10)
        events.append(_payment_event(baseline_timestamp, 200, rng))
        events.append(_payment_event(current_timestamp, 200, rng))

    # This is a normal, isolated upstream failure in the baseline. The subsequent
    # burst uses the same message template and begins exactly 90 seconds post-deploy.
    events.append(_payment_event(_BASELINE_START + timedelta(minutes=7, seconds=20), 504, rng))
    for minute in range(10):
        for occurrence in range(12):
            events.append(
                _payment_event(
                    _SPIKE_START + timedelta(minutes=minute, seconds=occurrence * 4), 504, rng
                )
            )

    events.append(
        {
            "ts": "2026-08-19T14:06:30Z",
            "level": "error",
            "service": "inventory-api",
            "msg": "payments client received status 504 for order 999999",
            "status": 504,
        }
    )
    return sorted(events, key=lambda event: str(event["ts"]))


def _payment_event(timestamp: datetime, status: int, rng: random.Random) -> dict[str, object]:
    order_id = rng.randint(100_000, 999_999)
    return {
        "duration_ms": rng.randint(15, 90),
        "level": "error" if status >= 500 else "info",
        "msg": f"payments client received status {status} for order {order_id}",
        "service": "checkout-api",
        "status": status,
        "trace_id": f"checkout-{order_id}",
        "ts": timestamp.isoformat().replace("+00:00", "Z"),
        "upstream": "payments",
    }


def _git_export() -> dict[str, Any]:
    return {
        "repo": "acme/checkout",
        "commits": [
            {
                "sha": DISTRACTOR_SHA,
                "author": "Sam Rivera",
                "authored_at": "2026-08-19T13:49:00Z",
                "committed_at": "2026-08-19T13:50:00Z",
                "message": "add checkout request metrics",
                "pr_number": 412,
                "files_changed": [
                    {
                        "path": "config/metrics.yaml",
                        "status": "modified",
                        "additions": 2,
                        "deletions": 1,
                        "hunks": (
                            "@@ -8,4 +8,5 @@ metrics:\n"
                            "-  labels: [route]\n"
                            "+  labels: [route, region]\n"
                        ),
                    }
                ],
            },
            {
                "sha": CAUSAL_SHA,
                "author": "Mina Patel",
                "authored_at": "2026-08-19T13:59:00Z",
                "committed_at": "2026-08-19T14:00:00Z",
                "message": "refactor client config",
                "pr_number": 413,
                "files_changed": [
                    {
                        "path": "config/payments-client.yaml",
                        "status": "modified",
                        "additions": 1,
                        "deletions": 1,
                        "hunks": (
                            "@@ -12,7 +12,7 @@ payments_client:\n"
                            "-  timeout: 30s\n"
                            "+  timeout: 3s\n"
                        ),
                    }
                ],
            },
        ],
        "deploys": [
            {
                "commit_sha": DISTRACTOR_SHA,
                "environment": "production",
                "started_at": "2026-08-19T13:55:00Z",
                "finished_at": "2026-08-19T13:56:00Z",
                "status": "success",
            },
            {
                "commit_sha": CAUSAL_SHA,
                "environment": "production",
                "started_at": "2026-08-19T14:01:30Z",
                "finished_at": "2026-08-19T14:02:00Z",
                "status": "success",
            },
        ],
    }


def _write_json_lines(path: Path, events: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n" for event in events)
    content = "".join(lines)
    path.write_text(content, encoding="utf-8", newline="\n")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, indent=2, sort_keys=True) + "\n"
    path.write_text(content, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    generate()

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
SEARCH_CAUSAL_SHA = "c" * 40
SEARCH_DISTRACTOR_SHA = "d" * 40
AUTH_CAUSAL_SHA = "e" * 40
AUTH_DISTRACTOR_SHA = "f" * 40
CDN_DISTRACTOR_SHA = "1" * 40
_BASELINE_START = datetime(2026, 8, 19, 13, 3, tzinfo=UTC)
_CURRENT_START = datetime(2026, 8, 19, 13, 38, tzinfo=UTC)
_SPIKE_START = datetime(2026, 8, 19, 14, 3, tzinfo=UTC)


def generate(output_dir: Path = ROOT) -> None:
    """Write corpus evidence under ``output_dir`` with a fixed random stream."""
    rng = random.Random(1337)
    _write_json_lines(output_dir / "logs" / "checkout-api.log", _log_events(rng))
    _write_json(output_dir / "git" / "checkout.json", _git_export())
    _write_lines(output_dir / "logs" / "payments-api.log", _payments_log())
    _write_lines(output_dir / "logs" / "search-api.log", _search_log())
    _write_lines(output_dir / "logs" / "auth-api.log", _auth_log())
    _write_lines(output_dir / "logs" / "cdn-api.log", _cdn_log())
    _write_json(output_dir / "git" / "search.json", _search_git_export())
    _write_json(output_dir / "git" / "auth.json", _auth_git_export())
    _write_json(output_dir / "git" / "cdn.json", _cdn_git_export())
    _write_json(output_dir / "terraform" / "plan-payments-pool.json", _payments_plan())
    _write_json(output_dir / "terraform" / "plan-cdn-cache.json", _cdn_plan())
    _write_json_array(output_dir / "terraform" / "applies.json", _terraform_applies())
    _write_json_array(output_dir / "k8s" / "pod-status.json", _pod_status())
    _write_json_array(output_dir / "k8s" / "events.json", _k8s_events())


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


def _payments_log() -> list[str]:
    baseline = datetime(2026, 8, 19, 23, 5, tzinfo=UTC)
    current = datetime(2026, 8, 19, 23, 40, tzinfo=UTC)
    spike = datetime(2026, 8, 20, 0, 3, tzinfo=UTC)
    records: list[tuple[datetime, str]] = []
    for minute in range(35):
        for second in (10, 40):
            records.append(
                _logfmt_payment_record(baseline + timedelta(minutes=minute, seconds=second), 200)
            )
            records.append(
                _logfmt_payment_record(current + timedelta(minutes=minute, seconds=second), 200)
            )
    records.append(_logfmt_payment_record(baseline + timedelta(minutes=7, seconds=20), 503))
    for minute in range(10):
        for occurrence in range(12):
            records.append(
                _logfmt_payment_record(
                    spike + timedelta(minutes=minute, seconds=occurrence * 5), 503
                )
            )
    return [line for _, line in sorted(records)]


def _logfmt_payment_record(timestamp: datetime, status: int) -> tuple[datetime, str]:
    if status == 200:
        message = "database connection checkout completed in 12ms"
        level = "info"
        pool_available = 64
    else:
        message = "database pool exhausted waiting 5000ms"
        level = "error"
        pool_available = 0
    stamp = timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    line = (
        f'ts={stamp} level={level} service=payments-api msg="{message}" '
        f"status={status} pool_available={pool_available} pool_size=80\n"
    )
    return timestamp, line


def _search_log() -> list[str]:
    baseline = datetime(2026, 8, 20, 6, 5, tzinfo=UTC)
    current = datetime(2026, 8, 20, 6, 40, tzinfo=UTC)
    spike = datetime(2026, 8, 20, 7, 3, tzinfo=UTC)
    records: list[tuple[datetime, str]] = []
    for minute in range(35):
        for second in (10, 40):
            records.append(
                _python_search_record(
                    baseline + timedelta(minutes=minute, seconds=second)
                )
            )
            records.append(
                _python_search_record(current + timedelta(minutes=minute, seconds=second))
            )
    for minute in range(5):
        for occurrence in range(12):
            timestamp = spike + timedelta(minutes=minute, seconds=occurrence * 5)
            stamp = timestamp.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
            raw = (
                f"{stamp} ERROR [search-api] worker.main: indexing request failed\n"
                "Traceback (most recent call last):\n"
                '  File "/srv/search/worker.py", line 88, in consume\n'
                '  File "/srv/search/indexer.py", line 241, in build_segment\n'
                "MemoryError: unable to allocate 268435456 bytes for segment\n"
            )
            records.append((timestamp, raw))
    return [line for _, record in sorted(records) for line in record.splitlines(keepends=True)]


def _python_search_record(timestamp: datetime) -> tuple[datetime, str]:
    stamp = timestamp.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    return timestamp, f"{stamp} INFO [search-api] worker.main: indexed shard successfully\n"


def _auth_log() -> list[str]:
    return _nginx_incident_log(
        baseline=datetime(2026, 8, 20, 13, 5, tzinfo=UTC),
        current=datetime(2026, 8, 20, 13, 40, tzinfo=UTC),
        spike=datetime(2026, 8, 20, 14, 3, tzinfo=UTC),
        method="POST",
        path="/oauth/token/refresh",
        failure_status=401,
    )


def _cdn_log() -> list[str]:
    return _nginx_incident_log(
        baseline=datetime(2026, 8, 20, 20, 5, tzinfo=UTC),
        current=datetime(2026, 8, 20, 20, 40, tzinfo=UTC),
        spike=datetime(2026, 8, 20, 21, 3, tzinfo=UTC),
        method="GET",
        path="/assets/app.js",
        failure_status=504,
    )


def _nginx_incident_log(
    *,
    baseline: datetime,
    current: datetime,
    spike: datetime,
    method: str,
    path: str,
    failure_status: int,
) -> list[str]:
    records: list[tuple[datetime, str]] = []
    for minute in range(35):
        for second in (10, 40):
            records.append(
                _nginx_record(
                    baseline + timedelta(minutes=minute, seconds=second), method, path, 200
                )
            )
            records.append(
                _nginx_record(
                    current + timedelta(minutes=minute, seconds=second), method, path, 200
                )
            )
    records.append(
        _nginx_record(
            baseline + timedelta(minutes=7, seconds=20),
            method,
            path,
            failure_status,
        )
    )
    for minute in range(10):
        for occurrence in range(12):
            records.append(
                _nginx_record(
                    spike + timedelta(minutes=minute, seconds=occurrence * 5),
                    method,
                    path,
                    failure_status,
                )
            )
    return [line for _, line in sorted(records)]


def _nginx_record(
    timestamp: datetime, method: str, path: str, status: int
) -> tuple[datetime, str]:
    elapsed = "0.024" if status == 200 else ("0.006" if status == 401 else "3.001")
    response_bytes = 842 if status == 200 else 167
    stamp = timestamp.strftime("%d/%b/%Y:%H:%M:%S +0000")
    line = (
        f'10.0.3.14 - - [{stamp}] "{method} {path} HTTP/1.1" {status} '
        f'{response_bytes} "-" "aegis-corpus/2.0" rt={elapsed} urt="{elapsed}"\n'
    )
    return timestamp, line


def _search_git_export() -> dict[str, Any]:
    return {
        "repo": "acme/search",
        "commits": [
            {
                "sha": SEARCH_DISTRACTOR_SHA,
                "author": "Noah Chen",
                "authored_at": "2026-08-20T06:48:00Z",
                "committed_at": "2026-08-20T06:49:00Z",
                "message": "add query latency labels",
                "pr_number": 701,
                "files_changed": [
                    {
                        "path": "config/metrics.yaml",
                        "status": "modified",
                        "additions": 2,
                        "deletions": 1,
                        "hunks": (
                            "@@ -4,3 +4,4 @@ labels:\n"
                            "-  - shard\n"
                            "+  - shard\n"
                            "+  - query_kind\n"
                        ),
                    }
                ],
            },
            {
                "sha": SEARCH_CAUSAL_SHA,
                "author": "Iris Okafor",
                "authored_at": "2026-08-20T06:58:00Z",
                "committed_at": "2026-08-20T06:59:00Z",
                "message": "tune worker resources",
                "pr_number": 702,
                "files_changed": [
                    {
                        "path": "deploy/search-worker.yaml",
                        "status": "modified",
                        "additions": 1,
                        "deletions": 1,
                        "hunks": (
                            "@@ -31,5 +31,5 @@ resources:\n"
                            "-    memory: 512Mi\n"
                            "+    memory: 128Mi\n"
                        ),
                    }
                ],
            },
        ],
        "deploys": [
            {
                "commit_sha": SEARCH_DISTRACTOR_SHA,
                "environment": "production",
                "started_at": "2026-08-20T06:51:00Z",
                "finished_at": "2026-08-20T06:52:00Z",
                "status": "success",
            },
            {
                "commit_sha": SEARCH_CAUSAL_SHA,
                "environment": "production",
                "started_at": "2026-08-20T07:01:30Z",
                "finished_at": "2026-08-20T07:02:00Z",
                "status": "success",
            },
        ],
    }


def _auth_git_export() -> dict[str, Any]:
    return {
        "repo": "acme/auth",
        "commits": [
            {
                "sha": AUTH_DISTRACTOR_SHA,
                "author": "Ava Singh",
                "authored_at": "2026-08-20T13:48:00Z",
                "committed_at": "2026-08-20T13:49:00Z",
                "message": "add issuer metrics",
                "pr_number": 881,
                "files_changed": [
                    {
                        "path": "config/metrics.yaml",
                        "status": "modified",
                        "additions": 1,
                        "deletions": 0,
                        "hunks": "@@ -9,3 +9,4 @@ labels:\n   - client_id\n+  - issuer\n",
                    }
                ],
            },
            {
                "sha": AUTH_CAUSAL_SHA,
                "author": "Leo Martin",
                "authored_at": "2026-08-20T13:58:00Z",
                "committed_at": "2026-08-20T13:59:00Z",
                "message": "refactor",
                "pr_number": 882,
                "files_changed": [
                    {
                        "path": "auth/token.py",
                        "status": "modified",
                        "additions": 1,
                        "deletions": 1,
                        "hunks": (
                            "@@ -74,5 +74,5 @@ def is_expired(expires_at, now):\n"
                            "-    return expires_at < now\n"
                            "+    return expires_at <= now\n"
                        ),
                    }
                ],
            },
        ],
        "deploys": [
            {
                "commit_sha": AUTH_DISTRACTOR_SHA,
                "environment": "production",
                "started_at": "2026-08-20T13:52:00Z",
                "finished_at": "2026-08-20T13:53:00Z",
                "status": "success",
            },
            {
                "commit_sha": AUTH_CAUSAL_SHA,
                "environment": "production",
                "started_at": "2026-08-20T14:01:30Z",
                "finished_at": "2026-08-20T14:02:00Z",
                "status": "success",
            },
        ],
    }


def _cdn_git_export() -> dict[str, Any]:
    return {
        "repo": "acme/cdn",
        "commits": [
            {
                "sha": CDN_DISTRACTOR_SHA,
                "author": "Maya Brooks",
                "authored_at": "2026-08-20T20:52:00Z",
                "committed_at": "2026-08-20T20:53:00Z",
                "message": "enable brotli responses",
                "pr_number": 944,
                "files_changed": [
                    {
                        "path": "nginx/compression.conf",
                        "status": "modified",
                        "additions": 1,
                        "deletions": 0,
                        "hunks": "@@ -5,3 +5,4 @@ compression:\n   gzip on;\n+  brotli on;\n",
                    }
                ],
            }
        ],
        "deploys": [
            {
                "commit_sha": CDN_DISTRACTOR_SHA,
                "environment": "production",
                "started_at": "2026-08-20T20:55:00Z",
                "finished_at": "2026-08-20T20:56:00Z",
                "status": "success",
            }
        ],
    }


def _payments_plan() -> dict[str, Any]:
    return {
        "format_version": "1.2",
        "terraform_version": "1.9.3",
        "resource_changes": [
            {
                "address": "module.payments.aws_db_parameter.pool_max_connections",
                "type": "aws_db_parameter",
                "provider_name": "registry.terraform.io/hashicorp/aws",
                "change": {
                    "actions": ["delete"],
                    "before": {
                        "name": "max_connections",
                        "value": "100",
                        "tags": {"service": "payments-api"},
                    },
                    "after": None,
                    "after_unknown": {},
                    "before_sensitive": {},
                    "after_sensitive": {},
                },
            }
        ],
    }


def _cdn_plan() -> dict[str, Any]:
    return {
        "format_version": "1.2",
        "terraform_version": "1.9.3",
        "resource_changes": [
            {
                "address": "module.cdn.aws_cloudfront_cache_policy.assets",
                "type": "aws_cloudfront_cache_policy",
                "provider_name": "registry.terraform.io/hashicorp/aws",
                "change": {
                    "actions": ["update"],
                    "before": {"default_ttl": 3600, "tags": {"service": "cdn-api"}},
                    "after": {"default_ttl": 0, "tags": {"service": "cdn-api"}},
                    "after_unknown": {},
                    "before_sensitive": {},
                    "after_sensitive": {},
                },
            }
        ],
    }


def _terraform_applies() -> list[dict[str, Any]]:
    return [
        {
            "apply_id": "apply-payments-pool-20260820-000130",
            "plan_file": "plan-payments-pool.json",
            "status": "success",
            "applied_at": "2026-08-20T00:01:30Z",
        },
        {
            "apply_id": "apply-cdn-cache-20260820-210130",
            "plan_file": "plan-cdn-cache.json",
            "status": "success",
            "applied_at": "2026-08-20T21:01:30Z",
        },
    ]


def _pod_status() -> list[dict[str, Any]]:
    return [
        {
            "metadata": {"uid": "search-indexer-pod-7f9d", "name": "search-api"},
            "status": {
                "containerStatuses": [
                    {
                        "name": "indexer",
                        "restartCount": 7,
                        "lastState": {
                            "terminated": {
                                "reason": "OOMKilled",
                                "exitCode": 137,
                                "finishedAt": "2026-08-20T07:05:00Z",
                            }
                        },
                    }
                ]
            },
        }
    ]


def _k8s_events() -> list[dict[str, Any]]:
    return [
        {
            "metadata": {"uid": "search-backoff-event-7f9d"},
            "involvedObject": {"name": "search-api"},
            "type": "Warning",
            "reason": "BackOff",
            "message": "back-off restarting failed container indexer",
            "count": 7,
            "lastTimestamp": "2026-08-20T07:06:00Z",
        }
    ]


def _write_json_lines(path: Path, events: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n" for event in events)
    content = "".join(lines)
    path.write_text(content, encoding="utf-8", newline="\n")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, indent=2, sort_keys=True) + "\n"
    path.write_text(content, encoding="utf-8", newline="\n")


def _write_json_array(path: Path, value: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, indent=2, sort_keys=True) + "\n"
    path.write_text(content, encoding="utf-8", newline="\n")


def _write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    generate()

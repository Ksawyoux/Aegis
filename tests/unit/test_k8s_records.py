from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from aegis.ingest.k8s import _event_records, _items, _pod_records
from aegis.ingest.normalize import ServiceRegistry


@dataclass
class _Service:
    id: int
    name: str = "search-api"
    repo: str | None = None
    log_keys: list[str] = field(default_factory=list)
    k8s_names: list[str] = field(default_factory=lambda: ["search-api"])
    infra_tags: dict[str, str] = field(default_factory=dict)


def _registry() -> ServiceRegistry:
    return ServiceRegistry.load([_Service(id=3)])


def test_pod_status_uses_terminated_reason_and_restart_count() -> None:
    records = _pod_records(
        [
            {
                "metadata": {"uid": "pod", "name": "search-api"},
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
        ],
        Path("pod-status.json"),
        _registry(),
    )

    draft, event_key = records[0]
    assert event_key is None
    assert draft.message == "OOMKilled: container indexer terminated (exit 137)"
    assert draft.attrs["restart_count"] == 7


def test_event_count_stays_on_one_record() -> None:
    records = _event_records(
        [
            {
                "metadata": {"uid": "event"},
                "involvedObject": {"name": "search-api"},
                "type": "Warning",
                "reason": "BackOff",
                "message": "retrying",
                "count": 7,
                "lastTimestamp": "2026-08-20T07:05:00Z",
            }
        ],
        Path("events.json"),
        _registry(),
    )

    draft, event_key = records[0]
    assert event_key == "event"
    assert draft.attrs["occurrence_count"] == 7
    assert draft.level == "warning"


def test_json_array_items_preserve_original_bytes_and_byte_offsets(tmp_path: Path) -> None:
    path = tmp_path / "events.json"
    first = '{"metadata":{"uid":"one"}}'
    second = '{"metadata":{"uid":"café"}}'
    path.write_text(f"[\n  {first},\n  {second}\n]\n", encoding="utf-8")

    items = _items(path)

    assert [item[0] for item in items] == [4, len(f"[\n  {first},\n  ".encode())]
    assert items[1][1].decode("utf-8") == second

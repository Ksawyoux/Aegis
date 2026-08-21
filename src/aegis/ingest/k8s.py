"""Kubernetes snapshot ingestion normalized into immutable log evidence."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, TypeAlias

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis.aggregate.rollups import capture_dirty_set, recompute
from aegis.db.models import LogEvent
from aegis.ingest.identity import k8s_event_uid, k8s_pod_uid
from aegis.ingest.logs import ResolvedDraft
from aegis.ingest.normalize import ServiceRegistry, canonical_level
from aegis.ingest.templates import template_hash


class KubernetesIngestError(ValueError):
    """A Kubernetes committed snapshot cannot be normalized safely."""


KubernetesItem: TypeAlias = dict[str, Any] | tuple[int, bytes, dict[str, Any]]


def ingest_kubernetes(session: Session, *, path: Path, registry: ServiceRegistry) -> int:
    """Ingest a pod-status or events JSON array and refresh dirty rollups."""
    items = _items(path)
    records = (
        _pod_records(items, path, registry)
        if path.name == "pod-status.json"
        else _event_records(items, path, registry)
    )
    dirty: set[tuple[int, datetime]] = set()
    inserted = 0
    for record, event_key in records:
        old = None
        if event_key is not None:
            old = session.scalar(
                select(LogEvent)
                .where(LogEvent.attrs["event_uid"].astext == event_key)
                .with_for_update()
            )
        if old is not None:
            if old.uid == record.uid:
                continue
            dirty.add((old.service_id, _minute(old.ts)))
            session.delete(old)
        existing = session.scalar(select(LogEvent).where(LogEvent.uid == record.uid))
        if existing is None:
            session.add(_event(record))
            dirty.add((record.service_id, _minute(record.ts)))
            inserted += 1
    session.flush()
    recompute(session, dirty=capture_dirty_set(session, changed=dirty))
    return inserted


def _items(path: Path) -> list[tuple[int, bytes, dict[str, Any]]]:
    """Decode an array while retaining each item's original byte range."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise KubernetesIngestError("Kubernetes input must be UTF-8 JSON") from error
    array_start = _items_array_start(text)
    decoder = json.JSONDecoder()
    index = array_start + 1
    result: list[tuple[int, bytes, dict[str, Any]]] = []
    while True:
        index = _skip_json_space(text, index)
        if index >= len(text) or text[index] == "]":
            break
        start = index
        try:
            value, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError as error:
            raise KubernetesIngestError("invalid Kubernetes JSON array") from error
        if not isinstance(value, dict):
            raise KubernetesIngestError("Kubernetes items must be objects")
        end = index
        result.append(
            (
                len(text[:start].encode("utf-8")),
                raw[len(text[:start].encode("utf-8")) : len(text[:end].encode("utf-8"))],
                value,
            )
        )
        index = _skip_json_space(text, index)
        if index < len(text) and text[index] == ",":
            index += 1
    return result


def _items_array_start(text: str) -> int:
    stripped = text.lstrip()
    if stripped.startswith("["):
        return len(text) - len(stripped)
    try:
        object_value = json.loads(text)
    except json.JSONDecodeError as error:
        raise KubernetesIngestError("invalid Kubernetes JSON") from error
    if not isinstance(object_value, dict) or not isinstance(object_value.get("items"), list):
        raise KubernetesIngestError("Kubernetes input must be an array or items object")
    match = re.search(r'"items"\s*:', text)
    if match is None:
        raise KubernetesIngestError("Kubernetes items array is absent")
    start = text.find("[", match.end())
    if start < 0:
        raise KubernetesIngestError("Kubernetes items array is absent")
    return start


def _skip_json_space(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


def _pod_records(
    items: Sequence[KubernetesItem],
    path: Path,
    registry: ServiceRegistry,
) -> list[tuple[ResolvedDraft, None]]:
    result: list[tuple[ResolvedDraft, None]] = []
    for index, item in enumerate(items):
        offset, source_raw, pod = _item_parts(item, index)
        metadata = _object(pod, "metadata")
        status = _object(pod, "status")
        resolution = registry.resolve_service(k8s_name=_string(metadata, "name"))
        if not resolution.resolved:
            continue
        service = resolution.service
        assert service is not None
        for container_status in status.get("containerStatuses", []):
            if not isinstance(container_status, dict):
                continue
            terminated = _terminated(container_status)
            if terminated is None:
                continue
            finished = _timestamp(terminated, "finishedAt")
            reason = _string(terminated, "reason")
            container = _string(container_status, "name")
            restart_count = _integer(container_status, "restartCount")
            exit_code = _integer(terminated, "exitCode")
            message = f"{reason}: container {container} terminated (exit {exit_code})"
            uid = k8s_pod_uid(
                pod_uid=_string(metadata, "uid"),
                container=container,
                restart_count=restart_count,
                finished_at=finished,
            )
            result.append(
                (
                    ResolvedDraft(
                        uid,
                        finished,
                        getattr(service, "id"),
                        "error",
                        None,
                        None,
                        message,
                        template_hash(message),
                        source_raw.decode("utf-8"),
                        {
                            "source": "k8s",
                            "kind": "PodStatus",
                            "reason": reason,
                            "restart_count": restart_count,
                            "exit_code": exit_code,
                        },
                        f"logs/{path.name}",
                        offset,
                    ),
                    None,
                )
            )
    return result


def _event_records(
    items: Sequence[KubernetesItem],
    path: Path,
    registry: ServiceRegistry,
) -> list[tuple[ResolvedDraft, str]]:
    result: list[tuple[ResolvedDraft, str]] = []
    for index, item in enumerate(items):
        offset, source_raw, event = _item_parts(item, index)
        metadata = _object(event, "metadata")
        involved = _object(event, "involvedObject")
        resolution = registry.resolve_service(k8s_name=_string(involved, "name"))
        if not resolution.resolved:
            continue
        service = resolution.service
        assert service is not None
        timestamp = _event_timestamp(event)
        reason, message = _string(event, "reason"), _string(event, "message")
        count = _integer(event, "count", default=1)
        event_uid = _string(metadata, "uid")
        full_message = f"{reason}: {message}"
        uid = k8s_event_uid(event_uid=event_uid, count=count, last_timestamp=timestamp)
        attrs = {
            "source": "k8s",
            "kind": "Event",
            "reason": reason,
            "occurrence_count": count,
            "event_uid": event_uid,
        }
        result.append(
            (
                ResolvedDraft(
                    uid,
                    timestamp,
                    getattr(service, "id"),
                    canonical_level(None, None) if event.get("type") != "Warning" else "warning",
                    None,
                    None,
                    full_message,
                    template_hash(full_message),
                    source_raw.decode("utf-8"),
                    attrs,
                    f"logs/{path.name}",
                    offset,
                ),
                event_uid,
            )
        )
    return result


def _item_parts(item: KubernetesItem, fallback_offset: int) -> tuple[int, bytes, dict[str, Any]]:
    if isinstance(item, tuple):
        return item
    return fallback_offset, json.dumps(item, sort_keys=True).encode("utf-8"), item


def _event(record: ResolvedDraft) -> LogEvent:
    return LogEvent(**record.__dict__)


def _terminated(value: dict[str, Any]) -> dict[str, Any] | None:
    last_state = value.get("lastState")
    return (
        last_state.get("terminated")
        if isinstance(last_state, dict) and isinstance(last_state.get("terminated"), dict)
        else None
    )


def _object(value: dict[str, Any], key: str) -> dict[str, Any]:
    item = value.get(key)
    if not isinstance(item, dict):
        raise KubernetesIngestError(f"missing object {key}")
    return item


def _string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise KubernetesIngestError(f"missing string {key}")
    return item


def _integer(value: dict[str, Any], key: str, *, default: int | None = None) -> int:
    item = value.get(key, default)
    if isinstance(item, bool) or not isinstance(item, int):
        raise KubernetesIngestError(f"missing integer {key}")
    return item


def _timestamp(value: dict[str, Any], key: str) -> datetime:
    try:
        item = datetime.fromisoformat(_string(value, key).replace("Z", "+00:00"))
    except ValueError as error:
        raise KubernetesIngestError(f"invalid timestamp {key}") from error
    if item.tzinfo is None or item.utcoffset() is None:
        raise KubernetesIngestError(f"timestamp {key} must be timezone aware")
    return item


def _event_timestamp(event: dict[str, Any]) -> datetime:
    raw_series = event.get("series")
    series: dict[str, Any] = raw_series if isinstance(raw_series, dict) else {}
    for key, value in (
        ("lastTimestamp", event),
        ("eventTime", event),
        ("lastObservedTime", series),
    ):
        if value.get(key) is not None:
            return _timestamp(value, key)
    raise KubernetesIngestError("event has no timestamp")


def _minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)

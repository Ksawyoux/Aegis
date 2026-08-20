"""Versioned, content-derived identifiers for evidence records."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from os import PathLike
from typing import Any

from aegis.ingest.textnorm import (
    normalize_environment,
    normalize_raw,
    normalize_source_file,
    normalize_source_offset,
    normalize_timestamp,
)

UID_VERSION = 1


def uid(kind: str, **fields: Any) -> str:
    """Return a canonical, type-preserving evidence identity.

    Callers of this primitive are responsible for applying the source-specific
    normalization rules in :mod:`aegis.ingest.textnorm` first.
    """
    payload = json.dumps(
        {"v": UID_VERSION, "kind": kind, **fields},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def log_uid(*, file: str | PathLike[str], offset: int, raw: bytes | str) -> str:
    """Return the UID for one log line using its source bytes and byte offset."""
    return uid(
        "log",
        file=normalize_source_file(file),
        offset=normalize_source_offset(offset),
        raw=normalize_raw(raw),
    )


def deployment_uid(*, commit_sha: str, environment: str, started_at: datetime) -> str:
    """Return the immutable identity for a deployment lifecycle."""
    return uid(
        "deploy",
        commit_sha=commit_sha,
        environment=normalize_environment(environment),
        started_at=normalize_timestamp(started_at),
    )


def infra_change_uid(
    *,
    apply_id: str,
    resource_type: str,
    resource_name: str,
    action: str,
    provider: str,
) -> str:
    """Return the identity for one immutable infrastructure change."""
    return uid(
        "infra",
        apply_id=apply_id,
        resource_type=resource_type,
        resource_name=resource_name,
        action=action,
        provider=provider,
    )


def k8s_event_uid(*, event_uid: str, count: int, last_timestamp: datetime) -> str:
    """Return the identity for one Kubernetes Event observation."""
    return uid(
        "k8s-event",
        event_uid=event_uid,
        count=count,
        last_timestamp=normalize_timestamp(last_timestamp),
    )


def k8s_pod_uid(
    *, pod_uid: str, container: str, restart_count: int, finished_at: datetime
) -> str:
    """Return the identity for one Kubernetes container status observation."""
    return uid(
        "k8s-pod",
        pod_uid=pod_uid,
        container=container,
        restart_count=restart_count,
        finished_at=normalize_timestamp(finished_at),
    )


def _json_default(value: object) -> str:
    """Serialize the sole non-JSON identity input type without repr fallbacks."""
    if isinstance(value, datetime):
        return normalize_timestamp(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

"""Ingest successful Terraform applies as immutable infrastructure evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis.db.models import InfraChange
from aegis.ingest.identity import infra_change_uid
from aegis.ingest.normalize import ServiceRegistry
from aegis.ingest.pipeline import EvidenceConflict


class TerraformIngestError(ValueError):
    """A committed Terraform artifact is malformed or unsupported."""


def ingest_terraform(
    session: Session, *, plan_path: Path, applies_path: Path, registry: ServiceRegistry
) -> int:
    """Load only successful applies that explicitly reference ``plan_path``."""
    applies = _load_array(applies_path)
    matching = [
        item
        for item in applies
        if item.get("plan_file") == plan_path.name and item.get("status") == "success"
    ]
    if not matching:
        return 0
    plan = _load_object(plan_path)
    changes = plan.get("resource_changes")
    if not isinstance(changes, list):
        raise TerraformIngestError("plan resource_changes must be an array")
    inserted = 0
    for apply in matching:
        apply_id = _string(apply, "apply_id")
        applied_at = _timestamp(apply, "applied_at")
        for resource in changes:
            if not isinstance(resource, dict):
                raise TerraformIngestError("resource change must be an object")
            row = _resource_change(resource, apply_id, applied_at, registry)
            if row is None:
                continue
            existing = session.scalar(select(InfraChange).where(InfraChange.uid == row.uid))
            if existing is None:
                session.add(row)
                inserted += 1
            elif _infra_values(existing) != _infra_values(row):
                raise EvidenceConflict(row.uid, tuple(_infra_values(row)))
    return inserted


def _resource_change(
    resource: Mapping[str, Any], apply_id: str, applied_at: datetime, registry: ServiceRegistry
) -> InfraChange | None:
    change = resource.get("change")
    if not isinstance(change, dict):
        raise TerraformIngestError("resource change missing change")
    actions = change.get("actions")
    if actions == ["no-op"]:
        return None
    action = _action(actions)
    resource_type = _string(resource, "type")
    resource_name = _string(resource, "address")
    provider = str(resource.get("provider_name", "terraform"))
    raw_before = change.get("before")
    raw_after = change.get("after")
    raw_unknown = change.get("after_unknown")
    before: dict[str, Any] = raw_before if isinstance(raw_before, dict) else {}
    after: dict[str, Any] = raw_after if isinstance(raw_after, dict) else {}
    unknown: dict[str, Any] = raw_unknown if isinstance(raw_unknown, dict) else {}
    sensitive = _merge_sensitive(change.get("before_sensitive"), change.get("after_sensitive"))
    diff = _diff(before, after, unknown, sensitive)
    after_tags = after.get("tags")
    service_id = _service_id(after_tags, registry)
    if service_id is None:
        service_id = _service_id(before.get("tags"), registry)
    uid = infra_change_uid(
        apply_id=apply_id,
        resource_type=resource_type,
        resource_name=resource_name,
        action=action,
        provider=provider,
    )
    return InfraChange(
        uid=uid,
        provider=provider,
        resource_type=resource_type,
        resource_name=resource_name,
        resource_id=resource.get("id") if isinstance(resource.get("id"), str) else None,
        action=action,
        attribute_diff=diff,
        applied_at=applied_at,
        apply_id=apply_id,
        source_ref=None,
        service_id=service_id,
    )


def _action(value: object) -> str:
    if value in (["create"], ["update"], ["delete"]):
        assert isinstance(value, list)
        action = value[0]
        assert isinstance(action, str)
        return action
    if value in (["create", "delete"], ["delete", "create"]):
        return "replace"
    raise TerraformIngestError(f"unsupported Terraform actions: {value!r}")


def _diff(before: Any, after: Any, unknown: Any, sensitive: Any) -> dict[str, Any]:
    if sensitive is True:
        return {"before": "<redacted>", "after": "<redacted>"}
    if unknown is True:
        return {}
    if isinstance(before, list) or isinstance(after, list):
        # Terraform marks sensitivity element-wise inside a list, and list order
        # is not stable between plans, so recursing positionally could pair a
        # secret element with a non-secret one. Redacting the whole list is the
        # only reduction that cannot leak; an over-redacted diff is recoverable,
        # a persisted credential is not.
        if _contains_sensitive(sensitive):
            return {"before": "<redacted>", "after": "<redacted>"} if before != after else {}
        if before != after:
            return {"before": before, "after": after}
        return {}
    if isinstance(before, dict) or isinstance(after, dict):
        result: dict[str, Any] = {}
        keys = set(before if isinstance(before, dict) else {}) | set(
            after if isinstance(after, dict) else {}
        )
        for key in sorted(keys):
            nested = _diff(
                before.get(key) if isinstance(before, dict) else None,
                after.get(key) if isinstance(after, dict) else None,
                unknown.get(key) if isinstance(unknown, dict) else None,
                sensitive.get(key) if isinstance(sensitive, dict) else None,
            )
            if nested:
                result[key] = nested
        return result
    if before != after:
        return {"before": before, "after": after}
    return {}


def _contains_sensitive(value: object) -> bool:
    """Return whether any node in a sensitivity subtree is marked sensitive."""
    if value is True:
        return True
    if isinstance(value, dict):
        return any(_contains_sensitive(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    return False


def _merge_sensitive(before: object, after: object) -> object:
    if before is True or after is True:
        return True
    if isinstance(before, list) or isinstance(after, list):
        before_items: list[Any] = before if isinstance(before, list) else []
        after_items: list[Any] = after if isinstance(after, list) else []
        return [
            _merge_sensitive(
                before_items[index] if index < len(before_items) else None,
                after_items[index] if index < len(after_items) else None,
            )
            for index in range(max(len(before_items), len(after_items)))
        ]
    if isinstance(before, dict) or isinstance(after, dict):
        left = before if isinstance(before, dict) else {}
        right = after if isinstance(after, dict) else {}
        return {
            key: _merge_sensitive(left.get(key), right.get(key)) for key in set(left) | set(right)
        }
    return False


def _service_id(tags: object, registry: ServiceRegistry) -> int | None:
    if not isinstance(tags, dict):
        return None
    resolution = registry.resolve_service(infra_tags=tags)
    if not resolution.resolved:
        return None
    service = resolution.service
    assert service is not None
    return getattr(service, "id", None)


def _load_array(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise TerraformIngestError(f"{path} must contain an object array")
    return value


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TerraformIngestError(f"{path} must contain an object")
    return value


def _string(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise TerraformIngestError(f"missing string {key}")
    return item


def _timestamp(value: Mapping[str, Any], key: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_string(value, key).replace("Z", "+00:00"))
    except ValueError as error:
        raise TerraformIngestError(f"invalid timestamp {key}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TerraformIngestError(f"timestamp {key} must be timezone-aware")
    return parsed


def _infra_values(value: InfraChange) -> dict[str, Any]:
    return {
        key: getattr(value, key)
        for key in (
            "provider",
            "resource_type",
            "resource_name",
            "action",
            "attribute_diff",
            "applied_at",
            "apply_id",
            "service_id",
        )
    }

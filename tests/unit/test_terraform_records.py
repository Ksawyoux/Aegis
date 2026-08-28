from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from aegis.ingest.normalize import ServiceRegistry
from aegis.ingest.terraform import _resource_change


@dataclass
class _Service:
    id: int
    name: str = "payments-api"
    repo: str | None = None
    log_keys: list[str] = field(default_factory=list)
    k8s_names: list[str] = field(default_factory=list)
    infra_tags: dict[str, str] = field(default_factory=lambda: {"service": "payments-api"})


def test_terraform_change_uses_full_address_and_before_tag_fallback() -> None:
    row = _resource_change(
        {
            "address": 'module.payments.aws_db_instance.pool["a"]',
            "type": "aws_db_instance",
            "provider_name": "aws",
            "change": {
                "actions": ["create", "delete"],
                "before": {"tags": {"service": "payments-api"}, "password": "old"},
                "after": {"tags": {"service": "other"}, "password": "new"},
                "after_unknown": {},
                "before_sensitive": {"password": True},
                "after_sensitive": {"password": True},
            },
        },
        "apply-a",
        datetime(2026, 8, 20, tzinfo=UTC),
        ServiceRegistry.load([_Service(id=7)]),
    )

    assert row is not None
    assert row.action == "replace"
    assert row.resource_name == 'module.payments.aws_db_instance.pool["a"]'
    assert row.service_id == 7
    assert row.attribute_diff["password"] == {"before": "<redacted>", "after": "<redacted>"}

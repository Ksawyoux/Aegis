from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from aegis.ingest.normalize import (
    AmbiguousServiceMapping,
    ServiceRegistry,
    canonical_level,
    status_class,
)


@dataclass
class ServiceRecord:
    name: str
    repo: str | None = None
    log_keys: list[str] = field(default_factory=list)
    k8s_names: list[str] = field(default_factory=list)
    infra_tags: dict[str, str] = field(default_factory=dict)


def test_two_services_with_empty_infra_tags_load_fine() -> None:
    first = ServiceRecord(name="api")
    second = ServiceRecord(name="worker")

    registry = ServiceRegistry.load([first, second])

    assert registry.resolve_service(infra_tags={"team": "payments"}).reason == "no_service_match"


def test_compatible_non_subset_tag_maps_rejected() -> None:
    with pytest.raises(AmbiguousServiceMapping, match="infra_tags"):
        ServiceRegistry.load(
            [
                ServiceRecord(name="payments", infra_tags={"team": "payments"}),
                ServiceRecord(name="production", infra_tags={"environment": "prod"}),
            ]
        )


def test_explicit_name_miss_does_not_fall_through() -> None:
    api = ServiceRecord(name="api", log_keys=["api-logger"])
    registry = ServiceRegistry.load([api])

    result = registry.resolve_service(name="unknown", log_key="api-logger")

    assert result.service is None
    assert result.reason == "no_service_match"


def test_statefulset_ordinal_and_replicaset_hash_stripped() -> None:
    deployment = ServiceRecord(name="api", k8s_names=["api"])
    statefulset = ServiceRecord(name="database", k8s_names=["database"])
    registry = ServiceRegistry.load([deployment, statefulset])

    assert registry.resolve_service(k8s_name="api-abc123def0-z9y8x").service is deployment
    assert registry.resolve_service(k8s_name="database-12").service is statefulset


def test_service_named_worker_7_not_mangled() -> None:
    worker = ServiceRecord(name="worker", k8s_names=["worker"])
    worker_7 = ServiceRecord(name="worker-7", k8s_names=["worker-7"])
    registry = ServiceRegistry.load([worker, worker_7])

    assert registry.resolve_service(k8s_name="worker-7").service is worker_7


def test_status_600_consistent_between_level_and_class() -> None:
    assert canonical_level(status_code=600) == "info"
    assert status_class(600) == "none"


def test_duplicate_log_key_across_services_is_rejected() -> None:
    with pytest.raises(AmbiguousServiceMapping, match="log_key"):
        ServiceRegistry.load(
            [
                ServiceRecord(name="api", log_keys=["shared"]),
                ServiceRecord(name="worker", log_keys=["shared"]),
            ]
        )


def test_resolution_order_prefers_name_over_log_key() -> None:
    name_holder = ServiceRecord(name="shared")
    key_holder = ServiceRecord(name="worker", log_keys=["shared"])
    registry = ServiceRegistry.load([name_holder, key_holder])

    assert registry.resolve_service(name="shared", log_key="shared").service is name_holder


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("trace", "debug"),
        ("DEBUG", "debug"),
        ("info", "info"),
        ("notice", "info"),
        ("information", "info"),
        ("warn", "warning"),
        ("warning", "warning"),
        ("err", "error"),
        ("error", "error"),
        ("severe", "error"),
        ("fatal", "fatal"),
        ("critical", "fatal"),
        ("crit", "fatal"),
        ("panic", "fatal"),
        ("emerg", "fatal"),
        ("alert", "fatal"),
    ],
)
def test_canonical_level_maps_every_level_alias(raw: str, expected: str) -> None:
    assert canonical_level(raw) == expected

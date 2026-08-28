"""Offline contracts for the four Part 2 falsification scenarios."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

from aegis.db.models import Service
from aegis.ingest.git import GitExport, load_git_export
from aegis.ingest.identity import deployment_uid, infra_change_uid
from aegis.ingest.k8s import _event_records, _items, _pod_records
from aegis.ingest.logs import ParseContext, ResolvedDraft, detect_format, iter_drafts
from aegis.ingest.normalize import ServiceRegistry, status_class
from aegis.mcp_server.citations import (
    format_commit,
    format_deploy,
    format_infra,
    format_rollup,
    is_wellformed,
)

ROOT = Path(__file__).parents[2]
CORPUS = ROOT / "corpus"
PART2_NAMES = (
    "payments-pool-exhaustion",
    "search-oom-crashloop",
    "auth-token-expiry",
    "cdn-cache-miss-storm",
)
EXPECTED_PHYSICAL_LINES = {
    "payments-api.log": 261,
    "search-api.log": 440,
    "auth-api.log": 261,
    "cdn-api.log": 261,
}


def test_part2_logs_match_manifest_and_have_dense_baselines() -> None:
    manifest = _yaml_mapping(CORPUS / "logs" / "manifest.yaml")
    scenarios = _scenarios_by_service()
    registry = _registry()

    for log_name, expected_lines in EXPECTED_PHYSICAL_LINES.items():
        declaration = cast(dict[str, Any], manifest[log_name])
        service_name = str(declaration["service"])
        path = CORPUS / "logs" / log_name
        assert detect_format(path).name == declaration["format"]
        assert sum(1 for _ in path.open(encoding="utf-8")) == expected_lines

        drafts = list(iter_drafts(path, _log_context(log_name, declaration, registry)))
        assert drafts
        assert all(isinstance(draft, ResolvedDraft) for draft in drafts)
        resolved = cast(list[ResolvedDraft], drafts)
        assert all(draft.service_id == _service_id(registry, service_name) for draft in resolved)

        scenario = scenarios[service_name]
        start = _timestamp(scenario["alert"]["window_start"])
        end = _timestamp(scenario["alert"]["window_end"])
        baseline_start = start - (end - start)
        baseline = [draft for draft in resolved if baseline_start <= draft.ts < start]
        assert len(baseline) >= 50, f"{service_name} would report baseline_sparse"


def test_search_traceback_is_multiline_and_oomkilled_comes_only_from_pod_status() -> None:
    registry = _registry()
    log_path = CORPUS / "logs" / "search-api.log"
    manifest = _yaml_mapping(CORPUS / "logs" / "manifest.yaml")
    drafts = list(
        iter_drafts(log_path, _log_context(log_path.name, manifest[log_path.name], registry))
    )
    traceback = next(
        draft
        for draft in drafts
        if isinstance(draft, ResolvedDraft) and draft.attrs.get("exc_type") == "MemoryError"
    )
    assert "Traceback (most recent call last):\n" in traceback.raw
    assert len(traceback.raw.splitlines()) == 5
    assert "OOMKilled" not in log_path.read_text(encoding="utf-8")

    pod_path = CORPUS / "k8s" / "pod-status.json"
    pod_records = _pod_records(_items(pod_path), pod_path, registry)
    assert len(pod_records) == 1
    pod_draft, event_key = pod_records[0]
    assert event_key is None
    assert pod_draft.message == "OOMKilled: container indexer terminated (exit 137)"
    assert pod_draft.attrs == {
        "source": "k8s",
        "kind": "PodStatus",
        "reason": "OOMKilled",
        "restart_count": 7,
        "exit_code": 137,
    }

    event_path = CORPUS / "k8s" / "events.json"
    event_records = _event_records(_items(event_path), event_path, registry)
    assert len(event_records) == 1
    event_draft, event_key = event_records[0]
    assert event_key == "search-backoff-event-7f9d"
    assert event_draft.attrs["occurrence_count"] == 7
    assert "OOMKilled" not in event_draft.raw


def test_payments_has_successful_terraform_cause_and_no_deployment() -> None:
    plan, apply = _terraform_evidence("plan-payments-pool.json")
    resource = plan["resource_changes"][0]
    assert apply["status"] == "success"
    assert resource["address"].endswith("pool_max_connections")
    assert resource["change"]["actions"] == ["delete"]
    assert resource["change"]["before"]["value"] == "100"
    assert resource["change"]["after"] is None
    assert resource["change"]["before"]["tags"] == {"service": "payments-api"}

    payment_exports = [export for export in _git_exports() if export.repo == "acme/payments"]
    assert not payment_exports or all(not export.deploys for export in payment_exports)


def test_auth_cause_is_only_exposed_by_the_refactor_hunk() -> None:
    export = load_git_export(CORPUS / "git" / "auth.json")
    causal = next(commit for commit in export.commits if commit.sha == "e" * 40)
    assert causal.message == "refactor"
    hunks = "\n".join(change.hunks or "" for change in causal.files_changed)
    assert "-    return expires_at < now" in hunks
    assert "+    return expires_at <= now" in hunks
    assert "<=" not in causal.message


def test_cdn_has_terraform_cause_and_an_unrelated_in_window_deploy() -> None:
    plan, apply = _terraform_evidence("plan-cdn-cache.json")
    resource = plan["resource_changes"][0]
    assert apply["status"] == "success"
    assert resource["change"]["before"]["default_ttl"] == 3600
    assert resource["change"]["after"]["default_ttl"] == 0

    export = load_git_export(CORPUS / "git" / "cdn.json")
    assert len(export.deploys) == 1
    distractor = export.deploys[0]
    assert distractor.commit_sha == "1" * 40
    scenario = _scenario("cdn-cache-miss-storm")
    assert _timestamp(scenario["alert"]["window_start"]) <= distractor.started_at
    assert distractor.started_at < _timestamp(scenario["alert"]["window_end"])
    commit = export.commits[0]
    assert commit.message == "enable brotli responses"
    assert all("cache" not in (change.hunks or "").lower() for change in commit.files_changed)


def test_part2_must_citations_are_derived_from_committed_evidence() -> None:
    derived = {
        "payments-pool-exhaustion": {
            _infra_citation("plan-payments-pool.json"),
            _log_rollup_citation("payments-pool-exhaustion", 503),
        },
        "search-oom-crashloop": {
            _deploy_citation("search.json", "c" * 40),
            _pod_rollup_citation(),
        },
        "auth-token-expiry": {
            format_commit("e" * 40),
            _log_rollup_citation("auth-token-expiry", 401),
        },
        "cdn-cache-miss-storm": {
            _infra_citation("plan-cdn-cache.json"),
            _log_rollup_citation("cdn-cache-miss-storm", 504),
        },
    }

    for name, citations in derived.items():
        declared = set(_scenario(name)["expect"]["must_cite"])
        assert declared == citations
        assert all(is_wellformed(citation) for citation in declared)


def _registry() -> ServiceRegistry:
    configured = _yaml_list(CORPUS / "services.yaml")
    services = [
        Service(
            id=index,
            name=str(item["name"]),
            repo=str(item["repo"]),
            log_keys=list(item.get("log_keys", [])),
            k8s_names=list(item.get("k8s_names", [])),
            infra_tags=dict(item.get("infra_tags", {})),
            log_timezone=str(item.get("log_timezone", "UTC")),
        )
        for index, item in enumerate(configured, start=1)
    ]
    return ServiceRegistry.load(services)


def _service_id(registry: ServiceRegistry, name: str) -> int:
    resolution = registry.resolve_service(name=name)
    assert resolution.resolved and resolution.service is not None
    service_id = getattr(resolution.service, "id")
    assert isinstance(service_id, int)
    return service_id


def _log_context(
    log_name: str, declaration: object, registry: ServiceRegistry
) -> ParseContext:
    assert isinstance(declaration, dict)
    return ParseContext(
        registry=registry,
        source_file=f"logs/{log_name}",
        default_log_timezone=str(declaration["timezone"]),
        declared_service=str(declaration["service"]),
    )


def _scenarios_by_service() -> dict[str, dict[str, Any]]:
    return {
        str(scenario["alert"]["service"]): scenario
        for scenario in (_scenario(name) for name in PART2_NAMES)
    }


def _scenario(name: str) -> dict[str, Any]:
    return _yaml_mapping(CORPUS / "scenarios" / f"{name}.yaml")


def _git_exports() -> Iterator[GitExport]:
    for path in sorted((CORPUS / "git").glob("*.json")):
        yield load_git_export(path)


def _terraform_evidence(plan_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = cast(
        dict[str, Any], json.loads((CORPUS / "terraform" / plan_name).read_text(encoding="utf-8"))
    )
    applies = cast(
        list[dict[str, Any]],
        json.loads((CORPUS / "terraform" / "applies.json").read_text(encoding="utf-8")),
    )
    apply = next(item for item in applies if item["plan_file"] == plan_name)
    return plan, apply


def _infra_citation(plan_name: str) -> str:
    plan, apply = _terraform_evidence(plan_name)
    resource = plan["resource_changes"][0]
    actions = resource["change"]["actions"]
    action = actions[0] if len(actions) == 1 else "replace"
    uid = infra_change_uid(
        apply_id=apply["apply_id"],
        resource_type=resource["type"],
        resource_name=resource["address"],
        action=action,
        provider=resource["provider_name"],
    )
    return format_infra(uid)


def _deploy_citation(export_name: str, sha: str) -> str:
    export = load_git_export(CORPUS / "git" / export_name)
    deploy = next(item for item in export.deploys if item.commit_sha == sha)
    uid = deployment_uid(
        commit_sha=deploy.commit_sha,
        environment=deploy.environment,
        started_at=deploy.started_at,
    )
    return format_deploy(uid)


def _log_rollup_citation(scenario_name: str, failure_status: int) -> str:
    scenario = _scenario(scenario_name)
    service_name = str(scenario["alert"]["service"])
    manifest = _yaml_mapping(CORPUS / "logs" / "manifest.yaml")
    log_name, declaration = next(
        (name, value)
        for name, value in manifest.items()
        if isinstance(value, dict) and value.get("service") == service_name
    )
    start = _timestamp(scenario["alert"]["window_start"])
    drafts = iter_drafts(
        CORPUS / "logs" / log_name,
        _log_context(log_name, declaration, _registry()),
    )
    draft = next(
        item
        for item in drafts
        if isinstance(item, ResolvedDraft)
        and item.status_code == failure_status
        and item.ts >= start
    )
    return format_rollup(
        service_name,
        draft.ts.replace(second=0, microsecond=0),
        status_class(draft.status_code),
        draft.level,
        draft.template_hash,
    )


def _pod_rollup_citation() -> str:
    path = CORPUS / "k8s" / "pod-status.json"
    draft, _ = _pod_records(_items(path), path, _registry())[0]
    return format_rollup(
        "search-api",
        draft.ts.replace(second=0, microsecond=0),
        status_class(draft.status_code),
        draft.level,
        draft.template_hash,
    )


def _timestamp(value: object) -> datetime:
    assert isinstance(value, str)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _yaml_mapping(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _yaml_list(path: Path) -> list[dict[str, Any]]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, list)
    return cast(list[dict[str, Any]], value)

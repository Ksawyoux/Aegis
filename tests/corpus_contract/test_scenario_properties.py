"""Contract checks for facts that must be present in the committed corpus."""

from __future__ import annotations

import json
import runpy
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import yaml

from aegis.db.models import Service
from aegis.ingest.git import load_git_export
from aegis.ingest.logs import ParseContext, ResolvedDraft, UnresolvedDraft, iter_drafts
from aegis.ingest.normalize import ServiceRegistry
from aegis.ingest.templates import template_hash

ROOT = Path(__file__).parents[2]
CORPUS = ROOT / "corpus"
SCENARIO_PATH = CORPUS / "scenarios" / "checkout-5xx-spike.yaml"
LOG_PATH = CORPUS / "logs" / "checkout-api.log"
GIT_PATH = CORPUS / "git" / "checkout.json"


def test_generator_is_byte_identical_across_two_fresh_outputs(tmp_path: Path) -> None:
    namespace = runpy.run_path(str(CORPUS / "generate.py"))
    generate = cast(Callable[[Path], None], namespace["generate"])
    first = tmp_path / "first"
    second = tmp_path / "second"

    generate(first)
    generate(second)

    for relative_path in (Path("logs/checkout-api.log"), Path("git/checkout.json")):
        assert (first / relative_path).read_bytes() == (second / relative_path).read_bytes()
        assert (first / relative_path).read_bytes() == (CORPUS / relative_path).read_bytes()


def test_committed_corpus_has_all_six_scenario_properties() -> None:
    scenario = _scenario()
    service = _service()
    drafts = _drafts(service)
    resolved = [draft for draft in drafts if isinstance(draft, ResolvedDraft)]
    export = load_git_export(GIT_PATH)
    causal = next(commit for commit in export.commits if commit.message == "refactor client config")
    causal_deploy = next(deploy for deploy in export.deploys if deploy.commit_sha == causal.sha)
    window_start = _timestamp(scenario["alert"]["window_start"])
    window_end = _timestamp(scenario["alert"]["window_end"])

    # 1. A full equal-length baseline exists immediately before the alert window.
    baseline_start = window_start - (window_end - window_start)
    assert any(baseline_start <= draft.ts < window_start for draft in resolved)

    # 2. A 5xx burst starts 90 seconds after the causal production deploy.
    spike_start = causal_deploy.started_at + timedelta(seconds=90)
    counts_by_minute = Counter(
        draft.ts.replace(second=0, microsecond=0)
        for draft in resolved
        if draft.status_code is not None and draft.status_code >= 500
    )
    assert counts_by_minute[spike_start] >= 10
    assert max(counts_by_minute[minute] for minute in counts_by_minute if minute < spike_start) < 10
    assert min(minute for minute, count in counts_by_minute.items() if count >= 10) == spike_start

    # 3. The causal commit has a neutral message; only its hunk states the cause.
    assert causal.message == "refactor client config"
    causal_hunks = [file.hunks for file in causal.files_changed if file.hunks is not None]
    assert any("timeout: 30s" in hunk and "timeout: 3s" in hunk for hunk in causal_hunks)

    # 4. A separate, substantive deploy is present during the incident window.
    distractor = next(
        deploy
        for deploy in export.deploys
        if deploy.commit_sha != causal.sha and window_start <= deploy.started_at < window_end
    )
    distractor_commit = next(
        commit for commit in export.commits if commit.sha == distractor.commit_sha
    )
    assert distractor.status == "success"
    assert any(
        file.hunks is not None and "metrics" in file.hunks
        for file in distractor_commit.files_changed
    )

    # 5. A successful and a failing response deliberately share one template hash.
    _assert_shared_200_504_template()

    # 6. At least one line reaches the unresolved-event path with required raw evidence.
    unresolved = [draft for draft in drafts if isinstance(draft, UnresolvedDraft)]
    assert any(
        draft.reason == "no_service_match" and draft.raw and draft.source_file
        for draft in unresolved
    )


def test_scenario_keeps_canary_inside_expect_only() -> None:
    scenario = _scenario()
    canary = scenario["expect"]["canary"]

    assert canary == "GROUND-TRUTH-CANARY-DO-NOT-LEAK"
    assert canary not in yaml.safe_dump(scenario["alert"])
    assert canary not in LOG_PATH.read_text(encoding="utf-8")
    assert canary not in GIT_PATH.read_text(encoding="utf-8")


def _assert_shared_200_504_template() -> None:
    lines = [json.loads(line) for line in LOG_PATH.read_text(encoding="utf-8").splitlines()]
    successful = [line for line in lines if line.get("status") == 200]
    failures = [line for line in lines if line.get("status") == 504]
    pair = next(
        (
            (success, failure)
            for success in successful
            for failure in failures
            if isinstance(success.get("msg"), str)
            and isinstance(failure.get("msg"), str)
            and template_hash(success["msg"]) == template_hash(failure["msg"])
        ),
        None,
    )

    assert pair is not None
    success, failure = pair
    assert success["status"] != failure["status"]
    assert template_hash(success["msg"]) == template_hash(failure["msg"])


def _drafts(service: Service) -> list[ResolvedDraft | UnresolvedDraft]:
    context = ParseContext(
        registry=ServiceRegistry.load([service]),
        source_file="logs/checkout-api.log",
        default_log_timezone="UTC",
    )
    return list(iter_drafts(LOG_PATH, context))


def _service() -> Service:
    configured = _services()[0]
    return Service(
        id=1,
        name=str(configured["name"]),
        repo=str(configured["repo"]),
        log_keys=list(configured["log_keys"]),
        k8s_names=list(configured["k8s_names"]),
        infra_tags={},
        log_timezone=str(configured["log_timezone"]),
    )


def _scenario() -> dict[str, Any]:
    value = yaml.safe_load(SCENARIO_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _services() -> list[dict[str, Any]]:
    value = yaml.safe_load((CORPUS / "services.yaml").read_text(encoding="utf-8"))
    assert isinstance(value, list)
    return cast(list[dict[str, Any]], value)


def _timestamp(value: object) -> datetime:
    assert isinstance(value, str)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)

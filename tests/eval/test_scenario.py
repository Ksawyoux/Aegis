"""The evaluation that asserts v0.1's falsifiable claim.

    A single agent, using only the two final-shaped aggregate MCP tools over a
    real stdio boundary, correlates a deployment hunk with a statistically
    visible telemetry change and produces the correct structured claim using
    only citations returned during that run.

This is the only suite that spends model tokens, so it is opt-in: it skips
unless ``ANTHROPIC_API_KEY`` is set.

Assertions follow the resolved question in specification section 13: a required
semantic subset plus location-specific forbidden conditions, never exact
citation equality. The agent is non-deterministic, so demanding an exact
citation set would flake without measuring anything the claim depends on.
Exact-payload regression belongs in the tool tests, which are deterministic.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Generator, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from aegis.agent.summary import Claim, IncidentSummary, ProvenanceError, validate_provenance
from aegis.app.investigate import build_investigation_request, investigate
from aegis.app.run_context import InMemorySink, RunContext

ROOT = Path(__file__).parents[2]
SCENARIO_PATH = ROOT / "corpus" / "scenarios" / "checkout-5xx-spike.yaml"

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

requires_api_key = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY is not set; this suite spends model tokens",
)


@pytest.fixture
def scenario() -> dict[str, Any]:
    return dict(yaml.safe_load(SCENARIO_PATH.read_text()))


@pytest.fixture
def run_context() -> RunContext:
    return RunContext(run_id="eval-checkout-5xx-spike", sink=InMemorySink())


@pytest.fixture
def summary(
    seeded_session: Session, scenario: dict[str, Any], run_context: RunContext
) -> IncidentSummary:
    """Run the agent once and share the result across the assertions below."""
    return investigate(build_investigation_request(scenario), run_context)


def _all_claims(summary: IncidentSummary) -> list[Claim]:
    return [summary.root_cause, *summary.ruled_out, *summary.similar_incidents]


def _all_cites(summary: IncidentSummary) -> list[str]:
    cites = [cite for claim in _all_claims(summary) for cite in claim.cites]
    cites.extend(cite for entry in summary.timeline for cite in entry.cites)
    return cites


def _matches_any(pattern: str, values: Sequence[str]) -> bool:
    """Expected citations may be globs, because a rollup minute is not fixed."""
    return any(fnmatch.fnmatch(value, pattern) for value in values)


@requires_api_key
def test_summary_names_the_right_service(
    summary: IncidentSummary, scenario: dict[str, Any]
) -> None:
    assert summary.service == scenario["expect"]["service"]


@requires_api_key
def test_root_cause_describes_the_causal_change(
    summary: IncidentSummary, scenario: dict[str, Any]
) -> None:
    statement = summary.root_cause.statement.lower()
    missing = [
        term for term in scenario["expect"]["root_cause_contains"] if term.lower() not in statement
    ]
    assert not missing, f"root cause omits {missing}: {summary.root_cause.statement!r}"


@requires_api_key
def test_required_evidence_is_cited(summary: IncidentSummary, scenario: dict[str, Any]) -> None:
    """A required subset, not an exact set: extra valid citations are fine."""
    cites = _all_cites(summary)
    missing = [
        pattern for pattern in scenario["expect"]["must_cite"] if not _matches_any(pattern, cites)
    ]
    assert not missing, f"required evidence not cited: {missing}"


@requires_api_key
def test_distractor_is_ruled_out_and_never_the_cause(
    summary: IncidentSummary, scenario: dict[str, Any]
) -> None:
    """The scenario's whole point: a plausible non-causal deploy sits in the window."""
    ruled_out_text = " ".join(claim.statement for claim in summary.ruled_out).lower()
    for needle in scenario["expect"]["ruled_out_contains"]:
        assert needle.lower() in ruled_out_text, f"distractor {needle} not addressed in ruled_out"

    root_cause_text = summary.root_cause.statement.lower()
    for forbidden in scenario["expect"]["forbidden_root_cause"]:
        assert forbidden.lower() not in root_cause_text, (
            f"distractor {forbidden} was named as the root cause"
        )


@requires_api_key
def test_confidence_meets_the_floor(summary: IncidentSummary, scenario: dict[str, Any]) -> None:
    floor = scenario["expect"]["min_confidence"]
    assert CONFIDENCE_RANK[summary.confidence] >= CONFIDENCE_RANK[floor]


@requires_api_key
def test_every_citation_was_captured_during_this_run(
    summary: IncidentSummary, run_context: RunContext
) -> None:
    """Provenance already gates the run; assert it rather than trusting it silently."""
    validate_provenance(summary, run_context.captured_cites)
    assert run_context.captured_cites, "no tool result was captured"


@requires_api_key
def test_a_fabricated_citation_would_have_aborted_the_run(
    summary: IncidentSummary, run_context: RunContext
) -> None:
    """The negative control for the assertion above.

    Without this, ``test_every_citation_was_captured_during_this_run`` passes
    trivially if ``validate_provenance`` ever stops checking anything.
    """
    poisoned = summary.model_copy(
        update={
            "root_cause": Claim(
                statement=summary.root_cause.statement,
                cites=["log:" + "f" * 32],
            )
        }
    )
    with pytest.raises(ProvenanceError):
        validate_provenance(poisoned, run_context.captured_cites)


@requires_api_key
def test_ground_truth_canary_never_reached_the_model(
    summary: IncidentSummary, scenario: dict[str, Any], run_context: RunContext
) -> None:
    """The canary exists only in ``expect``; the brief allowlist must exclude it."""
    canary = scenario["expect"]["canary"]
    trace = run_context.to_json()
    assert canary not in str(trace), "ground-truth canary leaked into the run trace"


def test_brief_allowlist_excludes_ground_truth_without_calling_the_model(
    scenario: dict[str, Any],
) -> None:
    """Runs without an API key: the allowlist is checkable without the agent."""
    request = build_investigation_request(scenario)
    rendered = request.model_dump_json()
    assert scenario["expect"]["canary"] not in rendered
    for term in ("expect", "reachability", "must_cite", "root_cause_contains"):
        assert term not in rendered


def test_seed_helper_import_resolves_without_an_api_key() -> None:
    """The seeding fixture is lazy, so a broken import here hides until a key is set.

    Without this, renaming the corpus-contract helper leaves the whole eval
    suite green locally and failing only for whoever runs it with credentials.
    """
    from tests.corpus_contract.test_reachability import (  # noqa: PLC0415
        _seed_committed_corpus,
    )

    assert callable(_seed_committed_corpus)


def test_scenario_declares_a_forbidden_root_cause(scenario: dict[str, Any]) -> None:
    """A required-subset eval is only meaningful alongside a forbidden condition."""
    expect = scenario["expect"]
    assert expect["forbidden_root_cause"], "an eval without a forbidden condition can pass by luck"
    assert set(expect["forbidden_root_cause"]) & set(expect["ruled_out_contains"])


@pytest.fixture
def seeded_session(migrated_engine: Engine) -> Generator[Session]:
    """Ingest the committed corpus, mirroring the corpus-contract fixture."""
    from tests.corpus_contract.test_reachability import (  # noqa: PLC0415
        _seed_committed_corpus,
    )

    connection = migrated_engine.connect()
    transaction = connection.begin()
    db_session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        _seed_committed_corpus(db_session)
        yield db_session
    finally:
        db_session.close()
        transaction.rollback()
        connection.close()

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from aegis.agent.slack import build_blocks, post_summary
from aegis.agent.summary import Claim, IncidentSummary, TimelineEntry
from aegis.config import Settings


def _summary(entries: int = 0) -> IncidentSummary:
    cite = "commit:" + "a" * 40
    return IncidentSummary(
        service="checkout-api",
        root_cause=Claim(statement="Timeout changed.", cites=[cite]),
        confidence="high",
        timeline=[
            TimelineEntry(
                at=datetime(2026, 8, 20, tzinfo=UTC) + timedelta(minutes=index),
                what="x" * 3000,
                cites=[cite],
            )
            for index in range(entries)
        ],
        recommended_action="Restore it.",
    )


def test_blocks_respect_slack_section_and_message_limits() -> None:
    blocks = build_blocks(_summary(40), "run-1")
    assert len(blocks) <= 45
    assert all(
        len(block["text"]["text"]) <= 3000
        for block in blocks
        if block["type"] == "section"
    )


def test_post_summary_records_non_success_without_raising() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500)))
    try:
        outcome = post_summary(
            _summary(), "run-1", Settings(slack_webhook_url="https://slack.test"), client=client
        )
    finally:
        client.close()
    assert outcome.attempted is True
    assert outcome.ok is False
    assert outcome.status_code == 500


def test_post_summary_skips_when_no_webhook_is_configured() -> None:
    settings = Settings.model_construct(slack_webhook_url=None)
    assert post_summary(_summary(), "run-1", settings).model_dump() == {
        "attempted": False,
        "ok": False,
        "status_code": None,
        "error": "no webhook configured",
    }


def test_citations_survive_truncation_of_a_very_long_claim() -> None:
    """A truncated claim must never lose its evidence.

    Composing prose and citations and then truncating the result drops the
    citations exactly when the prose is long -- publishing an assertion with
    nothing behind it. Every fixture summary is short enough to hide this.
    """
    rollup = "rollup:checkout-api/2026-08-19T14:03:00Z/5xx/error/" + "0" * 32
    long_statement = "The upstream timeout was lowered. " * 200
    summary = IncidentSummary(
        service="checkout-api",
        root_cause=Claim(statement=long_statement, cites=["commit:" + "a" * 40, rollup]),
        confidence="high",
        timeline=[
            TimelineEntry(
                at=datetime(2026, 8, 19, 14, 3, tzinfo=UTC),
                what=long_statement,
                cites=[rollup],
            )
        ],
        ruled_out=[Claim(statement=long_statement, cites=["deploy:" + "b" * 32])],
        recommended_action="Restore the timeout.",
    )

    blocks = build_blocks(summary, "run-abc")
    rendered = [
        block["text"]["text"] for block in blocks if block.get("type") == "section"
    ]

    assert len(blocks) <= 50
    for text in rendered:
        assert len(text) <= 3000
    joined = "\n".join(rendered)
    for cite in ("commit:" + "a" * 40, rollup, "deploy:" + "b" * 32):
        assert cite in joined, f"citation dropped by truncation: {cite}"


def test_oversized_evidence_drops_whole_citations_and_says_how_many() -> None:
    """A severed citation is not weaker evidence -- it resolves to nothing.

    Truncating the evidence string mid-identifier produces a token that looks
    like a citation and is not one, with nothing telling the reader that
    anything was lost.
    """
    cites = [
        f"rollup:checkout-api/2026-08-19T14:0{index % 10}:00Z/5xx/error/{index:032d}"
        for index in range(60)
    ]
    summary = IncidentSummary(
        service="checkout-api",
        root_cause=Claim(statement="The upstream timeout was lowered.", cites=cites),
        confidence="high",
        timeline=[],
        recommended_action="Restore the timeout.",
    )

    blocks = build_blocks(summary, "run-abc")
    sections = [b["text"]["text"] for b in blocks if b.get("type") == "section"]
    root = next(text for text in sections if "*Root cause*" in text)

    assert len(root) <= 3000
    # Every rendered citation is delimited, so none was cut in half.
    assert root.count("`") % 2 == 0
    for token in root.split("`")[1::2]:
        assert token in cites
    assert "more in the stored trace" in root
    assert "The upstream timeout was lowered." in root


def test_the_limits_test_subject_is_not_empty() -> None:
    """Guards the limits assertions above, which an empty message would satisfy.

    ``all(len(t) <= 3000 for t in [])`` is true, so a build_blocks that returned
    nothing would pass every bound check while publishing no incident at all.
    """
    summary = IncidentSummary(
        service="checkout-api",
        root_cause=Claim(statement="A timeout caused the spike.", cites=["commit:" + "a" * 40]),
        confidence="high",
        timeline=[],
        recommended_action="Restore the timeout.",
    )

    blocks = build_blocks(summary, "run-abc")
    sections = [b for b in blocks if b.get("type") == "section"]

    assert len(sections) >= 2
    assert any("A timeout caused the spike." in b["text"]["text"] for b in sections)
    assert any("commit:" + "a" * 40 in b["text"]["text"] for b in sections)

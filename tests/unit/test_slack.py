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

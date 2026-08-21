"""Best-effort Slack Incoming Webhook delivery."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import httpx

from aegis.agent.summary import Claim, IncidentSummary
from aegis.app.records import DeliveryOutcome
from aegis.config import Settings

_SECTION_LIMIT = 2900
_BLOCK_LIMIT = 45


def build_blocks(summary: IncidentSummary, run_id: str) -> list[dict[str, Any]]:
    """Build a bounded Block Kit representation of a validated summary."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": _truncate(summary.service, 150)},
        },
        _claim_section("*Root cause*", summary.root_cause),
        _section(
            f"*Confidence:* {summary.confidence}\n*Recommended action:* "
            f"{summary.recommended_action}"
        ),
    ]
    candidates: list[dict[str, Any]] = [
        _section(f"*{entry.at.isoformat()}*\n{entry.what}", evidence=_cites(entry.cites))
        for entry in summary.timeline
    ]
    # One section per claim rather than one joined section per group: a joined
    # section can only be shortened by truncating it, which silently removes the
    # citations of whichever claims fall past the limit. Separate sections are
    # dropped whole and counted in the footer instead.
    candidates.extend(_claim_section("*Ruled out*", claim) for claim in summary.ruled_out)
    candidates.extend(
        _claim_section("*Similar incidents*", claim) for claim in summary.similar_incidents
    )
    available = _BLOCK_LIMIT - len(blocks) - 1
    shown = candidates[:available]
    blocks.extend(shown)
    dropped = len(candidates) - len(shown)
    footer = f"run_id: {run_id}"
    if dropped:
        footer += f" · {dropped} section(s) omitted due to Slack limits"
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]})
    return blocks


def post_summary(
    summary: IncidentSummary,
    run_id: str,
    settings: Settings,
    *,
    client: httpx.Client | None = None,
) -> DeliveryOutcome:
    """Post once to Slack and turn all transport outcomes into data."""
    if settings.slack_webhook_url is None:
        return DeliveryOutcome(attempted=False, ok=False, error="no webhook configured")
    owned_client = client is None
    active_client = client or httpx.Client(timeout=10.0)
    try:
        response = active_client.post(
            settings.slack_webhook_url, json={"blocks": build_blocks(summary, run_id)}
        )
        ok = 200 <= response.status_code < 300
        return DeliveryOutcome(
            attempted=True,
            ok=ok,
            status_code=response.status_code,
            error=None if ok else response.text,
        )
    except httpx.HTTPError as exc:
        return DeliveryOutcome(attempted=True, ok=False, error=f"{type(exc).__name__}: {exc}")
    finally:
        if owned_client:
            active_client.close()


def _section(value: str, *, evidence: str = "") -> dict[str, Any]:
    """Truncate the prose and never the evidence.

    The citation line is what makes a sentence a supported claim, so composing
    prose and citations and then truncating the result publishes an unsupported
    assertion whenever the prose is long -- and every fixture summary is short
    enough to hide it. Space for the evidence is reserved first.
    """
    if not evidence:
        text = _truncate(value, _SECTION_LIMIT)
    else:
        budget = _SECTION_LIMIT - len(evidence) - 1
        if budget <= 0:
            # The citations alone exceed the limit. Publishing them without the
            # prose is recoverable; publishing prose without them is not.
            text = _truncate(evidence, _SECTION_LIMIT)
        else:
            text = f"{_truncate(value, budget)}\n{evidence}"
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _claim_section(heading: str, claim: Claim) -> dict[str, Any]:
    return _section(f"{heading}\n{claim.statement}", evidence=_cites(claim.cites))


def _cites(cites: Iterable[str]) -> str:
    return "Evidence: " + ", ".join(f"`{cite}`" for cite in cites)


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


__all__ = ["DeliveryOutcome", "build_blocks", "post_summary"]

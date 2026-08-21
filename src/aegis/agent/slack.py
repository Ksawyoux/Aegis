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
        _section(f"*Root cause*\n{_claim(summary.root_cause)}"),
        _section(
            f"*Confidence:* {summary.confidence}\n*Recommended action:* "
            f"{summary.recommended_action}"
        ),
    ]
    candidates: list[dict[str, Any]] = [
        _section(f"*{entry.at.isoformat()}*\n{entry.what}\n{_cites(entry.cites)}")
        for entry in summary.timeline
    ]
    if summary.ruled_out:
        candidates.append(
            _section("*Ruled out*\n" + "\n".join(_claim(claim) for claim in summary.ruled_out))
        )
    if summary.similar_incidents:
        candidates.append(
            _section(
                "*Similar incidents*\n"
                + "\n".join(_claim(claim) for claim in summary.similar_incidents)
            )
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


def _section(value: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": _truncate(value, _SECTION_LIMIT)}}


def _claim(claim: Claim) -> str:
    return f"{claim.statement}\n{_cites(claim.cites)}"


def _cites(cites: Iterable[str]) -> str:
    return "Evidence: " + ", ".join(f"`{cite}`" for cite in cites)


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


__all__ = ["DeliveryOutcome", "build_blocks", "post_summary"]

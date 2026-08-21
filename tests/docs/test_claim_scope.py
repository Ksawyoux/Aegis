"""Mechanical checks that the v1.0 scope claims stay stated, not silently rewritten.

Two independently falsifiable properties. First, the two canonical paragraphs
required by the Part 4 specification are present verbatim in ``README.md``.
Second, every claim-bearing paragraph anywhere in the scanned tree is pinned to
a committed, human-reviewed snapshot -- a new or changed match fails until a
person deliberately updates ``claim_scope_approved.txt``. Neither check proves
the absence of a paraphrase that avoids every listed term; that remains a
human-review problem, stated as such in the module docstring next to it.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.docs.claim_scope_scan import ROOT, claim_bearing_paragraphs, render_snapshot

README = ROOT / "README.md"
SNAPSHOT = Path(__file__).with_name("claim_scope_approved.txt")

FEASIBILITY_PARAGRAPH = (
    "Aegis Context v1.0 is a feasibility demonstration. Five planted scenarios show that one "
    "agent can correlate pre-ingested deploy, infrastructure, telemetry, and postmortem evidence "
    "through three aggregate MCP tools. They do not establish that Aegis Context is more "
    "accurate, cheaper, faster, or more reliable than a multi-agent system. No swarm baseline, "
    "matched-budget comparison, repeated-trial analysis, or held-out external corpus is included."
)

PROVENANCE_PARAGRAPH = (
    "Provenance validation proves only that each citation in a displayed claim was returned by "
    "one of the three MCP tools during that investigation. It rejects malformed, fabricated, and "
    "unseen identifiers. It does not prove that the cited row semantically supports the sentence "
    "attached to it. Semantic support is evaluated by the planted scenario contracts or by a "
    "human reviewer."
)


def _normalized_readme_paragraphs() -> set[str]:
    text = README.read_text(encoding="utf-8")
    return {" ".join(chunk.split()) for chunk in re.split(r"\n\s*\n", text) if chunk.strip()}


def test_readme_contains_the_canonical_feasibility_paragraph() -> None:
    assert FEASIBILITY_PARAGRAPH in _normalized_readme_paragraphs()


def test_readme_contains_the_canonical_provenance_paragraph() -> None:
    assert PROVENANCE_PARAGRAPH in _normalized_readme_paragraphs()


def test_claim_bearing_paragraph_snapshot_is_unchanged() -> None:
    """A new or changed claim-bearing paragraph must be reviewed, not merely pass CI.

    Regenerate the snapshot with ``uv run python -m tests.docs.claim_scope_scan``
    only after a human has read every line the diff adds.
    """
    assert SNAPSHOT.exists(), "claim_scope_approved.txt is missing; see module docstring"
    current = SNAPSHOT.read_text(encoding="utf-8")
    observed = render_snapshot()
    assert observed == current, (
        "claim-bearing language changed without a reviewed snapshot update. "
        "Review the new paragraphs, then regenerate the snapshot deliberately."
    )


def test_snapshot_is_not_vacuously_empty() -> None:
    """Guards against a scan that silently stops matching anything."""
    assert claim_bearing_paragraphs(), "the claim-scope scan found no matches at all"

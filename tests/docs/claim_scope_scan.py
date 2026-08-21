"""Shared scanning logic for the repository-wide claim-scope change detector.

This is not itself a test module: it is imported by ``test_claim_scope.py`` and
also runnable directly (``python -m tests.docs.claim_scope_scan``) to regenerate
``claim_scope_approved.txt`` after a human has reviewed every new or changed
match. The mechanical check this supports is explicitly a change detector, not
a semantic proof -- a paraphrase that avoids every listed term is a human-review
problem this scan cannot see.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]

#: Terms whose presence in a paragraph makes that paragraph claim-bearing enough
#: to require human review before it can change silently. Matched as whole words
#: only -- deliberately no stemming, so "prove" and "proven" are each listed.
TERMS: tuple[str, ...] = (
    "beat",
    "better",
    "outperform",
    "superior",
    "superiority",
    "prove",
    "proven",
    "provenance",
    "support",
    "supported",
    "guarantee",
    "accurate",
    "accuracy",
    "correct",
)

_TERM_PATTERN = re.compile(
    "|".join(rf"\b{re.escape(term)}\b" for term in TERMS),
    re.IGNORECASE,
)

#: Paths scanned for claim-bearing language, relative to the repository root.
SCANNED_GLOBS: tuple[str, ...] = (
    "README.md",
    "docs/**/*.md",
    "src/**/*.py",
    "pyproject.toml",
)


def scanned_files() -> list[Path]:
    """Return every file the claim-scope scan covers, in a stable sorted order."""
    matched: set[Path] = set()
    for pattern in SCANNED_GLOBS:
        for path in ROOT.glob(pattern):
            if path.is_file():
                matched.add(path)
    return sorted(matched, key=lambda path: path.relative_to(ROOT).as_posix())


def _normalize_paragraph(text: str) -> str:
    """Collapse a paragraph's internal whitespace to single spaces."""
    return " ".join(text.split())


def _paragraphs(path: Path) -> list[str]:
    """Split one file's text into blank-line-delimited, normalized paragraphs."""
    text = path.read_text(encoding="utf-8")
    return [
        _normalize_paragraph(chunk)
        for chunk in re.split(r"\n\s*\n", text)
        if chunk.strip()
    ]


def claim_bearing_paragraphs() -> list[str]:
    """Return every claim-bearing paragraph across the scanned tree.

    Each entry is ``"<relative-path>: <normalized paragraph>"``, sorted for a
    deterministic diff against the committed snapshot.
    """
    entries: list[str] = []
    for path in scanned_files():
        relative = path.relative_to(ROOT).as_posix()
        for paragraph in _paragraphs(path):
            if _TERM_PATTERN.search(paragraph):
                entries.append(f"{relative}: {paragraph}")
    return sorted(entries)


def render_snapshot() -> str:
    """Render the committed snapshot file's exact expected contents."""
    return "\n".join(claim_bearing_paragraphs()) + "\n"


if __name__ == "__main__":
    snapshot_path = Path(__file__).with_name("claim_scope_approved.txt")
    snapshot_path.write_text(render_snapshot(), encoding="utf-8")
    print(f"wrote {snapshot_path}")

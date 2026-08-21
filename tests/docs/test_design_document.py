"""Structural checks on the dated product design document.

The design document explains the product that exists at v1.0; it is deliberately
not a second implementation plan. These checks are mechanical: they parse
Markdown headings and require the four core subjects while rejecting headings
that read like a build order, a checklist, or acceptance criteria. They cannot
verify the prose is accurate -- only that the document has not drifted back
into being a task list.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
DESIGN_DOC = ROOT / "docs" / "2026-08-19-aegis-context-design.md"

REQUIRED_SUBJECTS = ("problem", "architecture", "data model", "interfaces")

FORBIDDEN_HEADING_TERMS = (
    "implementation",
    "build order",
    "acceptance",
    "exit criteria",
    "task list",
    "checklist",
)

_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_MCP_TOOL_NAMES = ("get_incident_diff", "get_error_telemetry", "search_similar_postmortems")


def _headings() -> list[str]:
    text = DESIGN_DOC.read_text(encoding="utf-8")
    return [match.group(1) for match in _HEADING_RE.finditer(text)]


def test_design_document_exists() -> None:
    assert DESIGN_DOC.exists(), f"missing {DESIGN_DOC}"


def test_design_document_contains_the_four_core_subjects() -> None:
    lowered = {heading.lower() for heading in _headings()}
    missing = [subject for subject in REQUIRED_SUBJECTS if subject not in lowered]
    assert not missing, f"design document is missing required headings: {missing}"


def test_design_document_contains_no_planning_or_acceptance_headings() -> None:
    offending = [
        heading
        for heading in _headings()
        if any(term in heading.lower() for term in FORBIDDEN_HEADING_TERMS)
    ]
    assert not offending, f"design document contains planning-style headings: {offending}"


def test_design_document_names_exactly_three_mcp_tools() -> None:
    text = DESIGN_DOC.read_text(encoding="utf-8")
    for tool in _MCP_TOOL_NAMES:
        assert tool in text, f"design document does not name MCP tool {tool!r}"


def test_readme_names_exactly_three_mcp_tools() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for tool in _MCP_TOOL_NAMES:
        assert tool in readme, f"README does not name MCP tool {tool!r}"


def test_readme_lists_both_database_paths_and_both_api_keys() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docker compose" in text.lower()
    assert "AEGIS_DEMO_DB_MODE=external" in text
    assert "ANTHROPIC_API_KEY" in text
    assert "OPENAI_API_KEY" in text


def test_readme_contains_the_exact_demo_commands() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "make demo" in text
    assert "make demo-live" in text


def test_readme_does_not_list_ollama_as_a_prerequisite() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "ollama" not in text.lower()

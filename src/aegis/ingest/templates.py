"""Stable log-message templates for rollup grouping."""

from __future__ import annotations

import hashlib
import re

_PLACEHOLDER_RE = re.compile(
    r"<(TS|URL|EMAIL|UUID|HEX|IP|HOSTPORT|PATH|STR|DUR|SIZE|NUM)>"
)
_WS_RE = re.compile(r"\s+")

# Branch order is intentional: ``re.sub`` selects the leftmost matching span,
# then this alternation selects the first branch at that position.
_MASK_RE = re.compile(
    r"(?P<TS>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
    r'|(?P<URL>[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s\'"<>]+)'
    r"|(?P<EMAIL>[\w.+\-]+@[\w\-]+\.[\w.\-]+)"
    r"|(?P<UUID>[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})"
    r"|(?P<HEX>0[xX][0-9a-fA-F]+\b|\b(?=[0-9a-fA-F]{8,}\b)"
    r"[0-9a-fA-F]*[a-fA-F][0-9a-fA-F]*\b)"
    r"|(?P<IP>\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"
    r"|\[[0-9a-fA-F:]*:[0-9a-fA-F:]*\]:\d+\b)"
    r"|(?P<HOSTPORT>\b[a-zA-Z0-9\-]+(?:\.[a-zA-Z0-9\-]+)+:\d+\b)"
    r"|(?P<PATH>(?:/[\w.\-]+){2,}/?)"
    # The escape alternation is a single backslash plus any character: a doubled
    # backslash here would leave escaped quotes unconsumed, ending the match early
    # and leaking string content into the template.
    r"|(?P<STR>\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')"
    r"|(?P<DUR>-?\b\d+(?:\.\d+)?(?:ms|us|µs|ns|s|m|h)\b)"
    r"|(?P<SIZE>-?\b\d+(?:\.\d+)?(?:[KMGT]i?B)\b)"
    r"|(?P<NUM>-?\b\d+(?:\.\d+)?\b)"
)


def mask(message: str) -> str:
    """Replace volatile tokens in a log message with stable placeholders."""
    escaped = _PLACEHOLDER_RE.sub(lambda match: f"<ESC{match.group(1)}>", message)
    masked = _MASK_RE.sub(lambda match: f"<{match.lastgroup}>", escaped)
    return _WS_RE.sub(" ", masked).strip()


def template_hash(message: str) -> str:
    """Return the 128-bit stable grouping hash for one log message."""
    return hashlib.sha256(mask(message).encode("utf-8")).hexdigest()[:32]

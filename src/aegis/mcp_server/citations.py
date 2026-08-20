"""Stable, parseable citations for evidence returned by MCP tools.

The public citation strings in this module are deliberately independent of
database sequence identifiers.  They are an evidence identity contract, not
display labels, so every formatter validates its components before joining
them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from aegis.ingest.timewindow import format_rollup_timestamp

_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._@-]+$")
_UID_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_CONTENT_SHA_RE = re.compile(r"^[0-9a-f]{8,}$")
_ROLLUP_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_ORDINAL_RE = re.compile(r"^(?:0|[1-9]\d*)$")

CitationKind = Literal["commit", "deploy", "infra", "log", "rollup", "postmortem"]


class MalformedCitation(ValueError):
    """Raised when a string does not conform to the public citation grammar."""


@dataclass(frozen=True, slots=True)
class Citation:
    """A parsed citation with its unmodified wire components.

    ``components`` are ordered as they appear in the grammar.  The named
    properties are convenience accessors for consumers of a particular kind.
    """

    kind: CitationKind
    components: tuple[str, ...]

    @property
    def sha(self) -> str:
        """Return the commit SHA for a commit citation."""
        return self._component("commit", 0, "sha")

    @property
    def uid(self) -> str:
        """Return the UID for a deployment, infra, or log citation."""
        if self.kind not in {"deploy", "infra", "log"}:
            raise AttributeError("only deploy, infra, and log citations have a uid")
        return self.components[0]

    @property
    def service(self) -> str:
        """Return the service name for a rollup citation."""
        return self._component("rollup", 0, "service")

    @property
    def bucket_start(self) -> str:
        """Return the canonical UTC bucket timestamp for a rollup citation."""
        return self._component("rollup", 1, "bucket_start")

    @property
    def status_class(self) -> str:
        """Return the status class for a rollup citation."""
        return self._component("rollup", 2, "status_class")

    @property
    def level(self) -> str:
        """Return the log level for a rollup citation."""
        return self._component("rollup", 3, "level")

    @property
    def template_hash(self) -> str:
        """Return the template hash for a rollup citation."""
        return self._component("rollup", 4, "template_hash")

    @property
    def slug(self) -> str:
        """Return the postmortem slug for a postmortem citation."""
        return self._component("postmortem", 0, "slug")

    @property
    def content_sha8(self) -> str:
        """Return the eight-character content hash prefix for a postmortem citation."""
        return self._component("postmortem", 1, "content_sha8")

    @property
    def ordinal(self) -> int:
        """Return the postmortem chunk ordinal."""
        return int(self._component("postmortem", 2, "ordinal"))

    def _component(self, required_kind: CitationKind, index: int, name: str) -> str:
        if self.kind != required_kind:
            raise AttributeError(f"only {required_kind} citations have {name}")
        return self.components[index]


def format_commit(sha: str) -> str:
    """Format a commit citation from its full lowercase SHA-1 hexadecimal value."""
    _require_match(sha, _SHA_RE, "commit SHA")
    return f"commit:{sha}"


def format_deploy(uid: str) -> str:
    """Format a deployment citation from its content-derived UID."""
    return _format_uid_kind("deploy", uid)


def format_infra(uid: str) -> str:
    """Format an infrastructure-change citation from its content-derived UID."""
    return _format_uid_kind("infra", uid)


def format_log(uid: str) -> str:
    """Format a log-event citation from its content-derived UID."""
    return _format_uid_kind("log", uid)


def format_rollup(
    service: str,
    bucket_start: datetime,
    status_class: str,
    level: str,
    template_hash: str,
) -> str:
    """Format a rollup citation using all five columns of its primary key."""
    _require_component(service, "service")
    _require_component(status_class, "status_class")
    _require_component(level, "level")
    _require_component(template_hash, "template_hash")
    timestamp = format_rollup_timestamp(bucket_start)
    return f"rollup:{service}/{timestamp}/{status_class}/{level}/{template_hash}"


def format_postmortem(slug: str, content_sha: str, ordinal: int) -> str:
    """Format a postmortem chunk citation from its document version and position.

    ``content_sha`` may be the full content hash (the normal database value) or
    an already shortened hash, but must contain at least eight lowercase hex
    characters.  The public grammar always uses exactly its first eight.
    """
    _require_postmortem_slug(slug)
    _require_match(content_sha, _CONTENT_SHA_RE, "content_sha")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise ValueError("ordinal must be a non-negative integer")
    return f"postmortem:{slug}@{content_sha[:8]}#{ordinal}"


def parse(cite: str) -> Citation:
    """Parse one unambiguous citation or raise :class:`MalformedCitation`."""
    if not isinstance(cite, str):
        raise MalformedCitation("citation must be a string")

    prefix, separator, body = cite.partition(":")
    if not separator:
        raise MalformedCitation("citation is missing its kind separator")

    if prefix == "commit":
        _parse_match(body, _SHA_RE, "commit SHA")
        return Citation("commit", (body,))
    if prefix in {"deploy", "infra", "log"}:
        _parse_match(body, _UID_RE, f"{prefix} uid")
        return Citation(prefix, (body,))
    if prefix == "rollup":
        return _parse_rollup(body)
    if prefix == "postmortem":
        return _parse_postmortem(body)
    raise MalformedCitation(f"unknown citation kind: {prefix!r}")


def is_wellformed(cite: str) -> bool:
    """Return whether ``cite`` conforms to the citation grammar, never raising."""
    try:
        parse(cite)
    except (MalformedCitation, TypeError, ValueError):
        return False
    return True


def _format_uid_kind(kind: Literal["deploy", "infra", "log"], uid: str) -> str:
    _require_match(uid, _UID_RE, f"{kind} uid")
    return f"{kind}:{uid}"


def _parse_rollup(body: str) -> Citation:
    parts = body.split("/")
    if len(parts) != 5:
        raise MalformedCitation("rollup citations require five slash-separated components")
    service, timestamp, status_class, level, template_hash = parts
    _parse_component(service, "service")
    _parse_rollup_timestamp(timestamp)
    _parse_component(status_class, "status_class")
    _parse_component(level, "level")
    _parse_component(template_hash, "template_hash")
    return Citation("rollup", tuple(parts))


def _parse_postmortem(body: str) -> Citation:
    slug, at, remainder = body.partition("@")
    if not at:
        raise MalformedCitation("postmortem citation is missing '@'")
    content_sha8, hash_mark, ordinal = remainder.partition("#")
    if not hash_mark:
        raise MalformedCitation("postmortem citation is missing '#'")
    _parse_postmortem_slug(slug)
    _parse_match(content_sha8, re.compile(r"^[0-9a-f]{8}$"), "content_sha8")
    _parse_match(ordinal, _ORDINAL_RE, "ordinal")
    return Citation("postmortem", (slug, content_sha8, ordinal))


def _parse_rollup_timestamp(value: str) -> None:
    _parse_match(value, _ROLLUP_TIMESTAMP_RE, "rollup timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MalformedCitation("invalid rollup timestamp") from exc
    if format_rollup_timestamp(parsed) != value:
        raise MalformedCitation("rollup timestamp is not canonical UTC")


def _require_component(value: str, name: str) -> None:
    _require_match(value, _COMPONENT_RE, name)


def _require_postmortem_slug(value: str) -> None:
    _require_component(value, "slug")
    if "@" in value:
        raise ValueError(f"invalid slug: {value!r}")


def _require_match(value: str, pattern: re.Pattern[str], name: str) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"invalid {name}: {value!r}")


def _parse_component(value: str, name: str) -> None:
    if _COMPONENT_RE.fullmatch(value) is None:
        raise MalformedCitation(f"invalid {name}: {value!r}")


def _parse_postmortem_slug(value: str) -> None:
    _parse_component(value, "slug")
    if "@" in value:
        raise MalformedCitation(f"invalid slug: {value!r}")


def _parse_match(value: str, pattern: re.Pattern[str], name: str) -> None:
    if pattern.fullmatch(value) is None:
        raise MalformedCitation(f"invalid {name}: {value!r}")

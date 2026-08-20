"""Canonical normalization for content-derived evidence identities."""

from __future__ import annotations

from datetime import UTC, datetime
from os import PathLike, fspath
from pathlib import PurePosixPath
from typing import TypeAlias
from unicodedata import normalize

_COMPONENT_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._@-"
)
PathInput: TypeAlias = str | PathLike[str]


def normalize_source_file(source_file: PathInput) -> str:
    """Return an NFC-normalized, relative path with POSIX separators.

    The caller must first make ``source_file`` relative to ``Settings.corpus_dir``.
    """
    value = normalize("NFC", fspath(source_file)).replace("\\", "/")
    path = PurePosixPath(value)
    if path.is_absolute() or not value or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("source_file must be a non-empty path relative to corpus_dir")
    return path.as_posix()


def normalize_source_offset(source_offset: int) -> int:
    """Validate a byte offset supplied by the caller, not a character index."""
    if isinstance(source_offset, bool) or not isinstance(source_offset, int) or source_offset < 0:
        raise ValueError("source_offset must be a non-negative byte offset")
    return source_offset


def normalize_raw(raw: bytes | str) -> str:
    """Decode raw UTF-8 and normalize its one trailing record terminator.

    When bytes are supplied, decoding deliberately uses ``errors='replace'``. Callers
    pass the source bytes here; offsets remain byte offsets even after decoding.
    """
    if isinstance(raw, bytes):
        value = raw.decode("utf-8", errors="replace")
    elif isinstance(raw, str):
        value = raw
    else:
        raise TypeError("raw must be bytes or str")
    if value.endswith("\r\n"):
        value = value[:-2]
    elif value.endswith("\n"):
        value = value[:-1]
    return normalize("NFC", value)


def normalize_timestamp(value: datetime) -> str:
    """Render an aware timestamp as the unique UTC UID wire spelling."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def normalize_service(value: str) -> str:
    """NFC-normalize and validate an immutable, case-sensitive service name."""
    return _normalize_component(value, label="service")


def normalize_slug(value: str) -> str:
    """NFC-normalize and validate a case-sensitive postmortem slug."""
    return _normalize_component(value, label="slug")


def normalize_environment(value: str) -> str:
    """NFC-normalize and validate a case-sensitive deployment environment."""
    return _normalize_component(value, label="environment")


def normalize_repo(value: str) -> str:
    """Canonicalize a repository identifier to lower-case ``owner/name`` form."""
    normalized = normalize("NFC", value).lower()
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    owner, separator, name = normalized.partition("/")
    if not separator or not owner or not name or "/" in name:
        raise ValueError("repo must use owner/name form")
    if not _is_component(owner) or not _is_component(name):
        raise ValueError("repo components contain unsupported characters")
    return normalized


def _normalize_component(value: str, *, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be str")
    normalized = normalize("NFC", value)
    if not _is_component(normalized):
        raise ValueError(f"{label} must match [A-Za-z0-9._@-]+")
    return normalized


def _is_component(value: str) -> bool:
    return bool(value) and all(character in _COMPONENT_CHARS for character in value)

"""Binary log ingestion and the extensible JSON-lines parser."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, TypeAlias, cast, runtime_checkable

from aegis.ingest.identity import log_uid
from aegis.ingest.normalize import ServiceRegistry, canonical_level
from aegis.ingest.templates import template_hash
from aegis.ingest.textnorm import normalize_raw, normalize_source_file
from aegis.ingest.timewindow import attach_local_time


@dataclass(frozen=True)
class ParseContext:
    """The stable file-level inputs shared by all log formats."""

    registry: ServiceRegistry
    source_file: str
    default_log_timezone: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_file", normalize_source_file(self.source_file))


@dataclass(frozen=True)
class ResolvedDraft:
    """A parsed log event whose owning service is known."""

    uid: str
    ts: datetime
    service_id: int
    level: str
    status_code: int | None
    trace_id: str | None
    message: str
    template_hash: str
    raw: str
    attrs: dict[str, Any]
    source_file: str
    source_offset: int


@dataclass(frozen=True)
class UnresolvedDraft:
    """A source line retained without inventing fields unavailable in the input."""

    uid: str
    raw: str
    reason: str
    source_file: str
    source_offset: int
    ts: datetime | None = None


Draft: TypeAlias = ResolvedDraft | UnresolvedDraft


class LogFormat(Protocol):
    """A pluggable parser for one line-oriented log format."""

    name: str

    def sniff(self, sample: Sequence[str]) -> float: ...

    def parse(self, line: str, offset: int, ctx: ParseContext) -> Draft: ...


@runtime_checkable
class _RawAwareLogFormat(Protocol):
    """Optional extension preserving source bytes for identity construction."""

    def parse_raw(self, raw: bytes, offset: int, ctx: ParseContext) -> Draft: ...


class _ResolvedLogService(Protocol):
    id: int
    log_timezone: str


class JsonLinesFormat:
    """JSON object-per-line logs; duplicate object keys use JSON's last-value rule."""

    name = "json-lines"

    def sniff(self, sample: Sequence[str]) -> float:
        if not sample:
            return 0.0
        objects = sum(_is_json_object(line) for line in sample)
        return objects / len(sample)

    def parse(self, line: str, offset: int, ctx: ParseContext) -> Draft:
        """Parse a decoded caller-provided line.

        ``iter_drafts`` calls :meth:`parse_raw` instead so a UID is always based
        on the original source bytes. This public method retains the format
        registry's specified ``parse(line, offset, ctx)`` surface.
        """
        return self._parse(line=line, raw=line, offset=offset, ctx=ctx)

    def parse_raw(self, raw: bytes, offset: int, ctx: ParseContext) -> Draft:
        """Parse source bytes while preserving them for ``log_uid``."""
        return self._parse(
            line=raw.decode("utf-8", errors="replace"), raw=raw, offset=offset, ctx=ctx
        )

    def _parse(self, *, line: str, raw: bytes | str, offset: int, ctx: ParseContext) -> Draft:
        normalized_raw = normalize_raw(raw)
        event_uid = log_uid(file=ctx.source_file, offset=offset, raw=raw)
        try:
            value = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            return _unresolved(event_uid, normalized_raw, "unparseable", offset, ctx)

        if not isinstance(value, dict) or not isinstance(value.get("msg"), str):
            return _unresolved(event_uid, normalized_raw, "unparseable", offset, ctx)

        timestamp = _parse_timestamp(value.get("ts"))
        if timestamp is None:
            return _unresolved(event_uid, normalized_raw, "no_timestamp", offset, ctx)

        resolution = ctx.registry.resolve_service(name=value.get("service"))
        if not resolution.resolved:
            assert resolution.reason is not None
            return _unresolved(
                event_uid, normalized_raw, resolution.reason, offset, ctx, ts=timestamp
            )

        service = cast(_ResolvedLogService, resolution.service)
        attrs = _attrs(value)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            attachment = attach_local_time(timestamp, service.log_timezone)
            timestamp = attachment.timestamp
            if attachment.tz_ambiguous:
                attrs["tz_ambiguous"] = True
            if attachment.tz_nonexistent:
                attrs["tz_nonexistent"] = True

        status_code = _status_code(value.get("status"), attrs)
        message = value["msg"]
        assert isinstance(message, str)
        trace_value = value.get("trace_id")
        trace_id = trace_value if isinstance(trace_value, str) else None
        level_value = value.get("level")
        level = canonical_level(level_value if isinstance(level_value, str) else None, status_code)
        return ResolvedDraft(
            uid=event_uid,
            ts=timestamp,
            service_id=service.id,
            level=level,
            status_code=status_code,
            trace_id=trace_id,
            message=message,
            template_hash=template_hash(message),
            raw=normalized_raw,
            attrs=attrs,
            source_file=ctx.source_file,
            source_offset=offset,
        )


FORMATS: tuple[LogFormat, ...] = (JsonLinesFormat(),)


def iter_raw_lines(path: Path) -> Iterator[tuple[int, bytes]]:
    """Yield each line's byte offset and original bytes, including its terminator."""
    with path.open("rb") as handle:
        offset = 0
        for raw in handle:
            yield offset, raw
            offset += len(raw)


def detect_format(path: Path, formats: Sequence[LogFormat] = FORMATS) -> LogFormat:
    """Select the best parser from the first 20 non-blank decoded source lines."""
    if not formats:
        raise ValueError("at least one log format must be registered")
    sample: list[str] = []
    for _, raw in iter_raw_lines(path):
        line = raw.decode("utf-8", errors="replace")
        if line.strip():
            sample.append(line)
        if len(sample) == 20:
            break
    return max(formats, key=lambda candidate: candidate.sniff(sample))


def iter_drafts(
    path: Path, ctx: ParseContext, formats: Sequence[LogFormat] = FORMATS
) -> Iterator[Draft]:
    """Detect a file's format once, then yield a draft for every source line."""
    log_format = detect_format(path, formats)
    if isinstance(log_format, _RawAwareLogFormat):
        for offset, raw in iter_raw_lines(path):
            yield log_format.parse_raw(raw, offset, ctx)
        return
    for offset, raw in iter_raw_lines(path):
        yield log_format.parse(raw.decode("utf-8", errors="replace"), offset, ctx)


def _is_json_object(line: str) -> bool:
    try:
        return isinstance(json.loads(line), dict)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _unresolved(
    event_uid: str,
    raw: str,
    reason: str,
    offset: int,
    ctx: ParseContext,
    *,
    ts: datetime | None = None,
) -> UnresolvedDraft:
    return UnresolvedDraft(
        uid=event_uid,
        raw=raw,
        reason=reason,
        source_file=ctx.source_file,
        source_offset=offset,
        ts=ts,
    )


def _attrs(value: dict[str, Any]) -> dict[str, Any]:
    consumed = {"ts", "level", "service", "msg", "trace_id", "status", "duration_ms"}
    attrs = {key: item for key, item in value.items() if key not in consumed}
    if "duration_ms" in value:
        duration = value["duration_ms"]
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            attrs["duration_ms"] = duration
        else:
            attrs["coercion_dropped"] = ["duration_ms"]
    return attrs


def _status_code(value: object, attrs: dict[str, Any]) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    if value is not None:
        attrs.setdefault("coercion_dropped", []).append("status")
    return None

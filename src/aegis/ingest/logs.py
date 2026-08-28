"""Binary log ingestion and the extensible JSON-lines parser."""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Iterable, Iterator, Sequence
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
    declared_service: str | None = None
    dependency_roots: tuple[str, ...] = ()

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
    """A record-oriented parser for one immutable log artifact."""

    name: str

    def sniff(self, sample: Sequence[str]) -> float: ...

    def feed(self, line: str, offset: int, ctx: ParseContext) -> Iterable[Draft]: ...

    def finish(self) -> Iterable[Draft]: ...


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

    def feed(self, line: str, offset: int, ctx: ParseContext) -> Iterable[Draft]:
        """Yield the one JSON-lines record represented by ``line``."""
        yield self.parse(line, offset, ctx)

    def finish(self) -> Iterable[Draft]:
        return ()

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


class NginxFormat:
    """nginx combined access logs with optional upstream timing fields."""

    name = "nginx"
    _pattern = re.compile(
        r'^(?P<remote>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "(?P<method>\S+) '
        r'(?P<path>\S+) [^"]+" (?P<status>\d{3}) \S+ "[^"]*" "[^"]*"'
        r'(?: rt=(?P<rt>\S+))?(?: urt="(?P<urt>[^"]*)")?\s*$'
    )

    def sniff(self, sample: Sequence[str]) -> float:
        return (
            sum(bool(self._pattern.match(line)) for line in sample) / len(sample) if sample else 0.0
        )

    def feed(self, line: str, offset: int, ctx: ParseContext) -> Iterable[Draft]:
        raw = line
        normalized = normalize_raw(raw)
        match = self._pattern.match(line.rstrip("\r\n"))
        event_uid = log_uid(file=ctx.source_file, offset=offset, raw=raw)
        if match is None:
            yield _unresolved(event_uid, normalized, "unparseable", offset, ctx)
            return
        timestamp = _parse_nginx_timestamp(match.group("ts"))
        if timestamp is None:
            yield _unresolved(event_uid, normalized, "no_timestamp", offset, ctx)
            return
        resolution = ctx.registry.resolve_service(name=ctx.declared_service)
        if not resolution.resolved:
            assert resolution.reason is not None
            yield _unresolved(event_uid, normalized, resolution.reason, offset, ctx, ts=timestamp)
            return
        service = cast(_ResolvedLogService, resolution.service)
        attrs: dict[str, Any] = {"method": match.group("method"), "path": match.group("path")}
        upstream = match.group("urt")
        if upstream is not None:
            attrs["upstream_times"] = upstream
        duration = _nginx_duration(match.group("rt"))
        if duration is not None:
            attrs["duration_ms"] = duration
        status = int(match.group("status"))
        yield ResolvedDraft(
            event_uid,
            timestamp,
            service.id,
            canonical_level(None, status),
            status,
            None,
            f"{match.group('method')} {match.group('path')}",
            template_hash(f"{match.group('method')} {match.group('path')}"),
            normalized,
            attrs,
            ctx.source_file,
            offset,
        )

    def finish(self) -> Iterable[Draft]:
        return ()


class LogfmtFormat:
    """Strict logfmt parser; duplicate keys are intentionally last-wins."""

    name = "logfmt"

    def sniff(self, sample: Sequence[str]) -> float:
        return (
            sum("=" in line and not line.lstrip().startswith("{") for line in sample) / len(sample)
            if sample
            else 0.0
        )

    def feed(self, line: str, offset: int, ctx: ParseContext) -> Iterable[Draft]:
        raw = line
        normalized = normalize_raw(raw)
        event_uid = log_uid(file=ctx.source_file, offset=offset, raw=raw)
        try:
            values = _parse_logfmt(line.rstrip("\r\n"))
        except ValueError:
            yield _unresolved(event_uid, normalized, "unparseable", offset, ctx)
            return
        message = values.get("msg")
        if not isinstance(message, str):
            yield _unresolved(event_uid, normalized, "unparseable", offset, ctx)
            return
        timestamp = _parse_timestamp(values.get("ts"))
        if timestamp is None:
            yield _unresolved(event_uid, normalized, "no_timestamp", offset, ctx)
            return
        declared = (
            values.get("service")
            if isinstance(values.get("service"), str)
            else ctx.declared_service
        )
        resolution = ctx.registry.resolve_service(name=declared)
        if not resolution.resolved:
            assert resolution.reason is not None
            yield _unresolved(event_uid, normalized, resolution.reason, offset, ctx, ts=timestamp)
            return
        service = cast(_ResolvedLogService, resolution.service)
        attrs = _attrs(values)
        status = _status_code(values.get("status"), attrs)
        level_value = values.get("level")
        level = canonical_level(level_value if isinstance(level_value, str) else None, status)
        yield ResolvedDraft(
            event_uid,
            timestamp,
            service.id,
            level,
            status,
            _string_or_none(values.get("trace_id")),
            message,
            template_hash(message),
            normalized,
            attrs,
            ctx.source_file,
            offset,
        )

    def finish(self) -> Iterable[Draft]:
        return ()


_TRACE_HEADER = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+(?P<level>[A-Z]+)\s+"
    r"\[(?P<service>[^\]]+)\]\s+(?P<logger>[\w.]+):\s(?P<message>.*)$"
)
_TRACE_FRAME = re.compile(r'^\s+File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>.+))?$')
_TRACE_EXCEPTION = re.compile(r"^(?P<exc>[A-Za-z_][\w.]*)(?:: ?(?P<detail>.*))?$")
_TRACE_CHAIN = {
    "During handling of the above exception, another exception occurred:",
    "The above exception was the direct cause of the following exception:",
}


@dataclass
class _TraceRecord:
    offset: int
    header: re.Match[str]
    raw_lines: list[str]
    state: str = "body"
    frames: list[dict[str, Any]] = None  # type: ignore[assignment]
    exc_type: str | None = None
    detail_lines: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.frames = []
        self.detail_lines = []


class PythonTracebackFormat:
    """Stateful Python logging parser that emits completed records only."""

    name = "python-traceback"

    def __init__(self) -> None:
        self._record: _TraceRecord | None = None
        self._ctx: ParseContext | None = None

    def sniff(self, sample: Sequence[str]) -> float:
        return (
            sum(bool(_TRACE_HEADER.match(line)) for line in sample) / len(sample) if sample else 0.0
        )

    def feed(self, line: str, offset: int, ctx: ParseContext) -> Iterable[Draft]:
        self._ctx = ctx
        header = _TRACE_HEADER.match(line.rstrip("\r\n"))
        if header is not None:
            if self._record is not None:
                # A new header means the previous record's traceback was truncated
                # in the source. Unlike the end-of-file case, appending cannot
                # change it, so its identity is stable and emitting is safe -- but
                # it is marked, because an absent exc_type must not be read as
                # "this event raised nothing".
                yield self._draft(self._record, ctx)
            self._record = _TraceRecord(offset, header, [line])
            return
        record = self._record
        if record is None:
            yield _unresolved(
                log_uid(file=ctx.source_file, offset=offset, raw=line),
                normalize_raw(line),
                "unparseable",
                offset,
                ctx,
            )
            return
        record.raw_lines.append(line)
        body = line.rstrip("\r\n")
        if record.state == "body" and body == "Traceback (most recent call last):":
            record.state = "traceback"
        elif record.state == "traceback":
            frame = _TRACE_FRAME.match(body)
            if frame:
                record.frames.append(
                    {
                        "file": frame.group("file"),
                        "line": int(frame.group("line")),
                        "func": frame.group("func") or "<module>",
                    }
                )
            elif not body.startswith((" ", "\t")):
                exception = _TRACE_EXCEPTION.match(body)
                if exception:
                    record.exc_type = exception.group("exc")
                    record.detail_lines = [exception.group("detail") or ""]
                    record.state = "after_exception"
        elif record.state == "after_exception":
            if body in _TRACE_CHAIN:
                record.state = "traceback"
            else:
                record.detail_lines.append(body)
        return
        yield  # pragma: no cover

    def finish(self) -> Iterable[Draft]:
        """Emit the open record only if it is complete.

        A record's identity is derived from the concatenation of all its lines,
        so emitting a traceback before its exception line has arrived assigns a
        uid that the completed record will not share. Appending the missing
        line and re-ingesting then inserts a second row beside the first,
        doubling the evidence and every rollup count derived from it.

        ``state == "traceback"`` means the traceback header was seen and the
        terminating exception line was not, which only happens when the file
        ends mid-traceback. Dropping that record is recoverable -- the bytes are
        still in the file and are re-read on the next ingest -- while a
        duplicated one is not.
        """
        if self._record is None or self._ctx is None:
            return ()
        record, self._record = self._record, None
        if record.state == "traceback":
            # Cleared before returning: FORMATS holds one stateful instance that
            # every file shares, so leaving the partial record in place would
            # carry it into the next file and emit it under that file's context.
            #
            # It is reported as unresolved rather than dropped. Persisting it as
            # an event would duplicate on re-ingest, because appending the
            # missing exception line changes the raw value the uid derives from.
            # Dropping it silently would let a truncated file ingest "cleanly"
            # forever while losing a real error, so the ingest report names it.
            raw = "".join(record.raw_lines)
            return (
                _unresolved(
                    log_uid(file=self._ctx.source_file, offset=record.offset, raw=raw),
                    normalize_raw(raw),
                    "incomplete_traceback",
                    record.offset,
                    self._ctx,
                ),
            )
        return (self._draft(record, self._ctx),)

    def _draft(self, record: _TraceRecord, ctx: ParseContext) -> Draft:
        timestamp = datetime.strptime(record.header.group("ts"), "%Y-%m-%d %H:%M:%S,%f")
        raw = "".join(record.raw_lines)
        event_uid = log_uid(file=ctx.source_file, offset=record.offset, raw=raw)
        header_service = record.header.group("service")
        if ctx.declared_service is not None and ctx.declared_service != header_service:
            return _unresolved(
                event_uid,
                normalize_raw(raw),
                "ambiguous_service",
                record.offset,
                ctx,
                ts=timestamp,
            )
        resolution = ctx.registry.resolve_service(name=header_service)
        if not resolution.resolved:
            assert resolution.reason is not None
            return _unresolved(
                event_uid, normalize_raw(raw), resolution.reason, record.offset, ctx, ts=timestamp
            )
        service = cast(_ResolvedLogService, resolution.service)
        attrs: dict[str, Any] = {"logger": record.header.group("logger")}
        if record.state == "traceback":
            # Reached only when a new header cut this record's traceback short.
            # Without the marker an absent exc_type is indistinguishable from an
            # event that never raised, and the frames collected so far would be
            # read as a complete stack.
            attrs["assembly"] = "incomplete"
        if record.exc_type is not None:
            attrs["exc_type"] = record.exc_type
            detail = "\n".join(record.detail_lines).strip()
            message = f"{record.exc_type}: {detail}" if detail else record.exc_type
        else:
            message = record.header.group("message")
        if record.frames:
            attrs["stack"] = record.frames
            candidates = [
                frame
                for frame in record.frames
                if not any(frame["file"].startswith(root) for root in ctx.dependency_roots)
            ]
            if candidates:
                attrs["top_frame"] = candidates[-1]
        return ResolvedDraft(
            event_uid,
            attach_local_time(timestamp, service.log_timezone).timestamp,
            service.id,
            canonical_level(record.header.group("level"), None),
            None,
            None,
            message,
            template_hash(message),
            normalize_raw(raw),
            attrs,
            ctx.source_file,
            record.offset,
        )


FORMATS: tuple[LogFormat, ...] = (
    JsonLinesFormat(),
    NginxFormat(),
    LogfmtFormat(),
    PythonTracebackFormat(),
)


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
    """Detect a file format once, feed source records, and drain it at EOF."""
    log_format = detect_format(path, formats)
    if isinstance(log_format, _RawAwareLogFormat):
        for offset, raw in iter_raw_lines(path):
            # Existing raw-aware formats preserve identity from the original
            # bytes.  Stateful formats receive decoded lines and build their
            # complete raw value themselves.
            if isinstance(log_format, JsonLinesFormat):
                yield log_format.parse_raw(raw, offset, ctx)
            else:
                yield from log_format.feed(raw.decode("utf-8", errors="replace"), offset, ctx)
    else:
        for offset, raw in iter_raw_lines(path):
            yield from log_format.feed(raw.decode("utf-8", errors="replace"), offset, ctx)
    yield from log_format.finish()


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


def _parse_nginx_timestamp(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%d/%b/%Y:%H:%M:%S %z")
    except ValueError:
        return None


def _nginx_duration(value: str | None) -> float | None:
    if value is None or value == "-":
        return None
    try:
        return float(value.split(",")[-1]) * 1000
    except ValueError:
        return None


def _parse_logfmt(line: str) -> dict[str, str]:
    try:
        parts = shlex.split(line, posix=True)
    except ValueError as error:
        raise ValueError("invalid logfmt") from error
    values: dict[str, str] = {}
    for item in parts:
        if "=" not in item:
            raise ValueError("invalid logfmt token")
        key, value = item.split("=", 1)
        if not key:
            raise ValueError("empty logfmt key")
        values[key] = value
    return values


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


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

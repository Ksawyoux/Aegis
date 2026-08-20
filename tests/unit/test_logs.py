from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from aegis.ingest.identity import log_uid
from aegis.ingest.logs import (
    JsonLinesFormat,
    ParseContext,
    ResolvedDraft,
    UnresolvedDraft,
    iter_drafts,
    iter_raw_lines,
)
from aegis.ingest.normalize import ServiceRegistry


@dataclass
class ServiceRecord:
    id: int
    name: str
    log_timezone: str = "UTC"
    repo: str | None = None
    log_keys: list[str] = field(default_factory=list)
    k8s_names: list[str] = field(default_factory=list)
    infra_tags: dict[str, str] = field(default_factory=dict)


def _context() -> ParseContext:
    return ParseContext(
        registry=ServiceRegistry.load([ServiceRecord(id=7, name="checkout-api")]),
        source_file="logs/checkout.jsonl",
        default_log_timezone="UTC",
    )


def test_binary_reader_preserves_exact_byte_offsets_and_uids(tmp_path: Path) -> None:
    first = b'{"ts":"2024-01-01T00:00:00Z","service":"checkout-api","msg":"caf\xc3\xa9"}\r\n'
    blank = b"\r\n"
    invalid_utf8 = b"not-json-\xff\n"
    fourth = b'{"ts":"2024-01-01T00:01:00Z","service":"checkout-api","msg":"ok"}'
    path = tmp_path / "checkout.jsonl"
    path.write_bytes(first + blank + invalid_utf8 + fourth)

    assert list(iter_raw_lines(path)) == [
        (0, first),
        (len(first), blank),
        (len(first) + len(blank), invalid_utf8),
        (len(first) + len(blank) + len(invalid_utf8), fourth),
    ]

    drafts = list(iter_drafts(path, _context()))

    assert drafts[0].uid == log_uid(file="logs/checkout.jsonl", offset=0, raw=first)
    assert drafts[1].uid == log_uid(file="logs/checkout.jsonl", offset=len(first), raw=blank)
    assert drafts[2].uid == log_uid(
        file="logs/checkout.jsonl", offset=len(first) + len(blank), raw=invalid_utf8
    )
    assert drafts[3].uid == log_uid(
        file="logs/checkout.jsonl",
        offset=len(first) + len(blank) + len(invalid_utf8),
        raw=fourth,
    )
    assert drafts[1].source_offset == len(first)


def test_unresolved_drafts_do_not_fabricate_resolved_fields() -> None:
    parser = JsonLinesFormat()
    context = _context()

    unparseable = parser.parse("not json", 0, context)
    no_timestamp = parser.parse('{"service":"checkout-api","msg":"missing"}', 9, context)
    no_service = parser.parse(
        '{"ts":"2024-01-01T00:00:00Z","service":"unknown","msg":"missing"}', 18, context
    )

    assert isinstance(unparseable, UnresolvedDraft)
    assert unparseable.reason == "unparseable"
    assert unparseable.ts is None
    assert isinstance(no_timestamp, UnresolvedDraft)
    assert no_timestamp.reason == "no_timestamp"
    assert no_timestamp.ts is None
    assert isinstance(no_service, UnresolvedDraft)
    assert no_service.reason == "no_service_match"
    assert no_service.ts == datetime(2024, 1, 1, tzinfo=UTC)
    for draft in (unparseable, no_timestamp, no_service):
        assert not hasattr(draft, "level")
        assert not hasattr(draft, "message")
        assert not hasattr(draft, "template_hash")


def test_no_timestamp_precedes_unknown_service() -> None:
    draft = JsonLinesFormat().parse('{"service":"unknown","msg":"missing"}', 0, _context())

    assert isinstance(draft, UnresolvedDraft)
    assert draft.reason == "no_timestamp"


def test_status_coercion_accepts_int_and_digit_string() -> None:
    parser = JsonLinesFormat()

    integer = parser.parse(
        '{"ts":"2024-01-01T00:00:00Z","service":"checkout-api","msg":"ok","status":503}',
        0,
        _context(),
    )
    digits = parser.parse(
        '{"ts":"2024-01-01T00:00:00Z","service":"checkout-api","msg":"ok","status":"404"}',
        0,
        _context(),
    )

    assert isinstance(integer, ResolvedDraft)
    assert integer.status_code == 503
    assert isinstance(digits, ResolvedDraft)
    assert digits.status_code == 404


def test_invalid_status_is_dropped_into_attrs() -> None:
    draft = JsonLinesFormat().parse(
        '{"ts":"2024-01-01T00:00:00Z","service":"checkout-api","msg":"ok","status":"5xx"}',
        0,
        _context(),
    )

    assert isinstance(draft, ResolvedDraft)
    assert draft.status_code is None
    assert draft.attrs["coercion_dropped"] == ["status"]


def test_duration_coercion_retains_numbers_and_drops_other_values() -> None:
    parser = JsonLinesFormat()
    accepted_int = parser.parse(
        '{"ts":"2024-01-01T00:00:00Z","service":"checkout-api","msg":"ok","duration_ms":2}',
        0,
        _context(),
    )
    accepted = parser.parse(
        '{"ts":"2024-01-01T00:00:00Z","service":"checkout-api","msg":"ok","duration_ms":1.5}',
        0,
        _context(),
    )
    dropped = parser.parse(
        '{"ts":"2024-01-01T00:00:00Z","service":"checkout-api","msg":"ok","duration_ms":"slow"}',
        0,
        _context(),
    )

    assert isinstance(accepted_int, ResolvedDraft)
    assert accepted_int.attrs["duration_ms"] == 2
    assert isinstance(accepted, ResolvedDraft)
    assert accepted.attrs["duration_ms"] == 1.5
    assert isinstance(dropped, ResolvedDraft)
    assert dropped.attrs["coercion_dropped"] == ["duration_ms"]
    assert "duration_ms" not in dropped.attrs


def test_non_string_message_is_unparseable_and_duplicate_key_uses_last_value() -> None:
    parser = JsonLinesFormat()
    invalid = parser.parse(
        '{"ts":"2024-01-01T00:00:00Z","service":"checkout-api","msg":3}', 0, _context()
    )
    duplicate = parser.parse(
        '{"ts":"2024-01-01T00:00:00Z","service":"checkout-api","msg":"first","msg":"last"}',
        0,
        _context(),
    )

    assert isinstance(invalid, UnresolvedDraft)
    assert invalid.reason == "unparseable"
    assert isinstance(duplicate, ResolvedDraft)
    assert duplicate.message == "last"

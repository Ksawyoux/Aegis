# ruff: noqa: E501
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from aegis.ingest.logs import (
    LogfmtFormat,
    NginxFormat,
    ParseContext,
    PythonTracebackFormat,
    ResolvedDraft,
    iter_drafts,
)
from aegis.ingest.normalize import ServiceRegistry


@dataclass
class _Service:
    id: int
    name: str
    log_timezone: str = "UTC"
    repo: str | None = None
    log_keys: list[str] = field(default_factory=list)
    k8s_names: list[str] = field(default_factory=list)
    infra_tags: dict[str, str] = field(default_factory=dict)


def _context(**kwargs: object) -> ParseContext:
    return ParseContext(
        registry=ServiceRegistry.load([_Service(id=7, name="payments-api")]),
        source_file="logs/example.log",
        default_log_timezone="UTC",
        **kwargs,
    )


def test_nginx_uses_declared_service_and_derives_error_level() -> None:
    line = (
        '10.0.3.14 - - [19/Aug/2026:14:03:22 +0000] "POST /api/v1/checkout HTTP/1.1" '
        '504 167 "-" "okhttp/4.12.0" rt=30.001 urt="10.001, 30.001"\n'
    )
    draft = next(iter(NginxFormat().feed(line, 0, _context(declared_service="payments-api"))))

    assert isinstance(draft, ResolvedDraft)
    assert draft.service_id == 7
    assert draft.level == "error"
    assert draft.status_code == 504
    assert draft.attrs["duration_ms"] == 30001.0
    assert draft.attrs["upstream_times"] == "10.001, 30.001"


def test_logfmt_preserves_quoted_spaces_and_equals() -> None:
    line = 'ts=2026-08-19T14:03:22.481Z level=error service=payments-api msg="pool exhausted x=y" detail="a b=c"\n'
    draft = next(iter(LogfmtFormat().feed(line, 0, _context())))

    assert isinstance(draft, ResolvedDraft)
    assert draft.message == "pool exhausted x=y"
    assert draft.attrs["detail"] == "a b=c"


def test_traceback_assembly_handles_chain_details_and_last_app_frame(tmp_path: Path) -> None:
    path = tmp_path / "trace.log"
    path.write_text(
        "2026-08-19 14:03:22,481 ERROR [payments-api] worker.main: failed request\n"
        "Traceback (most recent call last):\n"
        '  File "/srv/app/outer.py", line 3, in outer\n'
        '  File "/site-packages/lib.py", line 9, in lib\n'
        '  File "/srv/app/inner.py", line 11, in inner\n'
        "SyntaxError: invalid syntax\n"
        "  ^\n"
        "more detail: still part of exception\n"
        "During handling of the above exception, another exception occurred:\n"
        "Traceback (most recent call last):\n"
        '  File "/srv/app/chained.py", line 20\n'
        "KeyboardInterrupt\n",
        encoding="utf-8",
    )

    first = list(
        iter_drafts(
            path,
            _context(dependency_roots=("/site-packages",)),
            formats=(PythonTracebackFormat(),),
        )
    )
    second = list(
        iter_drafts(
            path,
            _context(dependency_roots=("/site-packages",)),
            formats=(PythonTracebackFormat(),),
        )
    )

    assert len(first) == len(second) == 1
    draft = first[0]
    assert isinstance(draft, ResolvedDraft)
    assert draft.uid == second[0].uid
    assert "^" in draft.raw
    assert draft.attrs["exc_type"] == "KeyboardInterrupt"
    assert draft.attrs["top_frame"] == {"file": "/srv/app/chained.py", "line": 20, "func": "<module>"}


def test_colon_bearing_continuation_is_not_an_exception() -> None:
    parser = PythonTracebackFormat()
    ctx = _context()
    list(parser.feed("2026-08-19 14:03:22,481 INFO [payments-api] worker.main: ordinary\n", 0, ctx))
    list(parser.feed("note: this remains a normal continuation\n", 70, ctx))
    drafts = list(parser.finish())

    assert len(drafts) == 1
    assert isinstance(drafts[0], ResolvedDraft)
    assert "exc_type" not in drafts[0].attrs

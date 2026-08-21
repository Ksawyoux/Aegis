"""An unterminated traceback must not be persisted at end of file.

The record's identity is derived from the concatenation of all its lines, so a
traceback emitted before its exception line arrives has a different uid from the
same traceback once completed. Appending the missing line and re-ingesting then
inserts a second row beside the first, silently doubling the evidence and every
rollup count derived from it.
"""

from __future__ import annotations

from pathlib import Path

from aegis.db.models import Service
from aegis.ingest.logs import ParseContext, iter_drafts
from aegis.ingest.normalize import ServiceRegistry

_HEADER = "2026-08-20 03:10:00,000 ERROR [search-api] worker: "
_COMPLETE = (
    f"{_HEADER}request failed\n"
    "Traceback (most recent call last):\n"
    '  File "/app/search/handler.py", line 42, in handle\n'
    "    return self._query(payload)\n"
    "ValueError: index shard unavailable\n"
)
_PARTIAL = _COMPLETE[: _COMPLETE.index("ValueError")]


def _drafts(path: Path) -> list[object]:
    # SQLAlchemy column defaults apply at flush, not construction, so an
    # unflushed Service has None where the registry expects sequences.
    service = Service(
        name="search-api", log_keys=[], k8s_names=[], infra_tags={}, log_timezone="UTC"
    )
    service.id = 1
    ctx = ParseContext(
        registry=ServiceRegistry.load([service]),
        source_file=path.name,
        default_log_timezone="UTC",
        declared_service="search-api",
    )
    return list(iter_drafts(path, ctx))


def test_a_file_ending_mid_traceback_emits_no_record(tmp_path: Path) -> None:
    path = tmp_path / "search-api.log"
    path.write_text(_PARTIAL, encoding="utf-8")

    assert _drafts(path) == []


def test_the_completed_traceback_is_emitted_once_after_the_append(tmp_path: Path) -> None:
    path = tmp_path / "search-api.log"
    path.write_text(_PARTIAL, encoding="utf-8")
    _drafts(path)

    path.write_text(_COMPLETE, encoding="utf-8")
    drafts = _drafts(path)

    assert len(drafts) == 1
    uids = {getattr(draft, "uid", None) for draft in drafts}
    assert len(uids) == 1


def test_a_terminated_traceback_is_still_emitted_at_end_of_file(tmp_path: Path) -> None:
    """The fix must not suppress complete records that simply end the file."""
    path = tmp_path / "search-api.log"
    path.write_text(_COMPLETE, encoding="utf-8")

    assert len(_drafts(path)) == 1

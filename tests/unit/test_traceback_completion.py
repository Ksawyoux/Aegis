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


def test_a_file_ending_mid_traceback_reports_unresolved_rather_than_an_event(
    tmp_path: Path,
) -> None:
    """It must be neither persisted as an event nor silently discarded.

    Persisting duplicates on re-ingest, because appending the exception line
    changes the raw value the uid derives from. Discarding lets a truncated file
    ingest "cleanly" forever while losing a real error, so it surfaces in the
    unresolved report instead.
    """
    path = tmp_path / "search-api.log"
    path.write_text(_PARTIAL, encoding="utf-8")

    drafts = _drafts(path)

    assert len(drafts) == 1
    assert type(drafts[0]).__name__ == "UnresolvedDraft"
    assert getattr(drafts[0], "reason", None) == "incomplete_traceback"


def test_the_completed_traceback_is_emitted_once_after_the_append(tmp_path: Path) -> None:
    """The union of both passes must contain exactly one event uid.

    Discarding the first pass would hide the defect entirely: the pre-fix code
    emitted a partial event there, and only comparing the two passes together
    shows that the completed record is a second, differently identified row.
    """
    path = tmp_path / "search-api.log"
    path.write_text(_PARTIAL, encoding="utf-8")
    first = _drafts(path)

    path.write_text(_COMPLETE, encoding="utf-8")
    second = _drafts(path)

    event_uids = {
        getattr(draft, "uid", None)
        for draft in (*first, *second)
        if type(draft).__name__ != "UnresolvedDraft"
    }
    assert len(event_uids) == 1, (
        "the partial and completed records were persisted under different uids, "
        "which doubles the evidence and every rollup count derived from it"
    )
    assert len(second) == 1


def test_a_terminated_traceback_is_still_emitted_at_end_of_file(tmp_path: Path) -> None:
    """The fix must not suppress complete records that simply end the file."""
    path = tmp_path / "search-api.log"
    path.write_text(_COMPLETE, encoding="utf-8")

    assert len(_drafts(path)) == 1


def test_a_new_header_emits_the_truncated_record_but_marks_it(tmp_path: Path) -> None:
    """A following header makes the traceback final, so the record is stable.

    Unlike the end-of-file case, appending cannot change this record, so it is
    emitted rather than lost. It is marked because an absent exc_type would
    otherwise be indistinguishable from an event that never raised, and the
    frames gathered so far would read as a complete stack.
    """
    second_header = "2026-08-20 03:11:00,000 ERROR [search-api] worker: second event\n"
    path = tmp_path / "search-api.log"
    path.write_text(_PARTIAL + second_header, encoding="utf-8")

    drafts = _drafts(path)

    assert len(drafts) == 2
    assert drafts[0].attrs["assembly"] == "incomplete"  # type: ignore[attr-defined]
    assert "exc_type" not in drafts[0].attrs  # type: ignore[attr-defined]
    assert "assembly" not in drafts[1].attrs  # type: ignore[attr-defined]

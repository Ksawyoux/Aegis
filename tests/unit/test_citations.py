from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import pytest

from aegis.mcp_server.citations import (
    Citation,
    MalformedCitation,
    format_commit,
    format_deploy,
    format_infra,
    format_log,
    format_postmortem,
    format_rollup,
    is_wellformed,
    parse,
)

UID = "a" * 32
SHA = "b" * 40
CONTENT_SHA = "c" * 64
BUCKET = datetime(2026, 8, 19, 14, 3, 22, 481000, tzinfo=UTC)


def test_full_sha_required() -> None:
    with pytest.raises(ValueError, match="commit SHA"):
        format_commit("b" * 39)

    assert format_commit(SHA) == f"commit:{SHA}"


def test_seven_char_prefix_collision_rejected() -> None:
    with pytest.raises(ValueError, match="commit SHA"):
        format_commit("abcdef0")


def test_rollup_cite_differs_on_level_only() -> None:
    error = format_rollup("api", BUCKET, "5xx", "error", "d" * 32)
    fatal = format_rollup("api", BUCKET, "5xx", "fatal", "d" * 32)

    assert error != fatal


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError, match="naive"):
        format_rollup("api", datetime(2026, 8, 19, 14, 3), "5xx", "error", "d" * 32)


def test_non_utc_aware_datetime_normalized() -> None:
    plus_two = datetime(2026, 8, 19, 16, 3, 22, tzinfo=timezone(timedelta(hours=2)))
    utc = datetime(2026, 8, 19, 14, 3, 22, tzinfo=UTC)

    assert format_rollup("api", plus_two, "5xx", "error", "d" * 32) == format_rollup(
        "api", utc, "5xx", "error", "d" * 32
    )


@pytest.mark.parametrize(
    ("formatter", "args"),
    [
        (format_log, ("a" * 31 + ":",)),
        (format_rollup, ("api/service", BUCKET, "5xx", "error", "d" * 32)),
        (format_postmortem, ("incident@one", CONTENT_SHA, 0)),
    ],
)
def test_delimiter_in_component_rejected(
    formatter: Callable[..., str], args: tuple[object, ...]
) -> None:
    with pytest.raises(ValueError):
        formatter(*args)


def test_postmortem_cite_changes_with_content_sha() -> None:
    first = format_postmortem("checkout-outage", "a" * 64, 3)
    changed = format_postmortem("checkout-outage", "b" * 64, 3)

    assert first != changed


@pytest.mark.parametrize(
    ("cite", "expected"),
    [
        (format_commit(SHA), Citation("commit", (SHA,))),
        (format_deploy(UID), Citation("deploy", (UID,))),
        (format_infra(UID), Citation("infra", (UID,))),
        (format_log(UID), Citation("log", (UID,))),
        (
            format_rollup("api", BUCKET, "5xx", "error", "d" * 32),
            Citation("rollup", ("api", "2026-08-19T14:03:22Z", "5xx", "error", "d" * 32)),
        ),
        (
            format_postmortem("checkout-outage", CONTENT_SHA, 3),
            Citation("postmortem", ("checkout-outage", "c" * 8, "3")),
        ),
    ],
)
def test_parse_round_trips_every_citation_kind(cite: str, expected: Citation) -> None:
    assert parse(cite) == expected


def test_parse_rejects_malformed_citations_and_predicate_never_raises() -> None:
    with pytest.raises(MalformedCitation):
        parse("rollup:api/2026-08-19T14:03:00Z/5xx/error/hash/extra")

    assert not is_wellformed("postmortem:incident@deadbeef#01")
    assert not is_wellformed(None)  # type: ignore[arg-type]

from datetime import UTC, datetime, timedelta

import pytest

from aegis.ingest.timewindow import (
    attach_local_time,
    baseline_window,
    format_rollup_timestamp,
    format_uid_timestamp,
    resolve_window,
)


def test_window_end_on_boundary_not_widened() -> None:
    window = resolve_window(
        datetime(2026, 8, 19, 14, 3, 22, tzinfo=UTC),
        datetime(2026, 8, 19, 14, 5, tzinfo=UTC),
    )

    assert window.start == datetime(2026, 8, 19, 14, 3, tzinfo=UTC)
    assert window.end == datetime(2026, 8, 19, 14, 5, tzinfo=UTC)
    assert window.snapped


def test_half_open_end_excluded() -> None:
    window = resolve_window(
        datetime(2026, 8, 19, 14, 3, tzinfo=UTC),
        datetime(2026, 8, 19, 14, 5, tzinfo=UTC),
    )

    assert window.contains(datetime(2026, 8, 19, 14, 4, 59, 999999, tzinfo=UTC))
    assert not window.contains(datetime(2026, 8, 19, 14, 5, tzinfo=UTC))


def test_baseline_adjacent_and_disjoint() -> None:
    window = resolve_window(
        datetime(2026, 8, 19, 14, 3, 22, tzinfo=UTC),
        datetime(2026, 8, 19, 14, 5, 1, tzinfo=UTC),
    )
    baseline = baseline_window(window)

    assert baseline.end == window.start
    assert baseline.end - baseline.start == window.end - window.start
    assert baseline.end <= window.start


def test_dst_ambiguous_local_time_marked() -> None:
    attached = attach_local_time(datetime(2024, 11, 3, 1, 30), "America/New_York")

    assert attached.tz_ambiguous
    assert not attached.tz_nonexistent
    assert attached.timestamp.fold == 0
    assert attached.utc == datetime(2024, 11, 3, 5, 30, tzinfo=UTC)


def test_dst_nonexistent_local_time_marked() -> None:
    attached = attach_local_time(datetime(2024, 3, 10, 2, 30), "America/New_York")

    assert not attached.tz_ambiguous
    assert attached.tz_nonexistent
    assert attached.timestamp == datetime(2024, 3, 10, 3, 30, tzinfo=attached.timestamp.tzinfo)
    assert attached.utc == datetime(2024, 3, 10, 7, 30, tzinfo=UTC)


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError, match="naive"):
        resolve_window(datetime(2026, 8, 19, 14, 3), datetime(2026, 8, 19, 14, 4, tzinfo=UTC))


def test_sub_minute_start_floors_correctly() -> None:
    window = resolve_window(
        datetime(2026, 8, 19, 14, 3, 59, 999999, tzinfo=UTC),
        datetime(2026, 8, 19, 14, 5, tzinfo=UTC),
    )

    assert window.start == datetime(2026, 8, 19, 14, 3, tzinfo=UTC)


def test_sub_minute_window_snaps_to_whole_minute() -> None:
    window = resolve_window(
        datetime(2026, 8, 19, 14, 3, 10, tzinfo=UTC),
        datetime(2026, 8, 19, 14, 3, 50, tzinfo=UTC),
    )

    assert window.start == datetime(2026, 8, 19, 14, 3, tzinfo=UTC)
    assert window.end == datetime(2026, 8, 19, 14, 4, tzinfo=UTC)


def test_wire_formatters_round_trip() -> None:
    value = datetime(2026, 8, 19, 16, 3, 22, 481000, tzinfo=UTC) + timedelta(hours=2)

    uid_wire = format_uid_timestamp(value)
    rollup_wire = format_rollup_timestamp(value)

    assert datetime.fromisoformat(uid_wire.replace("Z", "+00:00")) == value
    assert datetime.fromisoformat(rollup_wire.replace("Z", "+00:00")) == value.replace(
        microsecond=0
    )

"""Canonical timestamp, window, and local-time handling for ingestion.

Windows are always half-open.  The small amount of explicit DST handling here is
intentional: ``zoneinfo`` otherwise permits both ambiguous and nonexistent wall
times without telling a parser which one it received.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ResolvedWindow:
    """A UTC, minute-aligned, half-open time range."""

    start: datetime
    end: datetime
    snapped: bool

    def contains(self, value: datetime) -> bool:
        """Return whether an aware timestamp is in this half-open window."""
        timestamp = _as_utc(value)
        return self.start <= timestamp < self.end


@dataclass(frozen=True)
class LocalTimeAttachment:
    """An IANA-zone wall time and the DST facts discovered while attaching it."""

    timestamp: datetime
    tz_ambiguous: bool = False
    tz_nonexistent: bool = False

    @property
    def utc(self) -> datetime:
        """The same instant in UTC, ready for a UTC storage boundary."""
        return self.timestamp.astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    """Reject naive datetimes and give every instant a canonical UTC representation."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("naive datetimes are not permitted")
    return value.astimezone(UTC)


def resolve_window(start: datetime, end: datetime) -> ResolvedWindow:
    """Return the minute-snapped half-open window containing ``[start, end)``.

    In particular, an end that is already on a minute boundary is not changed:
    that boundary already excludes its minute under half-open semantics.
    """
    utc_start = _as_utc(start)
    utc_end = _as_utc(end)
    if utc_end <= utc_start:
        raise ValueError("window end must be after window start")

    snapped_start = utc_start.replace(second=0, microsecond=0)
    if utc_end.second == 0 and utc_end.microsecond == 0:
        snapped_end = utc_end
    else:
        snapped_end = utc_end.replace(second=0, microsecond=0) + timedelta(minutes=1)

    return ResolvedWindow(
        start=snapped_start,
        end=snapped_end,
        snapped=snapped_start != utc_start or snapped_end != utc_end,
    )


def baseline_window(window: ResolvedWindow) -> ResolvedWindow:
    """Return the equal-length window immediately preceding ``window``.

    ``window`` is already snapped, so this calculation preserves exact adjacency
    without re-snapping and cannot introduce an overlap.
    """
    start = _as_utc(window.start)
    end = _as_utc(window.end)
    if end <= start:
        raise ValueError("window end must be after window start")
    return ResolvedWindow(start=start - (end - start), end=start, snapped=False)


def _round_trips(local_time: datetime, zone: ZoneInfo, fold: int) -> bool:
    candidate = local_time.replace(tzinfo=zone, fold=fold)
    round_trip = candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    return round_trip == local_time


def attach_local_time(local_time: datetime, zone: str | ZoneInfo) -> LocalTimeAttachment:
    """Attach an IANA zone to a parser's naive wall clock timestamp.

    Ambiguous fall-back times choose the first occurrence (``fold=0``).  For a
    nonexistent spring-forward time, the wall clock is advanced by the transition
    gap.  The resulting timestamp retains its ``ZoneInfo`` zone; use ``.utc`` at
    a UTC-only storage boundary.
    """
    if local_time.tzinfo is not None or local_time.utcoffset() is not None:
        raise ValueError("local-time attachment requires a naive datetime")
    local_zone = ZoneInfo(zone) if isinstance(zone, str) else zone

    first = local_time.replace(tzinfo=local_zone, fold=0)
    second = local_time.replace(tzinfo=local_zone, fold=1)
    first_offset = first.utcoffset()
    second_offset = second.utcoffset()
    if first_offset is None or second_offset is None:
        raise ValueError("IANA zone did not provide a UTC offset")
    first_valid = _round_trips(local_time, local_zone, fold=0)
    second_valid = _round_trips(local_time, local_zone, fold=1)

    if first_valid or second_valid:
        ambiguous = (
            first_valid
            and second_valid
            and first_offset != second_offset
        )
        # fold=0 is the specified choice for both ordinary and ambiguous times.
        return LocalTimeAttachment(timestamp=first, tz_ambiguous=ambiguous)

    # In a forward transition ``fold=0`` has the pre-transition offset and
    # ``fold=1`` has the post-transition offset.  Their difference is the gap.
    gap = second_offset - first_offset
    if gap <= timedelta(0):  # Defensive: ZoneInfo's normal gap has a positive delta.
        raise ValueError("could not resolve nonexistent local time")
    shifted = local_time + gap
    return LocalTimeAttachment(
        timestamp=shifted.replace(tzinfo=local_zone, fold=0),
        tz_nonexistent=True,
    )


def format_uid_timestamp(value: datetime) -> str:
    """Render an instant for content-derived identifiers: UTC, ``Z``, microseconds."""
    return _as_utc(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def format_rollup_timestamp(value: datetime) -> str:
    """Render an instant for rollup citations: UTC, ``Z``, whole seconds."""
    return _as_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")

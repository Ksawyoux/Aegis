from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session

from aegis.aggregate.rollups import capture_dirty_set, recompute
from aegis.db.models import Commit, Deployment, ErrorRollup, InfraChange, LogEvent, Service
from aegis.mcp_server.queries import QueryError, get_error_telemetry, get_incident_diff


@pytest.fixture
def session(migrated_engine: Engine) -> Generator[Session]:
    connection = migrated_engine.connect()
    transaction = connection.begin()
    db_session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield db_session
    finally:
        db_session.close()
        transaction.rollback()
        connection.close()


NOW = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)


def test_incident_diff_is_half_open_and_carries_deployed_commit(session: Session) -> None:
    service = _service(session, "checkout-api")
    old = _commit(session, service.id, "a" * 40, NOW - timedelta(days=2))
    recent = _commit(session, service.id, "b" * 40, NOW + timedelta(minutes=1))
    _deployment(session, service.id, old.sha, "1" * 32, NOW + timedelta(seconds=30))
    _deployment(session, service.id, recent.sha, "2" * 32, NOW + timedelta(minutes=2))
    _infra(session, "3" * 32, NOW + timedelta(minutes=1), None)
    _infra(session, "4" * 32, NOW + timedelta(minutes=1), service.id)
    session.flush()

    response = get_incident_diff(
        session,
        service="checkout-api",
        window_start=NOW,
        window_end=NOW + timedelta(minutes=2),
        lookback_minutes=0,
        include_other_services=False,
    )

    assert response.window.start == NOW
    assert response.window.end == NOW + timedelta(minutes=2)
    assert response.other_services == []
    assert [deployment.commit.sha for deployment in response.focus.deployments] == [old.sha]
    assert response.focus.deployments[0].commit.message == "commit a"
    # `recent` is committed inside the half-open window and its only deployment
    # sits at exactly window_end, so that deployment is excluded and the commit
    # is genuinely standalone rather than already carried by a DeploymentRef.
    assert [commit.sha for commit in response.focus.commits] == [recent.sha]
    assert response.counts.model_dump() == {"commits": 1, "deployments": 1, "infra_changes": 1}
    assert [change.cite for change in response.unattributed] == [f"infra:{'3' * 32}"]


def test_error_telemetry_has_union_identities_and_deterministic_exemplars(session: Session) -> None:
    service = _service(session, "checkout-api")
    # Baseline-only identity spanning two minutes, so two rollup rows contribute
    # one exemplar candidate each.  The window-level tiebreak only ever applies
    # between minutes: within a minute, rollups.py has already collapsed the
    # choice using its own id-based tiebreak and Part 1 cannot reach inside.
    # Both candidates are equally rich -- the JSON nulls must not inflate the
    # first -- so the stable smallest uid must win over the smaller physical id.
    _event(
        session,
        service.id,
        "f" * 32,
        NOW - timedelta(minutes=1, seconds=-2),
        "baseline late uid",
        template="a" * 32,
        attrs={"stack": None, "upstream": None, "duration_ms": None, "exc_type": None},
    )
    _event(
        session,
        service.id,
        "0" * 32,
        NOW - timedelta(minutes=2, seconds=-1),
        "baseline selected uid",
        template="a" * 32,
    )
    # One current identity across two rollups: the richer event in the later
    # minute must win, rather than whichever minute happens to join first.
    _event(
        session,
        service.id,
        "c" * 32,
        NOW + timedelta(seconds=2),
        "less rich",
        template="b" * 32,
        status=504,
        trace_id="duplicate",
    )
    _event(
        session,
        service.id,
        "d" * 32,
        NOW + timedelta(minutes=1, seconds=2),
        "richer",
        template="b" * 32,
        status=504,
        attrs={"upstream": "payments", "duration_ms": 4},
        trace_id="later",
    )
    _event(
        session,
        service.id,
        "e" * 32,
        NOW + timedelta(seconds=3),
        "success",
        template="b" * 32,
        status=200,
        level="info",
        trace_id="duplicate",
    )
    session.flush()
    _recompute_all(session, service.id)

    response = get_error_telemetry(
        session,
        service="checkout-api",
        window_start=NOW,
        window_end=NOW + timedelta(minutes=2),
        top_n=10,
        baseline_sparse_threshold=3,
    )

    baseline_only = next(item for item in response.top_templates if item.template_hash == "a" * 32)
    current = next(
        item
        for item in response.top_templates
        if item.template_hash == "b" * 32 and item.status_class == "5xx"
    )
    assert (baseline_only.count, baseline_only.baseline_count, baseline_only.delta) == (0, 2, -2)
    assert baseline_only.source_cites == []
    assert baseline_only.baseline_cites
    assert baseline_only.exemplar.cite == f"log:{'0' * 32}"
    assert current.exemplar.cite == f"log:{'d' * 32}"
    assert response.baseline_sparse is True
    assert [(entry.status_class, entry.count) for entry in response.status_breakdown] == [
        ("2xx", 1),
        ("5xx", 2),
    ]
    assert response.sample_trace_ids == ["duplicate", "later"]
    template_keys = [
        (item.template_hash, item.status_class, item.level, item.count, item.delta)
        for item in response.top_templates
    ]
    assert template_keys == [
        ("b" * 32, "5xx", "error", 2, 2),
        ("a" * 32, "5xx", "error", 0, -2),
        ("b" * 32, "2xx", "info", 1, 1),
    ]


def test_query_argument_errors(session: Session) -> None:
    _service(session, "checkout-api")
    with pytest.raises(QueryError):
        get_incident_diff(
            session,
            service="missing",
            window_start=NOW,
            window_end=NOW + timedelta(minutes=1),
        )
    with pytest.raises(QueryError):
        get_incident_diff(
            session,
            service="checkout-api",
            window_start=NOW,
            window_end=NOW + timedelta(minutes=1),
            lookback_minutes=-1,
        )
    with pytest.raises(QueryError):
        get_error_telemetry(
            session,
            service="checkout-api",
            window_start=NOW,
            window_end=NOW + timedelta(minutes=1),
            top_n=0,
            baseline_sparse_threshold=1,
        )


def test_telemetry_is_identical_after_shuffled_physical_insertion(session: Session) -> None:
    first = _seed_deterministic_telemetry(session, reverse=False)
    session.execute(delete(ErrorRollup))
    session.execute(delete(LogEvent))
    session.execute(delete(Service))
    session.flush()
    second = _seed_deterministic_telemetry(session, reverse=True)

    assert first == second


def _seed_deterministic_telemetry(session: Session, *, reverse: bool) -> dict[str, object]:
    service = _service(session, "checkout-api")
    events = [
        ("a" * 32, NOW - timedelta(minutes=1, seconds=-1), "baseline", "a" * 32, {}, None),
        (
            "c" * 32,
            NOW + timedelta(seconds=3),
            "current plain",
            "b" * 32,
            {},
            "trace-a",
        ),
        (
            "b" * 32,
            NOW + timedelta(minutes=1, seconds=2),
            "current rich",
            "b" * 32,
            {"upstream": "payments"},
            "trace-b",
        ),
    ]
    insertion_order = list(reversed(events)) if reverse else events
    for uid, timestamp, message, template, attrs, trace_id in insertion_order:
        _event(
            session,
            service.id,
            uid,
            timestamp,
            message,
            template=template,
            attrs=attrs,
            trace_id=trace_id,
        )
    session.flush()
    _recompute_all(session, service.id)
    return get_error_telemetry(
        session,
        service="checkout-api",
        window_start=NOW,
        window_end=NOW + timedelta(minutes=2),
        baseline_sparse_threshold=1,
    ).model_dump(mode="json")


def _service(session: Session, name: str) -> Service:
    service = Service(name=name)
    session.add(service)
    session.flush()
    return service


def _commit(session: Session, service_id: int, sha: str, committed_at: datetime) -> Commit:
    commit = Commit(
        sha=sha,
        service_id=service_id,
        authored_at=committed_at,
        committed_at=committed_at,
        message=f"commit {sha[0]}",
        author=None,
        pr_number=None,
        files_changed=[],
        additions=0,
        deletions=0,
    )
    session.add(commit)
    return commit


def _deployment(
    session: Session, service_id: int, sha: str, uid: str, started_at: datetime
) -> None:
    session.add(
        Deployment(
            uid=uid,
            service_id=service_id,
            commit_sha=sha,
            environment="production",
            started_at=started_at,
            finished_at=started_at,
            status="success",
        )
    )


def _infra(session: Session, uid: str, applied_at: datetime, service_id: int | None) -> None:
    session.add(
        InfraChange(
            uid=uid,
            provider="terraform",
            resource_type="aws_lb",
            resource_name=uid,
            resource_id=None,
            action="update",
            attribute_diff={},
            applied_at=applied_at,
            apply_id=uid,
            source_ref=None,
            service_id=service_id,
        )
    )


def _event(
    session: Session,
    service_id: int,
    uid: str,
    ts: datetime,
    message: str,
    *,
    template: str,
    status: int = 500,
    level: str = "error",
    trace_id: str | None = None,
    attrs: dict[str, object] | None = None,
) -> None:
    session.add(
        LogEvent(
            uid=uid,
            ts=ts,
            service_id=service_id,
            level=level,
            status_code=status,
            trace_id=trace_id,
            message=message,
            template_hash=template,
            raw=message,
            attrs=attrs or {},
            source_file=uid,
            source_offset=0,
        )
    )


def _recompute_all(session: Session, service_id: int) -> None:
    buckets = {
        NOW - timedelta(minutes=2),
        NOW - timedelta(minutes=1),
        NOW,
        NOW + timedelta(minutes=1),
    }
    dirty = capture_dirty_set(session, changed={(service_id, bucket) for bucket in buckets})
    recompute(session, dirty=dirty)
    assert session.query(ErrorRollup).count() > 0

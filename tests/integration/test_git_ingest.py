from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from aegis.config import Settings
from aegis.db.models import Deployment, Service
from aegis.ingest.git import (
    GitExport,
    IllegalDeploymentTransition,
    UpsertCounts,
    upsert_commits,
    upsert_deployments,
)
from aegis.ingest.normalize import ServiceRegistry


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


def test_exact_deployment_replay_is_a_noop(session: Session) -> None:
    export = _export(deployments=[_deployment("1", "in_progress", None)])
    registry = _registry(session, export.repo)

    assert upsert_commits(session, export, registry) == UpsertCounts(inserted=1)
    assert upsert_deployments(session, export, registry, Settings()) == UpsertCounts(inserted=1)
    assert upsert_deployments(session, export, registry, Settings()) == UpsertCounts(unchanged=1)


@pytest.mark.parametrize("to_status", ["success", "failed", "rolled_back"])
def test_every_permitted_in_progress_transition_updates(
    session: Session, to_status: str
) -> None:
    initial = _deployment("1", "in_progress", None)
    export = _export(deployments=[initial])
    registry = _registry(session, export.repo)
    upsert_commits(session, export, registry)
    upsert_deployments(session, export, registry, Settings())

    next_export = _export(deployments=[_deployment("1", to_status, _finished_at())])

    assert upsert_deployments(session, next_export, registry, Settings()) == UpsertCounts(updated=1)
    stored = session.scalars(select(Deployment)).one()
    assert stored.status == to_status
    assert stored.finished_at == _finished_at()


def test_success_to_rolled_back_updates(session: Session) -> None:
    export = _export(deployments=[_deployment("1", "success", _finished_at())])
    registry = _registry(session, export.repo)
    upsert_commits(session, export, registry)
    upsert_deployments(session, export, registry, Settings())

    next_export = _export(deployments=[_deployment("1", "rolled_back", _finished_at())])

    assert upsert_deployments(session, next_export, registry, Settings()) == UpsertCounts(updated=1)


def test_differing_same_status_raises(session: Session) -> None:
    export = _export(deployments=[_deployment("1", "success", _finished_at())])
    registry = _registry(session, export.repo)
    upsert_commits(session, export, registry)
    upsert_deployments(session, export, registry, Settings())

    changed_finish = _finished_at() + timedelta(minutes=1)
    next_export = _export(deployments=[_deployment("1", "success", changed_finish)])

    with pytest.raises(IllegalDeploymentTransition, match="illegal deployment transition"):
        upsert_deployments(session, next_export, registry, Settings())


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        ("success", "in_progress"),
        ("success", "failed"),
        ("failed", "in_progress"),
        ("failed", "success"),
        ("failed", "rolled_back"),
        ("rolled_back", "in_progress"),
        ("rolled_back", "success"),
        ("rolled_back", "failed"),
    ],
)
def test_every_forbidden_differing_transition_raises(
    session: Session, from_status: str, to_status: str
) -> None:
    initial_finished = None if from_status == "in_progress" else _finished_at()
    export = _export(deployments=[_deployment("1", from_status, initial_finished)])
    registry = _registry(session, export.repo)
    upsert_commits(session, export, registry)
    upsert_deployments(session, export, registry, Settings())

    next_export = _export(deployments=[_deployment("1", to_status, _finished_at())])

    with pytest.raises(IllegalDeploymentTransition, match="illegal deployment transition"):
        upsert_deployments(session, next_export, registry, Settings())


def test_upsert_counts_are_correct_for_a_mixed_batch(session: Session) -> None:
    initial = _export(
        commit_numbers=[1, 2],
        deployments=[
            _deployment("1", "success", _finished_at()),
            _deployment("2", "in_progress", None),
        ],
    )
    registry = _registry(session, initial.repo)
    assert upsert_commits(session, initial, registry) == UpsertCounts(inserted=2)
    assert upsert_deployments(session, initial, registry, Settings()) == UpsertCounts(inserted=2)

    mixed = _export(
        commit_numbers=[1, 3],
        deployments=[
            _deployment("1", "success", _finished_at()),
            _deployment("2", "success", _finished_at()),
            _deployment("3", "in_progress", None),
        ],
    )

    assert upsert_commits(session, mixed, registry) == UpsertCounts(inserted=1, unchanged=1)
    assert upsert_deployments(session, mixed, registry, Settings()) == UpsertCounts(
        inserted=1, updated=1, unchanged=1
    )


def _registry(session: Session, repo: str) -> ServiceRegistry:
    service = Service(name=f"git-ingest-{uuid4().hex}", repo=repo)
    session.add(service)
    session.flush()
    return ServiceRegistry.load([service])


def _export(
    *,
    repo: str = "acme/checkout",
    commit_numbers: list[int] | None = None,
    deployments: list[dict[str, object]] | None = None,
) -> GitExport:
    numbers = commit_numbers or [1]
    return GitExport.model_validate(
        {
            "repo": repo,
            "commits": [_commit(number) for number in numbers],
            "deploys": deployments or [],
        }
    )


def _commit(number: int) -> dict[str, object]:
    timestamp = _started_at().isoformat().replace("+00:00", "Z")
    return {
        "sha": _sha(str(number)),
        "author": "Ada Lovelace",
        "authored_at": timestamp,
        "committed_at": timestamp,
        "message": f"commit {number}",
        "pr_number": None,
        "files_changed": [],
    }


def _deployment(number: str, status: str, finished_at: datetime | None) -> dict[str, object]:
    return {
        "commit_sha": _sha(number),
        "environment": "production",
        "started_at": _started_at().isoformat().replace("+00:00", "Z"),
        "finished_at": finished_at.isoformat().replace("+00:00", "Z") if finished_at else None,
        "status": status,
    }


def _sha(number: str) -> str:
    return f"{int(number):040x}"


def _started_at() -> datetime:
    return datetime(2025, 1, 1, 12, 0, tzinfo=UTC)


def _finished_at() -> datetime:
    return _started_at() + timedelta(minutes=5)

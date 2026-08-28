# ruff: noqa: E501
from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from aegis.db.models import InfraChange, Service
from aegis.ingest.normalize import ServiceRegistry
from aegis.ingest.terraform import ingest_terraform

_MISSING = object()


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


def _registry(session: Session) -> ServiceRegistry:
    service = Service(name="payments-api", infra_tags={"service": "payments-api"})
    session.add(service)
    session.flush()
    return ServiceRegistry.load([service])


def _plan(
    actions: list[str], *, after: object = _MISSING, before: object = _MISSING
) -> dict[str, object]:
    return {
        "resource_changes": [
            {
                "address": 'module.payments.aws_db_instance.pool["a"]',
                "type": "aws_db_instance",
                "provider_name": "registry.terraform.io/hashicorp/aws",
                "change": {
                    "actions": actions,
                    "before": before if before is not _MISSING else {"size": 20},
                    "after": after if after is not _MISSING else {"size": 30, "tags": {"service": "payments-api"}},
                    "after_unknown": {},
                    "before_sensitive": {},
                    "after_sensitive": {},
                },
            }
        ]
    }


@pytest.mark.parametrize("status", ["failed", "running"])
def test_terraform_requires_a_successful_apply(
    session: Session, tmp_path: Path, status: str
) -> None:
    plan_path, applies_path = tmp_path / "plan-a.json", tmp_path / "applies.json"
    plan_path.write_text(json.dumps(_plan(["update"])))
    applies_path.write_text(json.dumps([{"apply_id": "a", "plan_file": plan_path.name, "status": status, "applied_at": "2026-08-19T14:00:00Z"}]))

    assert ingest_terraform(session, plan_path=plan_path, applies_path=applies_path, registry=_registry(session)) == 0
    assert session.scalars(select(InfraChange)).all() == []


@pytest.mark.parametrize("actions", [["delete", "create"], ["create", "delete"]])
def test_terraform_replacement_orders_map_to_replace(
    session: Session, tmp_path: Path, actions: list[str]
) -> None:
    plan_path, applies_path = tmp_path / "plan-a.json", tmp_path / "applies.json"
    plan_path.write_text(json.dumps(_plan(actions)))
    applies_path.write_text(json.dumps([{"apply_id": "a", "plan_file": plan_path.name, "status": "success", "applied_at": "2026-08-19T14:00:00Z"}]))

    assert ingest_terraform(session, plan_path=plan_path, applies_path=applies_path, registry=_registry(session)) == 1
    assert session.scalar(select(InfraChange.action)) == "replace"


def test_delete_falls_back_to_before_tags_for_attribution(session: Session, tmp_path: Path) -> None:
    plan_path, applies_path = tmp_path / "plan-a.json", tmp_path / "applies.json"
    plan_path.write_text(json.dumps(_plan(["delete"], before={"tags": {"service": "payments-api"}, "size": 20}, after=None)))
    applies_path.write_text(json.dumps([{"apply_id": "a", "plan_file": plan_path.name, "status": "success", "applied_at": "2026-08-19T14:00:00Z"}]))

    registry = _registry(session)
    assert ingest_terraform(session, plan_path=plan_path, applies_path=applies_path, registry=registry) == 1
    row = session.scalar(select(InfraChange))
    assert row is not None
    assert row.service_id is not None


def test_unmatched_after_tags_fall_back_to_before_tags(session: Session, tmp_path: Path) -> None:
    plan_path, applies_path = tmp_path / "plan-a.json", tmp_path / "applies.json"
    plan_path.write_text(
        json.dumps(
            _plan(
                ["update"],
                before={"tags": {"service": "payments-api"}, "size": 20},
                after={"tags": {"service": "renamed-later"}, "size": 30},
            )
        )
    )
    applies_path.write_text(
        json.dumps(
            [
                {
                    "apply_id": "a",
                    "plan_file": plan_path.name,
                    "status": "success",
                    "applied_at": "2026-08-19T14:00:00Z",
                }
            ]
        )
    )

    assert (
        ingest_terraform(
            session,
            plan_path=plan_path,
            applies_path=applies_path,
            registry=_registry(session),
        )
        == 1
    )
    assert session.scalar(select(InfraChange.service_id)) is not None

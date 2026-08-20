"""Execute every scenario reachability assertion against real tool responses."""

from __future__ import annotations

from collections.abc import Generator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from aegis.config import Settings
from aegis.db.models import Service, UnresolvedEvent
from aegis.ingest.git import load_git_export, upsert_commits, upsert_deployments
from aegis.ingest.logs import ParseContext, ResolvedDraft, UnresolvedDraft, iter_drafts
from aegis.ingest.normalize import ServiceRegistry
from aegis.ingest.pipeline import ingest_source
from aegis.mcp_server.queries import get_error_telemetry, get_incident_diff

ROOT = Path(__file__).parents[2]
CORPUS = ROOT / "corpus"
SCENARIO_PATH = CORPUS / "scenarios" / "checkout-5xx-spike.yaml"
LOG_PATH = CORPUS / "logs" / "checkout-api.log"


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


def test_reachability_entries_resolve_against_live_tool_responses(session: Session) -> None:
    scenario = _scenario()
    service, registry = _seed_committed_corpus(session)
    alert = scenario["alert"]
    start = _timestamp(alert["window_start"])
    end = _timestamp(alert["window_end"])
    responses = {
        "get_incident_diff": get_incident_diff(
            session,
            service=service.name,
            window_start=start,
            window_end=end,
        ),
        "get_error_telemetry": get_error_telemetry(
            session,
            service=service.name,
            window_start=start,
            window_end=end,
            baseline_sparse_threshold=Settings().baseline_sparse_threshold,
        ),
    }
    del registry  # The persisted service and responses are the test's public boundary now.

    for entry in scenario["expect"]["reachability"]:
        assert isinstance(entry, Mapping)
        tool = entry["tool"]
        field = entry["field"]
        assert isinstance(tool, str)
        assert isinstance(field, str)
        values = resolve_field_path(responses[tool], field)
        assert values, f"{entry['fact']!r} has no reachable value at {field!r}"
        if "value_contains" in entry:
            required = entry["value_contains"]
            assert isinstance(required, Sequence) and not isinstance(required, str)
            assert any(_contains_all(value, required) for value in values), entry["fact"]
        if "value_predicate" in entry:
            predicate = entry["value_predicate"]
            assert isinstance(predicate, str)
            assert any(_matches_predicate(value, predicate) for value in values), entry["fact"]


def resolve_field_path(value: object, path: str) -> list[object]:
    """Resolve the scenario's small ``a.b[].c`` field-path language."""
    values = [value]
    for segment in path.split("."):
        is_list = segment.endswith("[]")
        name = segment[:-2] if is_list else segment
        if not name:
            raise ValueError(f"invalid field path segment: {segment!r}")
        next_values: list[object] = []
        for current in values:
            child = _field(current, name)
            if is_list:
                if not isinstance(child, Sequence) or isinstance(child, str | bytes | bytearray):
                    raise ValueError(f"field {name!r} is not a list")
                next_values.extend(child)
            else:
                next_values.append(child)
        values = next_values
    return values


def _seed_committed_corpus(session: Session) -> tuple[Service, ServiceRegistry]:
    configured = _services()[0]
    service = Service(
        name=str(configured["name"]),
        repo=str(configured["repo"]),
        log_keys=list(configured["log_keys"]),
        k8s_names=list(configured["k8s_names"]),
        infra_tags={},
        log_timezone=str(configured["log_timezone"]),
    )
    session.add(service)
    session.flush()
    registry = ServiceRegistry.load([service])
    drafts = _drafts(registry)
    cursor = sum(len(draft.raw.encode("utf-8")) for draft in drafts)

    with session.begin_nested():
        report = ingest_source(
            session,
            source="logs/checkout-api.log",
            records=drafts,
            cursor=cursor,
        )
        export = load_git_export(CORPUS / "git" / "checkout.json")
        upsert_commits(session, export, registry)
        upsert_deployments(session, export, registry, Settings())

    assert report.unresolved >= 1
    unresolved = session.scalars(select(UnresolvedEvent)).all()
    assert unresolved and all(event.raw for event in unresolved)
    return service, registry


def _drafts(registry: ServiceRegistry) -> list[ResolvedDraft | UnresolvedDraft]:
    context = ParseContext(
        registry=registry,
        source_file="logs/checkout-api.log",
        default_log_timezone="UTC",
    )
    return list(iter_drafts(LOG_PATH, context))


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        if name not in value:
            raise ValueError(f"field {name!r} is absent")
        return value[name]
    try:
        return getattr(value, name)
    except AttributeError as error:
        raise ValueError(f"field {name!r} is absent") from error


def _contains_all(value: object, required: Sequence[object]) -> bool:
    return isinstance(value, str) and all(str(token) in value for token in required)


def _matches_predicate(value: object, predicate: str) -> bool:
    if predicate == "> 0":
        return isinstance(value, int | float) and not isinstance(value, bool) and value > 0
    raise ValueError(f"unsupported value predicate: {predicate!r}")


def _scenario() -> dict[str, Any]:
    value = yaml.safe_load(SCENARIO_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _services() -> list[dict[str, Any]]:
    value = yaml.safe_load((CORPUS / "services.yaml").read_text(encoding="utf-8"))
    assert isinstance(value, list)
    return cast(list[dict[str, Any]], value)


def _timestamp(value: object) -> datetime:
    assert isinstance(value, str)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)

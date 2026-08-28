"""Execute every scenario reachability assertion against real tool responses."""

from __future__ import annotations

from collections.abc import Generator, Mapping, Sequence
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from aegis.app.run_context import _citations_in_model
from aegis.config import Settings
from aegis.db.models import Service, UnresolvedEvent
from aegis.ingest.git import load_git_export, upsert_commits, upsert_deployments
from aegis.ingest.k8s import ingest_kubernetes
from aegis.ingest.logs import ParseContext, ResolvedDraft, UnresolvedDraft, iter_drafts
from aegis.ingest.normalize import ServiceRegistry
from aegis.ingest.pipeline import ingest_source
from aegis.ingest.terraform import ingest_terraform
from aegis.mcp_server.queries import get_error_telemetry, get_incident_diff

ROOT = Path(__file__).parents[2]
CORPUS = ROOT / "corpus"
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
    services, registry = _seed_committed_corpus(session)
    del registry  # Persisted rows and responses are the test's public boundary now.

    for scenario in _scenarios():
        alert = scenario["alert"]
        service_name = str(alert["service"])
        service = services[service_name]
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
        captured_citations = set().union(
            *(_citations_in_model(response) for response in responses.values())
        )
        missing_citations = {
            pattern
            for pattern in scenario["expect"]["must_cite"]
            if not any(fnmatch(citation, pattern) for citation in captured_citations)
        }
        assert not missing_citations, (
            f"{scenario['name']} declares citations its tools do not return: "
            f"{sorted(missing_citations)}"
        )

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


def _seed_committed_corpus(
    session: Session,
) -> tuple[dict[str, Service], ServiceRegistry]:
    session.execute(
        text(
            "TRUNCATE error_rollups, log_events, unresolved_events, deployments, "
            "commits, infra_changes, ingest_watermarks, incidents, services "
            "RESTART IDENTITY CASCADE"
        )
    )
    services = {
        str(configured["name"]): Service(
            name=str(configured["name"]),
            repo=str(configured["repo"]),
            log_keys=list(configured.get("log_keys", [])),
            k8s_names=list(configured.get("k8s_names", [])),
            infra_tags=dict(configured.get("infra_tags", {})),
            log_timezone=str(configured.get("log_timezone", "UTC")),
        )
        for configured in _services()
    }
    session.add_all(services.values())
    session.flush()
    registry = ServiceRegistry.load(services.values())
    manifest = _log_manifest()
    unresolved_count = 0

    with session.begin_nested():
        for log_name, declaration in manifest.items():
            assert isinstance(declaration, Mapping)
            drafts = _drafts(log_name, declaration, registry)
            report = ingest_source(
                session,
                source=f"logs/{log_name}",
                records=drafts,
                cursor=sum(len(draft.raw.encode("utf-8")) for draft in drafts),
            )
            unresolved_count += report.unresolved

        for path in sorted((CORPUS / "git").glob("*.json")):
            export = load_git_export(path)
            upsert_commits(session, export, registry)
            upsert_deployments(session, export, registry, Settings())

        applies_path = CORPUS / "terraform" / "applies.json"
        for plan_path in sorted((CORPUS / "terraform").glob("plan-*.json")):
            ingest_terraform(
                session,
                plan_path=plan_path,
                applies_path=applies_path,
                registry=registry,
            )

        for name in ("pod-status.json", "events.json"):
            ingest_kubernetes(session, path=CORPUS / "k8s" / name, registry=registry)

    assert unresolved_count >= 1
    unresolved = session.scalars(select(UnresolvedEvent)).all()
    assert unresolved and all(event.raw for event in unresolved)
    return services, registry


def _drafts(
    log_name: str, declaration: Mapping[object, object], registry: ServiceRegistry
) -> list[ResolvedDraft | UnresolvedDraft]:
    context = ParseContext(
        registry=registry,
        source_file=f"logs/{log_name}",
        default_log_timezone=str(declaration.get("timezone", "UTC")),
        declared_service=(
            str(declaration["service"]) if declaration.get("service") is not None else None
        ),
    )
    return list(iter_drafts(CORPUS / "logs" / log_name, context))


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
    if predicate == "== 0":
        return isinstance(value, int | float) and not isinstance(value, bool) and value == 0
    if predicate == "is empty":
        return (
            isinstance(value, Mapping | Sequence)
            and not isinstance(value, str | bytes | bytearray)
            and not value
        )
    raise ValueError(f"unsupported value predicate: {predicate!r}")


def test_reachability_predicates_cover_empty_collections_and_zero_values() -> None:
    assert _matches_predicate([], "is empty")
    assert _matches_predicate(0, "== 0")
    assert not _matches_predicate(["deploy"], "is empty")
    assert not _matches_predicate(1, "== 0")


def _scenarios() -> list[dict[str, Any]]:
    return [
        cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted((CORPUS / "scenarios").glob("*.yaml"))
    ]


def _log_manifest() -> dict[str, dict[str, object]]:
    value = yaml.safe_load((CORPUS / "logs" / "manifest.yaml").read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, dict[str, object]], value)


def _services() -> list[dict[str, Any]]:
    value = yaml.safe_load((CORPUS / "services.yaml").read_text(encoding="utf-8"))
    assert isinstance(value, list)
    return cast(list[dict[str, Any]], value)


def _timestamp(value: object) -> datetime:
    assert isinstance(value, str)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)

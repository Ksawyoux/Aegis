"""The intentionally thin command-line interface for Aegis."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from uuid import uuid4

import typer
import uvicorn
import yaml  # type: ignore[import-untyped]
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from aegis.agent.trace_view import (
    RunNotFoundError,
    TraceIntegrityError,
    load_stored_run,
    render_trace,
    validate_trace_integrity,
)
from aegis.api.app import create_app
from aegis.app.investigate import build_investigation_request
from aegis.app.investigate import investigate as run_investigation
from aegis.app.render import render_markdown
from aegis.app.run_context import InMemorySink, RunContext
from aegis.config import Settings
from aegis.db.models import Service, UnresolvedEvent
from aegis.db.session import create_database_engine
from aegis.embeddings.providers import OpenAIEmbeddings
from aegis.ingest.git import load_git_export, upsert_commits, upsert_deployments
from aegis.ingest.k8s import ingest_kubernetes
from aegis.ingest.logs import FORMATS, ParseContext, detect_format, iter_drafts
from aegis.ingest.normalize import ServiceRegistry
from aegis.ingest.pipeline import IngestReport, LogRecord, ingest_source
from aegis.ingest.postmortems import ingest_postmortem
from aegis.ingest.terraform import ingest_terraform

app = typer.Typer(no_args_is_help=True)
db_app = typer.Typer(no_args_is_help=True)
ingest_app = typer.Typer(no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(ingest_app, name="ingest")


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Bring the configured database schema to its latest revision."""
    command.upgrade(Config("alembic.ini"), "head")


@ingest_app.command("services")
def ingest_services() -> None:
    """Load the configured service identities and reject ambiguous mappings."""
    settings = Settings()
    engine = create_database_engine(settings)
    try:
        services_path = settings.corpus_dir / "services.yaml"
        with Session(engine) as session, session.begin():
            _upsert_services(session, services_path)
        typer.echo(f"services: {len(_service_definitions(services_path))} loaded")
    finally:
        engine.dispose()


@ingest_app.command("all")
def ingest_all() -> None:
    """Ingest every committed offline evidence source in the corpus."""
    settings = Settings()
    engine = create_database_engine(settings)
    try:
        with Session(engine) as session, session.begin():
            _upsert_services(session, settings.corpus_dir / "services.yaml")
        typer.echo("services: loaded")

        for git_path in sorted((settings.corpus_dir / "git").glob("*.json")):
            with Session(engine) as session, session.begin():
                registry = _registry_from_session(session)
                export = load_git_export(git_path)
                commits = upsert_commits(session, export, registry, settings=settings)
                deployments = upsert_deployments(session, export, registry, settings)
            typer.echo(
                f"git {git_path.name}: commits inserted={commits.inserted} "
                f"unchanged={commits.unchanged}; deployments inserted={deployments.inserted} "
                f"updated={deployments.updated} unchanged={deployments.unchanged}"
            )

        for log_path in sorted((settings.corpus_dir / "logs").glob("*")):
            if not log_path.is_file() or log_path.name == "manifest.yaml":
                continue
            with Session(engine) as session, session.begin():
                registry = _registry_from_session(session)
                relative_path = log_path.relative_to(settings.corpus_dir).as_posix()
                manifest = _log_manifest(settings.corpus_dir / "logs" / "manifest.yaml")
                declaration = manifest.get(log_path.name, {})
                format_hint = _optional_string(declaration.get("format"))
                if format_hint is not None:
                    detected = detect_format(log_path, FORMATS)
                    if detected.name != format_hint:
                        raise ValueError(
                            f"log format mismatch for {log_path.name}: "
                            f"declared {format_hint!r}, detected {detected.name!r}"
                        )
                records = tuple(
                    iter_drafts(
                        log_path,
                        ParseContext(
                            registry=registry,
                            source_file=relative_path,
                            default_log_timezone=str(declaration.get("timezone", "UTC")),
                            declared_service=_optional_string(declaration.get("service")),
                        ),
                    )
                )
                report = ingest_source(
                    session,
                    source=relative_path,
                    records=records,
                    cursor=_draft_cursor(records),
                )
            _render_ingest_report(relative_path, report)

        terraform_dir = settings.corpus_dir / "terraform"
        applies_path = terraform_dir / "applies.json"
        if applies_path.exists():
            for plan_path in sorted(terraform_dir.glob("plan-*.json")):
                with Session(engine) as session, session.begin():
                    count = ingest_terraform(
                        session,
                        plan_path=plan_path,
                        applies_path=applies_path,
                        registry=_registry_from_session(session),
                    )
                typer.echo(f"terraform {plan_path.name}: inserted={count}")

        k8s_dir = settings.corpus_dir / "k8s"
        for name in ("pod-status.json", "events.json"):
            source = k8s_dir / name
            if source.exists():
                with Session(engine) as session, session.begin():
                    count = ingest_kubernetes(
                        session, path=source, registry=_registry_from_session(session)
                    )
                typer.echo(f"k8s {name}: inserted={count}")

        _ingest_postmortems(engine, settings)
        _render_unresolved_report(engine)
    finally:
        engine.dispose()


def _ingest_postmortems(engine: Engine, settings: Settings) -> None:
    """Embed and store the postmortem corpus, or say plainly why it was skipped.

    This is the only ingest path that needs a remote provider, so it is also the
    only one that can be unavailable on an otherwise working machine. It reports
    the skip rather than passing silently: without it ``search_similar_postmortems``
    returns nothing, and an empty result is indistinguishable from "no similar
    incident exists" at the point where the agent reads it.
    """
    directory = settings.corpus_dir / "postmortems"
    sources = sorted(directory.glob("*.md")) if directory.is_dir() else []
    if not sources:
        typer.echo("postmortems: none found")
        return
    if settings.openai_api_key is None:
        typer.echo(f"postmortems: skipped {len(sources)} file(s) -- OPENAI_API_KEY is not set")
        return

    provider = OpenAIEmbeddings(settings)
    for source in sources:
        with Session(engine) as session, session.begin():
            ingest_postmortem(session, path=source, provider=provider)
    typer.echo(f"postmortems: ingested={len(sources)}")


@app.command("serve-mcp")
def serve_mcp() -> None:
    """Run the MCP server over standard input and output."""
    from aegis.mcp_server.server import main

    main()


@app.command("serve-api")
def serve_api(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Serve the unauthenticated operational API on loopback by default."""
    uvicorn.run(create_app(), host=host, port=port)


@app.command("investigate")
def investigate_command(
    scenario: Path = typer.Option(..., "--scenario", exists=True, readable=True),
) -> None:
    """Run an investigation described by a scenario and render its result."""
    request = build_investigation_request(_load_scenario(scenario))
    summary = run_investigation(request, RunContext(uuid4().hex, InMemorySink()))
    typer.echo(render_markdown(summary))
    typer.echo(json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True))


@app.command("trace")
def trace_command(
    run_id: str = typer.Option(..., "--run-id"),
    json_output: bool = typer.Option(False, "--json"),
    full: bool = typer.Option(False, "--full"),
) -> None:
    """Render a persisted investigation's stored tool-call trace (read-only).

    Exits 1 when no incident carries ``run_id``, and 2 when the stored
    envelope fails integrity validation -- after printing whatever could be
    rendered, so a missing citation or malformed envelope is still visible
    rather than hidden behind a bare non-zero exit.
    """
    settings = Settings()
    engine = create_database_engine(settings)
    try:
        with Session(engine) as session:
            try:
                run = load_stored_run(session, run_id=run_id)
            except RunNotFoundError as error:
                typer.echo(str(error), err=True)
                raise typer.Exit(code=1) from error
            except TraceIntegrityError as error:
                typer.echo(str(error), err=True)
                raise typer.Exit(code=2) from error
    finally:
        engine.dispose()

    if json_output:
        typer.echo(
            json.dumps(run.record.model_dump(mode="json"), indent=2, sort_keys=True)
        )
    else:
        typer.echo(render_trace(run, include_payloads=full))

    try:
        validate_trace_integrity(run)
    except TraceIntegrityError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error


def _load_scenario(path: Path) -> dict[str, object]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise typer.BadParameter("scenario must be a YAML object", param_hint="--scenario")
    return value


def _upsert_services(session: Session, path: Path) -> ServiceRegistry:
    definitions = _service_definitions(path)
    # Validate the full corpus configuration before it can mutate the database.
    ServiceRegistry.load([_service_from_definition(definition) for definition in definitions])

    for definition in definitions:
        service = _service_from_definition(definition)
        existing = session.scalar(
            select(Service).where(Service.name == service.name).with_for_update()
        )
        if existing is None:
            session.add(service)
            continue
        existing.repo = service.repo
        existing.log_keys = service.log_keys
        existing.k8s_names = service.k8s_names
        existing.infra_tags = service.infra_tags
        existing.log_timezone = service.log_timezone
    session.flush()
    return _registry_from_session(session)


def _service_definitions(path: Path) -> list[dict[str, object]]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("services.yaml must contain a list of service objects")
    return value


def _service_from_definition(definition: Mapping[str, object]) -> Service:
    name = definition.get("name")
    if not isinstance(name, str):
        raise ValueError("service name must be a string")
    repo = definition.get("repo")
    if repo is not None and not isinstance(repo, str):
        raise ValueError(f"repo for {name!r} must be a string")
    return Service(
        name=name,
        repo=repo,
        log_keys=_string_list(definition.get("log_keys"), "log_keys", name),
        k8s_names=_string_list(definition.get("k8s_names"), "k8s_names", name),
        infra_tags=_mapping_value(definition.get("infra_tags"), "infra_tags", name),
        log_timezone=_string_value(definition.get("log_timezone", "UTC"), "log_timezone", name),
    )


def _string_list(value: object, field: str, service: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} for {service!r} must be a list of strings")
    return value


def _mapping_value(value: object, field: str, service: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field} for {service!r} must be an object with string keys")
    return value


def _string_value(value: object, field: str, service: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} for {service!r} must be a string")
    return value


def _registry_from_session(session: Session) -> ServiceRegistry:
    return ServiceRegistry.load(session.scalars(select(Service).order_by(Service.name)).all())


def _draft_cursor(records: Sequence[LogRecord]) -> int:
    return sum(len(record.raw.encode("utf-8")) for record in records)


def _log_manifest(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("log manifest must be an object")
    result: dict[str, dict[str, object]] = {}
    for name, declaration in value.items():
        if not isinstance(name, str) or not isinstance(declaration, dict):
            raise ValueError("log manifest entries must map a file name to an object")
        result[name] = declaration
    return result


def _optional_string(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise ValueError("manifest service must be a string")


def _render_ingest_report(source: str, report: IngestReport) -> None:
    typer.echo(
        f"logs {source}: inserted={report.inserted} duplicates={report.duplicates} "
        f"promoted={report.promoted} unresolved={report.unresolved}; "
        f"rollups dirty={report.rollup.dirty_pairs} deleted={report.rollup.deleted} "
        f"inserted={report.rollup.inserted}"
    )


def _render_unresolved_report(engine: Engine) -> None:
    with Session(engine) as session:
        rows = session.execute(
            select(UnresolvedEvent.reason, func.count())
            .group_by(UnresolvedEvent.reason)
            .order_by(UnresolvedEvent.reason)
        ).all()
    if not rows:
        typer.echo("unresolved: none")
        return
    typer.echo("unresolved:")
    for reason, count in rows:
        typer.echo(f"  {reason}: {count}")


__all__ = ["app", "build_investigation_request"]

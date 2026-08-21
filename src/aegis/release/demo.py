"""The `make demo` / `make demo-live` release coordinator (Part 4 §2, §6).

The Makefile stays a thin published interface; this module owns branching,
diagnostics, subprocess environment construction, database inspection, and
cleanup advice. Every child command runs with
``subprocess.run(argv, cwd=root, env=environment, check=True)`` -- no
``shell=True``, no interpolated secret, no ambient working directory -- and a
failing stage stops the run immediately with that stage named, rather than
letting a later stage print misleading PASS output after an earlier failure.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
import yaml  # type: ignore[import-untyped]
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from aegis.config import Settings

# Fixed at five because that is the milestone's falsifiable claim (Part 4 §3.2,
# §10.5), not a count derived from whatever happens to be on disk right now.
EXPECTED_SCENARIO_COUNT = 5

REQUIRED_PROJECT_TABLES: tuple[str, ...] = (
    "alembic_version",
    "services",
    "commits",
    "deployments",
    "infra_changes",
    "log_events",
    "unresolved_events",
    "error_rollups",
    "postmortems",
    "postmortem_chunks",
    "incidents",
    "ingest_watermarks",
)

# Evidence-table source families this worktree's corpus actually populates.
# infra_changes and postmortem_chunks are Part 2 deliverables not present in
# this tree; asserting a fabricated non-zero count for them here would be
# exactly the false-green defect Part 4 exists to close, so they are
# deliberately absent from this tuple rather than stubbed in.
NON_ZERO_SOURCE_TABLES: tuple[str, ...] = (
    "services",
    "commits",
    "deployments",
    "log_events",
    "error_rollups",
)

DemoDatabaseMode = Literal["compose", "external"]

_PLACEHOLDER_KEY_VALUES = frozenset({"", "sk-...", "changeme", "your-key-here", "xxx"})


class DemoError(RuntimeError):
    """A named release stage failed; the message states which one and why."""

    def __init__(self, stage: str, detail: str) -> None:
        self.stage = stage
        self.detail = detail
        super().__init__(f"ERROR [{stage}]: {detail}")


@dataclass(frozen=True)
class DemoOptions:
    root: Path
    database_mode: DemoDatabaseMode
    database_url: str
    compose_project: str = "aegis-context-demo"
    compose_postgres_port: int = 5433


@dataclass(frozen=True)
class ScenarioResult:
    """One scenario's demo-mode outcome, read back from its persisted incident."""

    name: str
    incident_id: int
    run_id: str
    confidence: str


@dataclass(frozen=True)
class DemoResult:
    corpus_digest: str
    scenario_results: tuple[ScenarioResult, ...]
    clean_start: bool


@dataclass(frozen=True)
class LiveDemoOptions:
    github_repo: str
    public_base_url: str
    timeout_seconds: int = 300


# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------


def preflight(options: DemoOptions) -> None:
    """Verify every prerequisite before starting Docker or mutating a database.

    Fails on the first unmet prerequisite, in the order the Part 4
    specification lists them (§2.2), so an operator sees the actual blocker
    rather than an unrelated downstream error.
    """
    _require_file(options.root / "uv.lock", "prerequisites")
    _require_file(options.root / "alembic.ini", "prerequisites")
    _require_file(options.root / "corpus" / "services.yaml", "prerequisites")
    scenarios = _load_scenarios(options.root)
    if len(scenarios) != EXPECTED_SCENARIO_COUNT:
        raise DemoError(
            "prerequisites",
            f"expected exactly {EXPECTED_SCENARIO_COUNT} scenario manifests under "
            f"corpus/scenarios, found {len(scenarios)}.",
        )

    openai_key = _stripped_env("OPENAI_API_KEY")
    if not openai_key or openai_key in _PLACEHOLDER_KEY_VALUES:
        raise DemoError(
            "prerequisites",
            "OPENAI_API_KEY is unset. Live embedding calls cannot run and a skipped "
            "evaluation does not satisfy make demo.",
        )
    anthropic_key = _stripped_env("ANTHROPIC_API_KEY")
    if not anthropic_key or anthropic_key in _PLACEHOLDER_KEY_VALUES:
        raise DemoError(
            "prerequisites",
            "ANTHROPIC_API_KEY is unset. The five agent evaluations cannot run and "
            "skipped evaluations do not satisfy make demo.",
        )

    if options.database_mode not in ("compose", "external"):
        raise DemoError(
            "prerequisites", f"database mode must be 'compose' or 'external', "
            f"got {options.database_mode!r}."
        )
    if options.database_mode == "compose":
        _require_docker_compose(options)
    else:
        if not options.database_url:
            raise DemoError(
                "prerequisites",
                "AEGIS_DEMO_DB_MODE=external requires an explicit AEGIS_DATABASE_URL.",
            )

    _require_corpus_sources_present(options.root)
    _require_unique_scenario_identity(scenarios)

    settings = Settings()
    if settings.anthropic_model != "claude-opus-5":
        raise DemoError(
            "prerequisites", f"AEGIS_ANTHROPIC_MODEL must be claude-opus-5, "
            f"got {settings.anthropic_model!r}."
        )
    if settings.embedding_model != "text-embedding-3-small":
        raise DemoError(
            "prerequisites", f"AEGIS_EMBEDDING_MODEL must be text-embedding-3-small, "
            f"got {settings.embedding_model!r}."
        )
    if settings.embedding_dim != 1024:
        raise DemoError(
            "prerequisites", f"embedding_dim must be 1024, got {settings.embedding_dim!r}."
        )


def _require_file(path: Path, stage: str) -> None:
    if not path.is_file():
        raise DemoError(stage, f"required file is missing: {path}")


def _stripped_env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _load_scenarios(root: Path) -> list[dict[str, Any]]:
    scenarios = []
    for path in sorted((root / "corpus" / "scenarios").glob("*.yaml")):
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise DemoError("prerequisites", f"{path} is not a YAML mapping")
        scenarios.append(loaded)
    return scenarios


def _require_corpus_sources_present(root: Path) -> None:
    corpus = root / "corpus"
    git_exports = list((corpus / "git").glob("*.json")) if (corpus / "git").is_dir() else []
    if not git_exports:
        raise DemoError("prerequisites", "no committed Git export found under corpus/git")
    logs_dir = corpus / "logs"
    logs = [p for p in logs_dir.iterdir() if p.is_file()] if logs_dir.is_dir() else []
    if not logs:
        raise DemoError("prerequisites", "no committed log fixtures found under corpus/logs")


def _require_unique_scenario_identity(scenarios: Sequence[Mapping[str, Any]]) -> None:
    seen_names: set[str] = set()
    seen_dedup_keys: set[str] = set()
    for scenario in scenarios:
        name = scenario.get("name")
        if not isinstance(name, str) or name in seen_names:
            raise DemoError("prerequisites", f"duplicate or missing scenario name: {name!r}")
        seen_names.add(name)
        alert = scenario.get("alert")
        dedup_key = alert.get("dedup_key") if isinstance(alert, dict) else None
        if isinstance(dedup_key, str):
            if dedup_key in seen_dedup_keys:
                raise DemoError("prerequisites", f"duplicate scenario dedup_key: {dedup_key!r}")
            seen_dedup_keys.add(dedup_key)


def _require_docker_compose(options: DemoOptions) -> None:
    docker = _run_capture(["docker", "version"], cwd=options.root)
    if docker.returncode != 0:
        raise DemoError(
            "prerequisites",
            "Docker is required for AEGIS_DEMO_DB_MODE=compose (the default). Install "
            "Docker, or rehearse locally with AEGIS_DEMO_DB_MODE=external and a disposable "
            "PostgreSQL 14+ database -- see README.md.",
        )
    compose = _run_capture(["docker", "compose", "version"], cwd=options.root)
    if compose.returncode != 0:
        raise DemoError("prerequisites", "docker compose is required and did not respond")


def _run_capture(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, returncode=1, stdout="", stderr=str(exc))


# --------------------------------------------------------------------------
# Child environment (Part 4 §2.1)
# --------------------------------------------------------------------------


def build_child_environment(options: DemoOptions, settings: Settings) -> dict[str, str]:
    """Construct the one explicit environment passed to every child command.

    ``StdioServerParameters`` does not inherit the parent environment, so
    every spawned MCP subprocess sees exactly this -- not the operator's
    ambient shell -- which is what keeps the API process, pytest, and the MCP
    child pointed at the same database and provider configuration.
    """
    environment = dict(os.environ)
    environment["AEGIS_DATABASE_URL"] = options.database_url
    environment["AEGIS_CORPUS_DIR"] = str((options.root / "corpus").resolve())
    environment["AEGIS_OPENAI_BASE_URL"] = settings.openai_base_url
    environment["AEGIS_EMBEDDING_MODEL"] = settings.embedding_model
    environment["OPENAI_API_KEY"] = _stripped_env("OPENAI_API_KEY")
    environment["ANTHROPIC_API_KEY"] = _stripped_env("ANTHROPIC_API_KEY")
    environment["AEGIS_DEMO_MODE"] = "1"
    environment["AEGIS_REQUIRE_POSTGRES"] = "1"
    environment["AEGIS_REQUIRE_LIVE_EVAL"] = "1"
    # Ambient Slack config in the operator's own shell must never turn a
    # demo evaluation into five real channel posts (Part 4 traps list).
    environment.pop("AEGIS_SLACK_WEBHOOK_URL", None)
    return environment


def redact_database_url(url: str) -> str:
    """Replace a database URL's password with ``***`` for printing and logs."""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    userinfo = parts.username or ""
    userinfo += ":***"
    host = parts.hostname or ""
    if parts.port:
        host += f":{parts.port}"
    netloc = f"{userinfo}@{host}" if userinfo else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


# --------------------------------------------------------------------------
# Database provisioning and empty-state proof (Part 4 §2.3)
# --------------------------------------------------------------------------


def _connect_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def wait_for_database(database_url: str, *, timeout_seconds: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        engine = _connect_engine(database_url)
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return
        except SQLAlchemyError as exc:
            last_error = exc
            time.sleep(1.0)
        finally:
            engine.dispose()
    raise DemoError(
        "database",
        f"could not reach {redact_database_url(database_url)} within "
        f"{timeout_seconds:.0f}s: {last_error}",
    )


def provision_compose_database(options: DemoOptions, environment: Mapping[str, str]) -> None:
    argv = ["docker", "compose", "-p", options.compose_project, "up", "-d", "postgres"]
    result = subprocess.run(argv, cwd=options.root, env=dict(environment), check=False)
    if result.returncode != 0:
        raise DemoError("database", "docker compose up -d postgres failed")
    wait_for_database(options.database_url)


def assert_database_is_empty(database_url: str) -> None:
    """Refuse a database that already has Aegis tables -- never delete anything.

    Existing schema or corpus would make the reproducibility claim vacuous:
    an operator could pass by re-running against state a previous attempt
    already built, not by reproducing it from nothing (Part 4 §2.3, §9).
    """
    engine = _connect_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT tablename FROM pg_catalog.pg_tables
                    WHERE schemaname = current_schema()
                      AND tablename = ANY(:names)
                    ORDER BY tablename
                    """
                ),
                {"names": list(REQUIRED_PROJECT_TABLES)},
            ).scalars().all()
    finally:
        engine.dispose()
    if rows:
        raise DemoError(
            "database",
            "demo database is not empty; found: " + ", ".join(rows) + ". "
            "Use a new disposable database. No rows were deleted. If this is a stale "
            "compose demo volume: docker compose -p aegis-context-demo down -v",
        )


def verify_migration_and_pgvector(database_url: str) -> tuple[str, str]:
    engine = _connect_engine(database_url)
    try:
        with engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            extension = connection.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            ).scalar()
    except SQLAlchemyError as exc:
        raise DemoError("migration", f"could not read migration state: {exc}") from exc
    finally:
        engine.dispose()
    if not version:
        raise DemoError("migration", "alembic_version has no row after migration")
    if not extension:
        raise DemoError("migration", "pgvector extension is not installed after migration")
    return str(version), str(extension)


# --------------------------------------------------------------------------
# Stage execution
# --------------------------------------------------------------------------


def _run_stage(
    label: str, argv: Sequence[str], *, root: Path, environment: Mapping[str, str]
) -> None:
    print(f"$ {' '.join(argv)}", flush=True)
    result = subprocess.run(list(argv), cwd=root, env=dict(environment), check=False)
    if result.returncode != 0:
        raise DemoError(
            label, f"command failed with exit code {result.returncode}: {' '.join(argv)}"
        )


# --------------------------------------------------------------------------
# Replay digest (Part 4 §2.5)
# --------------------------------------------------------------------------


def compute_replay_digest(engine: Engine) -> tuple[str, dict[str, int]]:
    """Return a content digest and exact per-table counts, ignoring sequence PKs.

    Sequence primary keys and ``created_at`` legitimately depend on database
    allocation order and wall time; everything that constitutes evidence
    identity, ordering, and content is included instead.
    """
    counts: dict[str, int] = {}
    canonical_rows: list[str] = []
    with engine.connect() as connection:
        for table in REQUIRED_PROJECT_TABLES:
            if table in {"alembic_version", "ingest_watermarks"}:
                continue
            count = connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            counts[table] = int(count)

        canonical_rows.extend(_canonical_services(connection))
        canonical_rows.extend(_canonical_commits(connection))
        canonical_rows.extend(_canonical_deployments(connection))
        canonical_rows.extend(_canonical_infra_changes(connection))
        canonical_rows.extend(_canonical_log_events(connection))
        canonical_rows.extend(_canonical_error_rollups(connection))
        canonical_rows.extend(_canonical_postmortem_chunks(connection))

    digest = hashlib.sha256("\n".join(canonical_rows).encode("utf-8")).hexdigest()
    return digest, counts


def _canonical_services(connection: Any) -> list[str]:
    rows = connection.execute(
        text("SELECT name, repo, log_keys, k8s_names, infra_tags FROM services ORDER BY name")
    ).mappings().all()
    return [f"service|{_canonical_json(dict(row))}" for row in rows]


def _canonical_commits(connection: Any) -> list[str]:
    rows = connection.execute(
        text(
            """
            SELECT c.sha, s.name AS service, c.authored_at, c.committed_at, c.message,
                   c.author, c.pr_number, c.files_changed, c.additions, c.deletions
            FROM commits c JOIN services s ON s.id = c.service_id
            ORDER BY c.sha
            """
        )
    ).mappings().all()
    return [f"commit|{_canonical_json(dict(row))}" for row in rows]


def _canonical_deployments(connection: Any) -> list[str]:
    rows = connection.execute(
        text(
            """
            SELECT d.uid, s.name AS service, d.commit_sha, d.environment,
                   d.started_at, d.finished_at, d.status
            FROM deployments d JOIN services s ON s.id = d.service_id
            ORDER BY d.uid
            """
        )
    ).mappings().all()
    return [f"deployment|{_canonical_json(dict(row))}" for row in rows]


def _canonical_infra_changes(connection: Any) -> list[str]:
    rows = connection.execute(
        text(
            """
            SELECT i.uid, s.name AS service, i.provider, i.resource_type, i.resource_name,
                   i.resource_id, i.action, i.attribute_diff, i.applied_at, i.apply_id,
                   i.source_ref
            FROM infra_changes i LEFT JOIN services s ON s.id = i.service_id
            ORDER BY i.uid
            """
        )
    ).mappings().all()
    return [f"infra|{_canonical_json(dict(row))}" for row in rows]


def _canonical_log_events(connection: Any) -> list[str]:
    rows = connection.execute(
        text(
            """
            SELECT l.uid, s.name AS service, l.ts, l.level, l.status_code, l.trace_id,
                   l.message, l.template_hash, l.raw, l.attrs, l.source_file, l.source_offset
            FROM log_events l JOIN services s ON s.id = l.service_id
            ORDER BY l.uid
            """
        )
    ).mappings().all()
    return [f"log|{_canonical_json(dict(row))}" for row in rows]


def _canonical_error_rollups(connection: Any) -> list[str]:
    rows = connection.execute(
        text(
            """
            SELECT s.name AS service, r.bucket_start, r.status_class, r.level,
                   r.template_hash, r.count, r.first_seen, r.last_seen,
                   e.uid AS exemplar_uid
            FROM error_rollups r
            JOIN services s ON s.id = r.service_id
            JOIN log_events e ON e.id = r.exemplar_log_event_id
            ORDER BY s.name, r.bucket_start, r.status_class, r.level, r.template_hash
            """
        )
    ).mappings().all()
    return [f"rollup|{_canonical_json(dict(row))}" for row in rows]


def _canonical_postmortem_chunks(connection: Any) -> list[str]:
    rows = connection.execute(
        text(
            """
            SELECT p.slug, p.content_sha, c.ordinal, c.kind, c.content
            FROM postmortem_chunks c JOIN postmortems p ON p.id = c.postmortem_id
            ORDER BY p.slug, c.ordinal
            """
        )
    ).mappings().all()
    return [f"postmortem|{_canonical_json(dict(row))}" for row in rows]


def _canonical_json(row: Mapping[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=_json_default)


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return str(value)


def assert_non_zero_source_counts(counts: Mapping[str, int]) -> None:
    zero = [table for table in NON_ZERO_SOURCE_TABLES if counts.get(table, 0) == 0]
    if zero:
        raise DemoError(
            "ingest",
            "expected non-zero rows after ingest for: " + ", ".join(zero),
        )


# --------------------------------------------------------------------------
# Offline demo orchestration (Part 4 §2.4)
# --------------------------------------------------------------------------


def run_offline_demo(options: DemoOptions) -> DemoResult:
    settings = Settings(database_url=options.database_url)
    environment = build_child_environment(options, settings)

    print("[1/11] prerequisites and corpus manifest")
    preflight(options)

    print("[2/11] database provisioning and SQL readiness")
    if options.database_mode == "compose":
        provision_compose_database(options, environment)
    else:
        wait_for_database(options.database_url)
    assert_database_is_empty(options.database_url)
    clean_start = True

    print("[3/11] Alembic migration and pgvector verification")
    _run_stage(
        "migration",
        ["uv", "run", "aegis", "db", "upgrade"],
        root=options.root,
        environment=environment,
    )
    verify_migration_and_pgvector(options.database_url)

    print("[4/11] ruff gate")
    _run_stage(
        "ruff", ["uv", "run", "ruff", "check", "."], root=options.root, environment=environment
    )

    print("[5/11] mypy gate")
    _run_stage(
        "mypy", ["uv", "run", "mypy", "--strict", "src"], root=options.root, environment=environment
    )

    # The destructive suite runs before the corpus exists, not after. Several
    # integration tests truncate evidence, insert fixed service names, and one
    # downgrades the schema to base; run against the just-ingested release
    # database they either collide on a duplicate service or delete the very
    # incidents stage 9 inspects. Nothing here depends on the corpus -- these
    # tests create and destroy their own data -- so ordering is the whole fix.
    print("[6/11] pytest gate, excluding the live evaluations")
    _run_stage(
        "pytest",
        ["uv", "run", "pytest", "-q", "--ignore=tests/eval"],
        root=options.root,
        environment=environment,
    )
    # test_migration.py downgrades to base and back; re-assert the head revision
    # rather than trusting that it restored what it found.
    _run_stage(
        "migrate-restore",
        ["uv", "run", "aegis", "db", "upgrade"],
        root=options.root,
        environment=environment,
    )
    assert_database_is_empty(options.database_url)

    print("[7/11] corpus ingest pass 1")
    _run_stage(
        "ingest-1",
        ["uv", "run", "aegis", "ingest", "all"],
        root=options.root,
        environment=environment,
    )
    engine = _connect_engine(options.database_url)
    try:
        first_digest, first_counts = compute_replay_digest(engine)
    finally:
        engine.dispose()
    assert_non_zero_source_counts(first_counts)

    print("[8/11] corpus ingest pass 2 and replay digest")
    _run_stage(
        "ingest-2",
        ["uv", "run", "aegis", "ingest", "all"],
        root=options.root,
        environment=environment,
    )
    engine = _connect_engine(options.database_url)
    try:
        second_digest, second_counts = compute_replay_digest(engine)
    finally:
        engine.dispose()
    if second_digest != first_digest:
        raise DemoError("ingest", "replay digest changed between ingest pass 1 and pass 2")
    if second_counts != first_counts:
        raise DemoError("ingest", "evidence counts changed between ingest pass 1 and pass 2")

    print("[9/11] five live evaluations against the ingested release database")
    _run_stage(
        "pytest-eval",
        ["uv", "run", "pytest", "-q", "tests/eval"],
        root=options.root,
        environment=environment,
    )

    print("[10/11] persisted trace and evaluation inspection")
    engine = _connect_engine(options.database_url)
    try:
        scenario_results = _read_demo_scenario_results(engine)
    finally:
        engine.dispose()
    if len(scenario_results) != EXPECTED_SCENARIO_COUNT:
        raise DemoError(
            "inspection",
            f"expected {EXPECTED_SCENARIO_COUNT} persisted demo-eval incidents, "
            f"found {len(scenario_results)}",
        )

    return DemoResult(
        corpus_digest=second_digest, scenario_results=scenario_results, clean_start=clean_start
    )


def _read_demo_scenario_results(engine: Engine) -> tuple[ScenarioResult, ...]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT id, dedup_key, status, summary_json
                FROM incidents
                WHERE dedup_key LIKE 'demo-eval:%'
                ORDER BY dedup_key
                """
            )
        ).mappings().all()
    results = []
    for row in rows:
        summary_json = row["summary_json"] or {}
        if row["status"] != "summarized" or not summary_json.get("summary"):
            raise DemoError(
                "inspection",
                f"{row['dedup_key']}: status={row['status']!r} did not persist a valid summary",
            )
        results.append(
            ScenarioResult(
                name=str(row["dedup_key"]).removeprefix("demo-eval:"),
                incident_id=int(row["id"]),
                run_id=str(summary_json["run_id"]),
                confidence=str(summary_json["summary"]["confidence"]),
            )
        )
    return tuple(results)


def print_completion_report(result: DemoResult) -> None:
    print("[11/11] scope and completion report")
    for scenario in result.scenario_results:
        print(
            f"PASS {scenario.name} confidence={scenario.confidence} "
            f"incident_id={scenario.incident_id} run_id={scenario.run_id}"
        )
        print(f"     inspect: uv run aegis trace --run-id {scenario.run_id}")
    print(f"PASS {len(result.scenario_results)}/{EXPECTED_SCENARIO_COUNT} live evaluations")
    print(f"PASS corpus replay digest {result.corpus_digest}")
    print(f"PASS clean_start={'true' if result.clean_start else 'false'}")
    print("SCOPE feasibility, not superiority; provenance, not semantic support")
    print("DEMO COMPLETE")


# --------------------------------------------------------------------------
# Live demo verifier (Part 4 §6)
# --------------------------------------------------------------------------


def run_live_demo(settings: Settings, options: LiveDemoOptions) -> None:
    """Verify a real, manually triggered GitHub push through a tunnel.

    Never pushes on the operator's behalf: this function only verifies the
    surrounding path (secret, registry, both health endpoints) and then polls
    the database for a commit GitHub actually delivered.
    """
    secret = (settings.github_webhook_secret or "").strip()
    if not secret:
        raise DemoError("live-demo", "AEGIS_GITHUB_WEBHOOK_SECRET is unset or blank")

    services = yaml.safe_load((Path("corpus") / "services.yaml").read_text(encoding="utf-8"))
    if not isinstance(services, list):
        raise DemoError("live-demo", "corpus/services.yaml is not a list")
    matches = [s for s in services if isinstance(s, dict) and s.get("repo") == options.github_repo]
    if len(matches) != 1:
        raise DemoError(
            "live-demo",
            f"GITHUB_REPO={options.github_repo!r} must resolve to exactly one entry in "
            f"corpus/services.yaml; found {len(matches)}",
        )
    service_name = str(matches[0]["name"])

    engine = _connect_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            service_id = connection.execute(
                text("SELECT id FROM services WHERE name = :name"), {"name": service_name}
            ).scalar()
        if service_id is None:
            raise DemoError(
                "live-demo",
                f"service {service_name!r} is not loaded; run `uv run aegis ingest services`",
            )

        asyncio.run(_probe_health(options))

        with engine.connect() as connection:
            known_shas = set(
                connection.execute(
                    text("SELECT sha FROM commits WHERE service_id = :id"), {"id": service_id}
                ).scalars()
            )

        print("READY")
        new_sha = _poll_for_new_commit(engine, service_id, known_shas, options.timeout_seconds)
        commit = _read_commit(engine, new_sha)
        _assert_webhook_commit_shape(commit)

        asyncio.run(_verify_via_mcp(settings, service_name, commit))
    finally:
        engine.dispose()

    print("LIVE DEMO PASS")
    print(f"repo={options.github_repo}")
    print(f"commit={new_sha}")
    print(f"citation=commit:{new_sha}")
    print("hunks_omitted=webhook")


async def _probe_health(options: LiveDemoOptions) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        local = await client.get("http://127.0.0.1:8000/healthz")
        if local.status_code != 200:
            raise DemoError("live-demo", f"local /healthz returned {local.status_code}")
        public = await client.get(f"{options.public_base_url.rstrip('/')}/healthz")
        if public.status_code != 200:
            raise DemoError(
                "live-demo",
                f"public /healthz through the tunnel returned {public.status_code}; the "
                "tunnel hostname may resolve without actually routing to this API",
            )


def _poll_for_new_commit(
    engine: Engine, service_id: int, known_shas: set[str], timeout_seconds: int
) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT sha FROM commits WHERE service_id = :id ORDER BY committed_at DESC"
                ),
                {"id": service_id},
            ).scalars().all()
        new_shas = [sha for sha in rows if sha not in known_shas]
        if new_shas:
            return str(new_shas[0])
        time.sleep(2.0)
    raise DemoError(
        "live-demo", f"no new commit for this service within {timeout_seconds}s of READY"
    )


def _read_commit(engine: Engine, sha: str) -> Mapping[str, Any]:
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT sha, committed_at, files_changed FROM commits WHERE sha = :sha"),
            {"sha": sha},
        ).mappings().one()
    return dict(row)


def _assert_webhook_commit_shape(commit: Mapping[str, Any]) -> None:
    files = commit["files_changed"]
    if not isinstance(files, list) or not files:
        raise DemoError("live-demo", "new commit has no changed paths")
    for file in files:
        if not file.get("path"):
            raise DemoError("live-demo", "new commit has an empty changed path")
        if file.get("hunks") is not None:
            raise DemoError("live-demo", "new commit unexpectedly carries hunk content")
        if file.get("hunks_omitted") != "webhook":
            raise DemoError(
                "live-demo",
                "expected hunks_omitted='webhook' for every path, got "
                f"{file.get('hunks_omitted')!r}",
            )


async def _verify_via_mcp(settings: Settings, service_name: str, commit: Mapping[str, Any]) -> None:
    from aegis.agent.transport import mcp_tools  # noqa: PLC0415

    committed_at: datetime = commit["committed_at"]
    window_start = committed_at.isoformat()
    window_end = (committed_at + _one_minute()).isoformat()
    async with mcp_tools(settings) as tools:
        by_name = {getattr(tool, "name", None): tool for tool in tools}
        diff_tool = by_name.get("get_incident_diff")
        if diff_tool is None:
            raise DemoError("live-demo", "get_incident_diff was not exposed by the spawned server")
        result = await diff_tool(
            service=service_name, window_start=window_start, window_end=window_end
        )
    rendered = json.dumps(result, default=str)
    if f"commit:{commit['sha']}" not in rendered:
        raise DemoError(
            "live-demo",
            "get_incident_diff did not return the new commit's citation for its own window",
        )
    for file in commit["files_changed"]:
        if file["path"] not in rendered:
            raise DemoError("live-demo", f"get_incident_diff omitted changed path {file['path']!r}")


def _one_minute() -> Any:
    from datetime import timedelta  # noqa: PLC0415

    return timedelta(minutes=1)


# --------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------


def _demo_options_from_environment(root: Path) -> DemoOptions:
    mode_raw = os.environ.get("AEGIS_DEMO_DB_MODE", "compose").strip() or "compose"
    if mode_raw not in ("compose", "external"):
        raise DemoError(
            "prerequisites",
            f"AEGIS_DEMO_DB_MODE must be compose or external, got {mode_raw!r}",
        )
    mode: DemoDatabaseMode = "compose" if mode_raw == "compose" else "external"
    port = int(os.environ.get("AEGIS_POSTGRES_PORT", "5433"))
    # docker-compose.yml's postgres service always creates a database named
    # "aegis" (POSTGRES_DB); the demo's isolation comes from the compose
    # project name giving it its own container and named volume, not from a
    # differently named database inside that container.
    default_url = f"postgresql+psycopg://aegis:aegis@127.0.0.1:{port}/aegis"
    database_url = os.environ.get("AEGIS_DATABASE_URL", "" if mode == "external" else default_url)
    return DemoOptions(
        root=root, database_mode=mode, database_url=database_url, compose_postgres_port=port
    )


def main(argv: Sequence[str] | None = None) -> int:
    # Line-buffer stdout even when redirected to a file or pipe (`make demo |
    # tee log`), so a stage announcement on stdout and an error on stderr
    # never interleave out of order -- a failure must never appear to precede
    # the stage that produced it.
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in ("offline", "live"):
        print("usage: python -m aegis.release.demo {offline|live}", file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parents[3]
    try:
        if args[0] == "offline":
            options = _demo_options_from_environment(root)
            result = run_offline_demo(options)
            print_completion_report(result)
        else:
            settings = Settings()
            live_options = LiveDemoOptions(
                github_repo=os.environ.get("GITHUB_REPO", "").strip(),
                public_base_url=os.environ.get("PUBLIC_BASE_URL", "").strip(),
                timeout_seconds=int(os.environ.get("AEGIS_LIVE_DEMO_TIMEOUT_SECONDS", "300")),
            )
            if not live_options.github_repo or not live_options.public_base_url:
                raise DemoError("live-demo", "GITHUB_REPO and PUBLIC_BASE_URL are both required")
            run_live_demo(settings, live_options)
    except DemoError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DemoDatabaseMode",
    "DemoError",
    "DemoOptions",
    "DemoResult",
    "LiveDemoOptions",
    "ScenarioResult",
    "build_child_environment",
    "compute_replay_digest",
    "main",
    "preflight",
    "redact_database_url",
    "run_live_demo",
    "run_offline_demo",
]

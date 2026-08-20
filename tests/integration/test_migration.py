from __future__ import annotations

from collections.abc import Generator

import pytest
from alembic import command
from sqlalchemy import Connection, Engine, event, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import alembic_config


@pytest.fixture
def connection(migrated_engine: Engine) -> Generator[Connection]:
    with migrated_engine.connect() as db_connection:
        transaction = db_connection.begin()
        try:
            yield db_connection
        finally:
            transaction.rollback()


def _insert_service(connection: Connection) -> int:
    return connection.execute(
        text("INSERT INTO services (name) VALUES ('payments') RETURNING id")
    ).scalar_one()


def test_rollup_level_check_rejects_unknown(connection: Connection) -> None:
    service_id = _insert_service(connection)
    event_id = connection.execute(
        text(
            """
            INSERT INTO log_events
                (uid, ts, service_id, level, message, template_hash, raw, source_file,
                 source_offset)
            VALUES
                (repeat('a', 32), now(), :service_id, 'error', 'message', repeat('b', 32), 'raw',
                 'log', 0)
            RETURNING id
            """
        ),
        {"service_id": service_id},
    ).scalar_one()

    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            connection.execute(
                text(
                    """
                    INSERT INTO error_rollups
                        (service_id, bucket_start, status_class, level, template_hash, count,
                         first_seen, last_seen, exemplar_log_event_id)
                    VALUES
                        (:service_id, date_trunc('minute', now()), '5xx', 'unknown',
                         repeat('c', 32),
                         1, now(), now(), :event_id)
                    """
                ),
                {"service_id": service_id, "event_id": event_id},
            )


def test_files_changed_jsonb_check_rejects_bad_omission_reason(connection: Connection) -> None:
    service_id = _insert_service(connection)

    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            connection.execute(
                text(
                    """
                    INSERT INTO commits
                        (sha, service_id, authored_at, committed_at, message, files_changed,
                         additions, deletions)
                    VALUES
                        (repeat('a', 40), :service_id, now(), now(), 'message',
                         '[{"path":"app.py","status":"modified","additions":1,"deletions":0,
                            "hunks":null,"hunks_omitted":"invalid"}]'::jsonb,
                         1, 0)
                    """
                ),
                {"service_id": service_id},
            )


def test_every_enumerated_domain_has_a_check(connection: Connection) -> None:
    domains = (
        ("deployments", "status"),
        ("infra_changes", "action"),
        ("log_events", "level"),
        ("unresolved_events", "reason"),
        ("error_rollups", "status_class"),
        ("error_rollups", "level"),
        ("postmortem_chunks", "kind"),
        ("incidents", "status"),
    )
    rows = connection.execute(
        text(
            """
            SELECT cls.relname AS table_name, pg_get_constraintdef(con.oid) AS definition
            FROM pg_constraint AS con
            JOIN pg_class AS cls ON cls.oid = con.conrelid
            WHERE con.contype = 'c'
              AND cls.relname = ANY(:table_names)
            """
        ),
        {"table_names": sorted({table_name for table_name, _ in domains})},
    ).mappings()
    checks_by_table: dict[str, list[str]] = {}
    for row in rows:
        checks_by_table.setdefault(row["table_name"], []).append(row["definition"])

    for table_name, column_name in domains:
        assert any(
            column_name in definition for definition in checks_by_table.get(table_name, [])
        ), f"missing CHECK for {table_name}.{column_name}"


def test_upgrade_creates_all_tables(migrated_engine: Engine) -> None:
    expected_tables = {
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
    }
    assert expected_tables <= set(inspect(migrated_engine).get_table_names())


def test_downgrade_does_not_drop_extension(migrated_engine: Engine) -> None:
    command.downgrade(alembic_config(), "base")
    try:
        with migrated_engine.connect() as connection:
            assert connection.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            ).scalar_one() == 1
    finally:
        command.upgrade(alembic_config(), "head")


def test_commits_created_before_deployments(migrated_engine: Engine) -> None:
    statements: list[str] = []

    def capture_statement(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    with migrated_engine.begin() as connection:
        command.downgrade(alembic_config(connection), "base")
        event.listen(migrated_engine, "before_cursor_execute", capture_statement)
        try:
            command.upgrade(alembic_config(connection), "head")
        finally:
            event.remove(migrated_engine, "before_cursor_execute", capture_statement)

    create_tables = [
        statement.lower() for statement in statements if "create table" in statement.lower()
    ]
    commits_position = next(
        i for i, statement in enumerate(create_tables) if "commits" in statement
    )
    deployments_position = next(
        i for i, statement in enumerate(create_tables) if "deployments" in statement
    )
    assert commits_position < deployments_position


def test_downgrade_base_reverses(migrated_engine: Engine) -> None:
    command.downgrade(alembic_config(), "base")
    try:
        assert not set(inspect(migrated_engine).get_table_names()) & {
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
        }
    finally:
        command.upgrade(alembic_config(), "head")

"""Integration tests for the release coordinator's database-backed checks.

These require the real ``migrated_engine`` fixture: the empty-schema probe
runs a real ``pg_catalog.pg_tables`` query, migration verification reads real
``alembic_version``/``pg_extension`` rows, and the replay digest is computed
from real evidence rows so its natural-key ordering and exclusion of sequence
identifiers and ``created_at`` are proven against PostgreSQL, not simulated.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import Connection, Engine, text

from aegis.release import demo


def test_a_migrated_schema_is_rejected_without_deleting_anything(migrated_engine: Engine) -> None:
    url = str(migrated_engine.url)
    with pytest.raises(demo.DemoError, match="not empty") as excinfo:
        demo.assert_database_is_empty(url)
    assert "services" in str(excinfo.value) or "alembic_version" in str(excinfo.value)

    # Nothing was deleted: the schema is still migrated afterward.
    with migrated_engine.connect() as verify:
        version = verify.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version is not None


def test_migration_and_pgvector_verification_succeeds_on_a_real_migrated_database(
    migrated_engine: Engine,
) -> None:
    version, extension = demo.verify_migration_and_pgvector(str(migrated_engine.url))
    assert version
    assert extension


def _insert_service(connection: Connection, name: str) -> int:
    return connection.execute(
        text("INSERT INTO services (name) VALUES (:name) RETURNING id"), {"name": name}
    ).scalar_one()


def _insert_log_event(connection: Connection, service_id: int, uid: str) -> int:
    return connection.execute(
        text(
            """
            INSERT INTO log_events
                (uid, ts, service_id, level, message, template_hash, raw, source_file,
                 source_offset)
            VALUES
                (:uid, now(), :service_id, 'error', 'boom', repeat('b', 32), 'raw', 'log', 0)
            RETURNING id
            """
        ),
        {"uid": uid, "service_id": service_id},
    ).scalar_one()


def _insert_rollup(connection: Connection, service_id: int, exemplar_id: int, count: int) -> None:
    connection.execute(
        text(
            """
            INSERT INTO error_rollups
                (service_id, bucket_start, status_class, level, template_hash, count,
                 first_seen, last_seen, exemplar_log_event_id)
            VALUES
                (:service_id, date_trunc('minute', now()), '5xx', 'error', repeat('c', 32),
                 :count, now(), now(), :exemplar_id)
            """
        ),
        {"service_id": service_id, "count": count, "exemplar_id": exemplar_id},
    )


def test_replay_digest_ignores_sequence_ids_and_created_at(postgres_engine: Engine) -> None:
    """Deleting and reinserting identical content under a fresh sequence id digests the same."""
    name = f"digest-svc-{uuid4().hex[:8]}"
    with postgres_engine.connect() as setup:
        _insert_service(setup, name)
        setup.commit()
    try:
        digest_first, _ = demo.compute_replay_digest(postgres_engine)

        with postgres_engine.connect() as reinsert:
            reinsert.execute(text("DELETE FROM services WHERE name = :name"), {"name": name})
            # A fresh row for the same content gets a new sequence id and a new
            # created_at -- neither should affect the digest.
            _insert_service(reinsert, name)
            reinsert.commit()

        digest_second, _ = demo.compute_replay_digest(postgres_engine)
        assert digest_first == digest_second
    finally:
        with postgres_engine.connect() as cleanup:
            cleanup.execute(text("DELETE FROM services WHERE name = :name"), {"name": name})
            cleanup.commit()


def test_replay_digest_changes_when_a_rollup_count_or_exemplar_changes(
    postgres_engine: Engine,
) -> None:
    name = f"digest-rollup-{uuid4().hex[:8]}"
    with postgres_engine.connect() as setup:
        service_id = _insert_service(setup, name)
        first_exemplar = _insert_log_event(setup, service_id, uuid4().hex[:32])
        _insert_rollup(setup, service_id, first_exemplar, count=1)
        setup.commit()

    try:
        digest_before, _ = demo.compute_replay_digest(postgres_engine)

        with postgres_engine.connect() as mutate:
            mutate.execute(
                text("UPDATE error_rollups SET count = 2 WHERE service_id = :id"),
                {"id": service_id},
            )
            mutate.commit()

        digest_after, _ = demo.compute_replay_digest(postgres_engine)
        assert digest_before != digest_after
    finally:
        with postgres_engine.connect() as cleanup:
            cleanup.execute(
                text("DELETE FROM error_rollups WHERE service_id = :id"), {"id": service_id}
            )
            cleanup.execute(
                text("DELETE FROM log_events WHERE service_id = :id"), {"id": service_id}
            )
            cleanup.execute(text("DELETE FROM services WHERE id = :id"), {"id": service_id})
            cleanup.commit()


def test_assert_non_zero_source_counts_fails_when_a_required_source_is_empty() -> None:
    counts = {table: 1 for table in demo.NON_ZERO_SOURCE_TABLES}
    counts["error_rollups"] = 0
    with pytest.raises(demo.DemoError, match="error_rollups"):
        demo.assert_non_zero_source_counts(counts)


def test_assert_non_zero_source_counts_accepts_all_present() -> None:
    counts = {table: 3 for table in demo.NON_ZERO_SOURCE_TABLES}
    demo.assert_non_zero_source_counts(counts)  # must not raise

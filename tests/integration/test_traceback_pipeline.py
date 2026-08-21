from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from aegis.db.models import LogEvent, Service
from aegis.ingest.logs import ParseContext, PythonTracebackFormat, iter_drafts
from aegis.ingest.normalize import ServiceRegistry
from aegis.ingest.pipeline import ingest_source


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


def test_multiline_traceback_replay_is_one_stable_event(session: Session, tmp_path: Path) -> None:
    service = Service(name="payments-api")
    session.add(service)
    session.flush()
    path = tmp_path / "trace.log"
    path.write_bytes(
        b"2026-08-19 14:03:22,481 ERROR [payments-api] worker.main: failed\r\n"
        b"Traceback (most recent call last):\r\n"
        b'  File "/srv/app/main.py", line 5, in run\r\n'
        b"RuntimeError: timeout\r\n"
    )
    context = ParseContext(
        registry=ServiceRegistry.load([service]),
        source_file="logs/trace.log",
        default_log_timezone="UTC",
    )
    records = tuple(iter_drafts(path, context, formats=(PythonTracebackFormat(),)))
    cursor = sum(len(record.raw.encode()) for record in records)

    first = ingest_source(session, source="logs/trace.log", records=records, cursor=cursor)
    second = ingest_source(session, source="logs/trace.log", records=records, cursor=cursor)

    assert (first.inserted, second.duplicates) == (1, 1)
    assert len(session.scalars(select(LogEvent)).all()) == 1

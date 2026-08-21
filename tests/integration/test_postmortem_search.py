# ruff: noqa: E501
from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from aegis.db.models import PostmortemChunk, Service
from aegis.embeddings import FixtureEmbeddings
from aegis.ingest.postmortems import ingest_postmortem
from aegis.mcp_server.queries import search_similar_postmortems


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


def _vector(index: int) -> list[float]:
    value = [0.0] * 1024
    value[index] = 1.0
    return value


def _write(path: Path, symptoms: str, resolution: str) -> None:
    path.write_text(
        f"---\ntitle: {path.stem}\noccurred_at: 2026-08-19T14:00:00Z\nservices: [payments-api]\n---\n"
        f"## Symptoms\n{symptoms}\n\n## Resolution\n{resolution}\n",
        encoding="utf-8",
    )


def test_postmortem_edit_deletes_chunks_before_reinsert_and_searches_nearest(
    session: Session, tmp_path: Path
) -> None:
    session.add(Service(name="payments-api"))
    near, far = tmp_path / "near.md", tmp_path / "far.md"
    _write(near, "pool exhausted", "raise pool size")
    _write(far, "cache misses", "warm cache")
    provider = FixtureEmbeddings(
        {
            "## Symptoms\npool exhausted": _vector(0),
            "## Resolution\nraise pool size": _vector(0),
            "## Symptoms\ncache misses": _vector(1),
            "## Resolution\nwarm cache": _vector(1),
            "pool signature": _vector(0),
            "## Symptoms\nchanged pool symptoms": _vector(0),
        }
    )
    ingest_postmortem(session, path=near, provider=provider)
    ingest_postmortem(session, path=far, provider=provider)
    session.flush()

    hits = search_similar_postmortems(
        session, error_signature="pool signature", provider=provider, service="payments-api"
    )
    assert [hit.slug for hit in hits] == ["near"]
    assert hits[0].resolution_cite is not None
    assert hits[0].similarity == 1.0

    _write(near, "changed pool symptoms", "raise pool size")
    ingest_postmortem(session, path=near, provider=provider)
    session.flush()
    ordinals = session.scalars(
        select(PostmortemChunk.ordinal).order_by(PostmortemChunk.ordinal)
    ).all()
    assert ordinals == [0, 0, 1, 1]


def test_search_floor_excludes_orthogonal_fixture_vector(session: Session, tmp_path: Path) -> None:
    session.add(Service(name="payments-api"))
    path = tmp_path / "far.md"
    _write(path, "cache misses", "warm cache")
    provider = FixtureEmbeddings(
        {"## Symptoms\ncache misses": _vector(1), "## Resolution\nwarm cache": _vector(1), "pool": _vector(0)}
    )
    ingest_postmortem(session, path=path, provider=provider)
    session.flush()

    assert search_similar_postmortems(session, error_signature="pool", provider=provider) == []

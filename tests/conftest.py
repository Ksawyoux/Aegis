from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError


def alembic_config(connection: Connection | None = None) -> Config:
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def upgrade_head(engine: Engine) -> None:
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "head")


@pytest.fixture(scope="session")
def postgres_engine() -> Generator[Engine]:
    # DB-backed tests must point at an explicitly opted-in database, never at
    # Settings()' .env fallback: eval and reachability seeding TRUNCATEs every
    # evidence table including services, so a silent fallback would wipe the
    # development database on each full-suite run. Absence skips; strict demo
    # mode (AEGIS_REQUIRE_POSTGRES=1) fails loudly instead.
    url = os.environ.get("AEGIS_DATABASE_URL", "").strip()
    if not url:
        if os.environ.get("AEGIS_REQUIRE_POSTGRES") == "1":
            raise RuntimeError(
                "AEGIS_REQUIRE_POSTGRES=1 requires an explicit AEGIS_DATABASE_URL"
            )
        pytest.skip("AEGIS_DATABASE_URL not set; database-backed tests skipped")
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        engine.dispose()
        if os.environ.get("AEGIS_REQUIRE_POSTGRES") == "1":
            raise
        pytest.skip("postgres unavailable")

    upgrade_head(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def database_url(postgres_engine: Engine) -> str:
    """The URL of the session database, resolved the same way ``postgres_engine`` is."""
    return str(postgres_engine.url)


@pytest.fixture
def migrated_engine(postgres_engine: Engine) -> Generator[Engine]:
    upgrade_head(postgres_engine)
    yield postgres_engine
    upgrade_head(postgres_engine)

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from aegis.config import Settings


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
    engine = create_engine(Settings().database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        engine.dispose()
        # AEGIS_REQUIRE_POSTGRES=1 is `make demo`'s strict mode (Part 4 §3.2):
        # a database outage must fail the release gate loudly, not vanish as a
        # skip that lets `pytest -q` exit zero while covering nothing real.
        if os.environ.get("AEGIS_REQUIRE_POSTGRES") == "1":
            raise
        pytest.skip("postgres unavailable")

    upgrade_head(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def database_url(postgres_engine: Engine) -> str:
    """The URL of the session database, resolved the same way ``postgres_engine`` is.

    Tests that hand a URL to a spawned subprocess or an app factory must not
    read ``os.environ`` directly: that variable is optional while
    ``Settings()`` has .env-backed fallbacks, and a KeyError at setup reads as
    15 opaque errors rather than one clear cause.
    """
    return str(postgres_engine.url)


@pytest.fixture
def migrated_engine(postgres_engine: Engine) -> Generator[Engine]:
    upgrade_head(postgres_engine)
    yield postgres_engine
    upgrade_head(postgres_engine)

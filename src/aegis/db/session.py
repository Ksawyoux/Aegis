from __future__ import annotations

from collections.abc import Generator
from functools import cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from aegis.config import Settings


def create_database_engine(settings: Settings | None = None) -> Engine:
    """Build the synchronous PostgreSQL engine used by ingestion and queries."""

    active_settings = settings or Settings()
    return create_engine(
        active_settings.database_url,
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=20,
    )


@cache
def get_engine(settings: Settings | None = None) -> Engine:
    """Return a lazily-created engine for a caller-selected configuration."""
    return create_database_engine(settings)


def get_session(
    *, settings: Settings | None = None, engine: Engine | None = None
) -> Generator[Session, None, None]:
    """Yield a short-lived session bound to an explicit or lazily resolved engine."""
    active_engine = engine or get_engine(settings)
    session = sessionmaker(bind=active_engine, autoflush=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()

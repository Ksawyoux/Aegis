from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from aegis.config import Settings


def create_database_engine(settings: Settings | None = None) -> Engine:
    """Build the synchronous PostgreSQL engine used by ingestion and queries."""

    active_settings = settings or Settings()
    return create_engine(active_settings.database_url, pool_pre_ping=True)


engine = create_database_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

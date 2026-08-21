from __future__ import annotations

from sqlalchemy import create_engine, text

from aegis.config import Settings
from aegis.db import session as session_module


def test_session_module_does_not_create_an_engine_at_import_time() -> None:
    """Applications can select their database before the first session opens."""
    assert not hasattr(session_module, "engine")
    assert not hasattr(session_module, "SessionLocal")


def test_get_session_uses_the_supplied_engine() -> None:
    engine = create_engine("sqlite://")
    try:
        sessions = session_module.get_session(engine=engine)
        session = next(sessions)
        assert session.bind is engine
        assert session.scalar(text("SELECT 1")) == 1
        sessions.close()
    finally:
        engine.dispose()


def test_get_engine_defers_settings_construction_until_called(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    captured: list[Settings] = []
    sentinel = create_engine("sqlite://")

    def fake_create(settings: Settings | None = None):  # type: ignore[no-untyped-def]
        assert settings is not None
        captured.append(settings)
        return sentinel

    settings = Settings(database_url="postgresql+psycopg://example.invalid/aegis")
    monkeypatch.setattr(session_module, "create_database_engine", fake_create)
    try:
        assert session_module.get_engine(settings) is sentinel
        assert captured == [settings]
    finally:
        sentinel.dispose()

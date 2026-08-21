"""Dependency-aware operational health endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from aegis.config import Settings

router = APIRouter()


@router.get("/healthz")
def healthz(request: Request) -> JSONResponse:
    """Report local database reachability and embedding configuration."""
    settings: Settings = request.app.state.settings
    database_ok, database_detail = _database_check(request.app.state.engine)
    embeddings_ok, embeddings_detail = _embedding_check(settings)
    checks: dict[str, dict[str, Any]] = {
        "database": {"ok": database_ok, "detail": database_detail},
        "embeddings": {"ok": embeddings_ok, "detail": embeddings_detail},
    }
    healthy = all(check["ok"] for check in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "healthy" if healthy else "unhealthy", "checks": checks},
    )


def _database_check(engine: Any) -> tuple[bool, str | None]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, None


def _embedding_check(settings: Settings) -> tuple[bool, str | None]:
    key = settings.openai_api_key
    if key is None or not key.get_secret_value():
        return False, "OPENAI_API_KEY is not configured"
    if not settings.embedding_model.startswith("text-embedding-3-"):
        return False, "embedding_model must use text-embedding-3-*"
    return True, None

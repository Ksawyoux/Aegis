"""Factory for the operational Aegis HTTP service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from aegis.api.github import router as github_router
from aegis.api.health import router as health_router
from aegis.api.webhooks import router as webhook_router
from aegis.config import Settings
from aegis.db.session import create_database_engine


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an application with a lifespan-owned, caller-selected engine."""
    active_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_database_engine(active_settings)
        app.state.settings = active_settings
        app.state.engine = engine
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(webhook_router)
    app.include_router(github_router)
    return app

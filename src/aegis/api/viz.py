"""Read-only visualization endpoints: the dashboard page and its polling feed.

``GET /viz`` serves the generated dashboard; ``GET /viz/live`` returns a
snapshot of recently ingested evidence. Both are read-only SELECTs over the
same engine as every other route, and neither mutates state, so exposing them
next to ``/healthz`` carries no new authority.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from sqlalchemy import Engine, text

router = APIRouter()

_DASHBOARD_PATH = Path(__file__).parents[3] / "visualization" / "index.html"


@router.get("/viz")
async def dashboard() -> FileResponse:
    """Serve the generated single-page dashboard."""
    return FileResponse(_DASHBOARD_PATH)


@router.get("/viz/live")
async def live(request: Request) -> dict[str, object]:
    """Return a snapshot for the dashboard's polling loop."""
    return _snapshot(request.app.state.engine)


def _snapshot(engine: Engine) -> dict[str, object]:
    with engine.connect() as connection:
        counts = {
            table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in (
                "services",
                "commits",
                "deployments",
                "log_events",
                "unresolved_events",
                "error_rollups",
                "infra_changes",
                "postmortems",
                "incidents",
            )
        }
        commits = [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT c.sha, c.message, c.committed_at, s.name AS service,
                           c.files_changed
                    FROM commits c JOIN services s ON s.id = c.service_id
                    ORDER BY c.committed_at DESC, c.sha DESC LIMIT 20
                    """
                )
            ).mappings()
        ]
        deployments = [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT d.environment, d.started_at, d.status, d.commit_sha,
                           s.name AS service
                    FROM deployments d JOIN services s ON s.id = d.service_id
                    ORDER BY d.started_at DESC LIMIT 10
                    """
                )
            ).mappings()
        ]
        infra = [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT i.apply_id, i.applied_at, i.action, i.resource_name,
                           s.name AS service
                    FROM infra_changes i LEFT JOIN services s ON s.id = i.service_id
                    ORDER BY i.applied_at DESC LIMIT 10
                    """
                )
            ).mappings()
        ]
        watermarks = [
            dict(row)
            for row in connection.execute(
                text("SELECT source, last_cursor FROM ingest_watermarks ORDER BY source")
            ).mappings()
        ]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "counts": counts,
        "commits": [_jsonable(row) for row in commits],
        "deployments": [_jsonable(row) for row in deployments],
        "infra_changes": [_jsonable(row) for row in infra],
        "watermarks": watermarks,
    }


def _jsonable(row: dict[str, object]) -> dict[str, object]:
    """Normalize datetimes and JSONB payloads into plain JSON-safe values."""
    result: dict[str, object] = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            result[key] = value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        else:
            result[key] = value
    return result

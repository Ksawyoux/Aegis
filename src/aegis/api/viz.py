"""Read-only visualization endpoints: the dashboard page and its polling feed.

``GET /viz`` serves the generated dashboard; ``GET /viz/live`` returns a
snapshot of recently ingested evidence. Both are read-only SELECTs over the
same engine as every other route, and neither mutates state, so exposing them
next to ``/healthz`` carries no new authority.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from sqlalchemy import Engine, text

from aegis.config import Settings

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


_INCIDENT_QUERY = text(
    """
    SELECT i.id, i.dedup_key, i.status, i.root_cause,
           COALESCE(s.name, '') AS service,
           i.alert_payload ->> 'alert_name' AS alert_name,
           i.summary_json -> 'summary' ->> 'confidence' AS confidence,
           i.window_start, i.window_end, i.opened_at
    FROM incidents i LEFT JOIN services s ON s.id = i.service_id
    ORDER BY i.id DESC LIMIT 50
    """
)

_REVIEW_QUERY = text(
    """
    SELECT r.sha, r.source, r.pr_number, r.verdict, r.files_changed,
           r.additions, r.deletions, r.findings, r.created_at,
           COALESCE(s.name, '') AS service
    FROM code_reviews r LEFT JOIN services s ON s.id = r.service_id
    ORDER BY r.created_at DESC LIMIT 20
    """
)


@router.get("/viz/dashboard")
async def dashboard_snapshot(request: Request) -> dict[str, object]:
    """Return the aggregated snapshot the TypeScript dashboard polls."""
    engine: Engine = request.app.state.engine
    base = _snapshot(engine)
    with engine.connect() as connection:
        incidents = [dict(row) for row in connection.execute(_INCIDENT_QUERY).mappings()]
        reviews = [dict(row) for row in connection.execute(_REVIEW_QUERY).mappings()]
    return {
        **base,
        "incidents": [_jsonable(row) for row in incidents],
        "reviews": [
            {**_jsonable(row), "findings": len(row["findings"] or [])} for row in reviews
        ],
    }


@router.get("/api/reviews/{sha}")
async def review_detail(sha: str, request: Request) -> dict[str, object]:
    """One review's full findings and stored diff for the dashboard detail view."""
    engine: Engine = request.app.state.engine
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT r.*, s.name AS service FROM code_reviews r
                LEFT JOIN services s ON s.id = r.service_id
                WHERE r.sha = :sha
                """
            ),
            {"sha": sha},
        ).mappings().first()
    if row is None:
        return {"detail": None}
    full = _jsonable(dict(row))
    full["findings"] = row["findings"] or []
    return {"detail": full}


@router.get("/api/reviews")
async def recent_reviews(request: Request) -> dict[str, object]:
    """Recent review verdicts with full findings payloads."""
    engine: Engine = request.app.state.engine
    with engine.connect() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT r.*, s.name AS service FROM code_reviews r
                    LEFT JOIN services s ON s.id = r.service_id
                    ORDER BY r.created_at DESC LIMIT 50
                    """
                )
            ).mappings()
        ]
    return {"reviews": [_jsonable(row) for row in rows]}


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


@router.get("/api/incidents")
async def incidents_full(request: Request) -> dict[str, object]:
    """Incident rows with summary-derived fields for the triage views."""
    engine: Engine = request.app.state.engine
    with engine.connect() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT i.id, i.dedup_key, i.status, i.root_cause,
                           i.alert_payload, i.summary_json, i.summary_md,
                           COALESCE(s.name, '') AS service,
                           i.window_start, i.window_end, i.opened_at
                    FROM incidents i LEFT JOIN services s ON s.id = i.service_id
                    ORDER BY i.id DESC LIMIT 50
                    """
                )
            ).mappings()
        ]
    return {"incidents": [_incident_view(_jsonable(row)) for row in rows]}


def _incident_view(row: dict[str, Any]) -> dict[str, object]:
    payload = row.get("alert_payload") or {}
    summary_json = row.get("summary_json") or {}
    summary = summary_json.get("summary") or {}
    trace = summary_json.get("trace") or []
    assert isinstance(payload, dict) and isinstance(summary_json, dict)
    assert isinstance(summary, dict) and isinstance(trace, list)
    severity = "low"
    if isinstance(payload, dict):
        inner = payload.get("payload")
        if isinstance(inner, dict) and isinstance(inner.get("severity"), str):
            severity = inner["severity"].lower()
    if row.get("status") == "failed":
        severity = "high"
    cites = _harvest_trace_citations(trace)
    return {
        "id": f"INC-{1000 + int(row['id'])}",
        "dedup": row.get("dedup_key"),
        "service": row.get("service") or "",
        "alert": payload.get("alert_name") if isinstance(payload, dict) else None,
        "severity": severity,
        "status": row.get("status"),
        "confidence": summary.get("confidence"),
        "opened": (row.get("opened_at") or "")[11:19] + "Z",
        "window": f"{str(row.get('window_start'))[11:16]} → {str(row.get('window_end'))[11:16]}",
        "window_start": row.get("window_start"),
        "window_end": row.get("window_end"),
        "window_full":
        f"{str(row.get('window_start'))[:19]} → {str(row.get('window_end'))[11:19]}Z",
        "cites": len(cites),
        "root_cause": summary.get("root_cause", {}).get("statement") or row.get("root_cause"),
        "root_cites": summary.get("root_cause", {}).get("cites") or [],
        "action": summary.get("recommended_action"),
        "timeline": summary.get("timeline") or [],
        "ruled_out": summary.get("ruled_out") or [],
        "similar": summary.get("similar_incidents") or [],
        "evidence": _evidence_rows(trace),
        "provenance_ok": summary_json.get("run_id") is not None,
        "run_id": summary_json.get("run_id"),
    }


def _harvest_trace_citations(trace: list[object]) -> list[str]:
    found: list[str] = []
    for event in trace:
        if not isinstance(event, dict) or event.get("kind") != "tool_result":
            continue
        _collect_cites(event.get("payload", {}).get("result"), found)
    return sorted(set(found))


def _collect_cites(node: object, out: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("cite", "cites", "source_cites", "baseline_cites", "resolution_cite"):
                values = value if isinstance(value, list) else [value]
                out.extend(v for v in values if isinstance(v, str))
            _collect_cites(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_cites(item, out)


def _evidence_rows(trace: list[object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    prefixes = ("commit", "deploy", "rollup", "log", "infra", "postmortem")
    for event in trace:
        if not isinstance(event, dict) or event.get("kind") != "tool_result":
            continue
        payload = event.get("payload", {})
        cites: list[str] = []
        _collect_cites(payload.get("result"), cites)
        if not cites:
            continue
        for cite in cites:
            if cite in seen:
                continue
            seen.add(cite)
            kind = next((p for p in prefixes if cite.startswith(p + ":")), "log")
            args = payload.get("args", {})
            rows.append(
                {
                    "kind": kind,
                    "uid": cite,
                    "label": f"{payload.get('tool')} · {json.dumps(args)[:90]}",
                    "raw": json.dumps(payload.get("result"), default=str)[:900],
                }
            )
    return rows


@router.get("/api/incidents/{incident_id}")
async def incident_detail(incident_id: int, request: Request) -> dict[str, object]:
    engine: Engine = request.app.state.engine
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT i.id, i.dedup_key, i.status, i.root_cause, i.alert_payload,
                       i.summary_json, COALESCE(s.name, '') AS service,
                       i.window_start, i.window_end, i.opened_at
                FROM incidents i LEFT JOIN services s ON s.id = i.service_id
                WHERE i.id = :id
                """
            ),
            {"id": incident_id},
        ).mappings().first()
    if row is None:
        return {"incident": None}
    return {"incident": _incident_view(_jsonable(dict(row)))}


@router.get("/api/telemetry")
async def telemetry(
    request: Request, service: str, window_start: str | None = None, window_end: str | None = None
) -> dict[str, object]:
    """Real get_error_telemetry output for the telemetry screen."""
    from datetime import datetime

    from aegis.db.session import get_session
    from aegis.mcp_server.queries import get_error_telemetry

    settings = Settings()
    if window_start is None or window_end is None:
        engine: Engine = request.app.state.engine
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT window_start, window_end FROM incidents i
                    JOIN services s ON s.id = i.service_id WHERE s.name = :s
                    ORDER BY (i.status = 'summarized') DESC, i.id DESC LIMIT 1
                    """
                ),
                {"s": service},
            ).first()
        if row is None:
            return {"telemetry": None, "detail": "no incident window known for this service"}
        window_start, window_end = str(row[0]), str(row[1])
    session_gen = get_session(settings=settings)
    session = next(session_gen)
    try:
        telemetry_model = get_error_telemetry(
            session,
            service=service,
            window_start=datetime.fromisoformat(window_start.replace("Z", "+00:00")),
            window_end=datetime.fromisoformat(window_end.replace("Z", "+00:00")),
            baseline_sparse_threshold=settings.baseline_sparse_threshold,
        )
        return {"telemetry": telemetry_model.model_dump(mode="json")}
    finally:
        session_gen.close()


@router.get("/api/diff")
async def incident_diff_api(
    request: Request, service: str, window_start: str | None = None, window_end: str | None = None
) -> dict[str, object]:
    """Real get_incident_diff output for the diff screen."""
    from datetime import datetime

    from aegis.db.session import get_session
    from aegis.mcp_server.queries import get_incident_diff

    settings = Settings()
    if window_start is None or window_end is None:
        engine: Engine = request.app.state.engine
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT window_start, window_end FROM incidents i
                    JOIN services s ON s.id = i.service_id WHERE s.name = :s
                    ORDER BY (i.status = 'summarized') DESC, i.id DESC LIMIT 1
                    """
                ),
                {"s": service},
            ).first()
        if row is None:
            return {"diff": None, "detail": "no incident window known for this service"}
        window_start, window_end = str(row[0]), str(row[1])
    session_gen = get_session(settings=settings)
    session = next(session_gen)
    try:
        diff_model = get_incident_diff(
            session,
            service=service,
            window_start=datetime.fromisoformat(window_start.replace("Z", "+00:00")),
            window_end=datetime.fromisoformat(window_end.replace("Z", "+00:00")),
        )
        return {"diff": diff_model.model_dump(mode="json")}
    finally:
        session_gen.close()


@router.get("/api/ingest-stats")
async def ingest_stats(request: Request) -> dict[str, object]:
    """Ingest health: watermarks, unresolved breakdown, recent unresolved rows."""
    engine: Engine = request.app.state.engine
    with engine.connect() as connection:
        watermarks = [
            dict(row)
            for row in connection.execute(
                text("SELECT source, last_cursor FROM ingest_watermarks ORDER BY source")
            ).mappings()
        ]
        reasons = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT reason, count(*) AS count FROM unresolved_events "
                    "GROUP BY reason ORDER BY count DESC"
                )
            ).mappings()
        ]
        recent = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT reason, source_file, source_offset, raw FROM unresolved_events "
                    "ORDER BY id DESC LIMIT 6"
                )
            ).mappings()
        ]
        total_unresolved = sum(int(r["count"]) for r in reasons)
    return {
        "watermarks": watermarks,
        "reasons": reasons,
        "recent": [_jsonable(r) for r in recent],
        "total_unresolved": total_unresolved,
    }

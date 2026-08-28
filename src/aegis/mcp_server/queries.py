"""Deterministic database queries backing the v0.1 MCP tools.

These functions deliberately keep the database-facing window type separate from
the Pydantic response mirror.  They accept a caller-owned session and neither
commit nor mutate it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any, TypeAlias

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis.db.models import (
    Commit,
    Deployment,
    ErrorRollup,
    InfraChange,
    LogEvent,
    Postmortem,
    PostmortemChunk,
    Service,
)
from aegis.embeddings import EmbeddingProvider
from aegis.ingest.timewindow import ResolvedWindow, baseline_window, resolve_window
from aegis.mcp_server.citations import (
    format_commit,
    format_deploy,
    format_infra,
    format_log,
    format_postmortem,
    format_rollup,
)
from aegis.mcp_server.schemas import (
    CommitRef,
    DeploymentRef,
    DiffCounts,
    ErrorTelemetry,
    Exemplar,
    Frame,
    IncidentDiff,
    PostmortemHit,
    SeriesPoint,
    ServiceChanges,
    StatusBreakdownEntry,
    TemplateAnomaly,
    rank_top_templates,
)
from aegis.mcp_server.schemas import (
    InfraChange as InfraChangeResponse,
)
from aegis.mcp_server.schemas import (
    ResolvedWindow as ResponseWindow,
)

TemplateIdentity: TypeAlias = tuple[str, str, str]
"""The status class, level, and template hash of a rollup identity."""


class QueryError(ValueError):
    """Raised for invalid MCP-query arguments that the server maps to ``ToolError``."""


def search_similar_postmortems(
    session: Session,
    *,
    error_signature: str,
    k: int = 5,
    service: str | None = None,
    provider: EmbeddingProvider,
) -> list[PostmortemHit]:
    """Return nearest distinct postmortems; Event occurrence totals never affect ranking."""
    if isinstance(k, bool) or not isinstance(k, int) or k <= 0:
        raise QueryError("k must be a positive integer")
    if (
        service is not None
        and session.scalar(select(Service.id).where(Service.name == service)) is None
    ):
        raise QueryError("unknown service")
    vector = provider.embed([error_signature])[0]
    distance = PostmortemChunk.embedding.cosine_distance(vector).label("distance")
    statement = select(PostmortemChunk, Postmortem, distance).join(
        Postmortem, PostmortemChunk.postmortem_id == Postmortem.id
    )
    if service is not None:
        statement = statement.where(Postmortem.services.contains([service]))
    rows = session.execute(
        statement.where(distance <= 0.65).order_by(
            distance.asc(), Postmortem.slug.asc(), PostmortemChunk.ordinal.asc()
        )
    ).all()
    hits: list[PostmortemHit] = []
    seen: set[str] = set()
    for chunk, postmortem, value in rows:
        if postmortem.slug in seen:
            continue
        seen.add(postmortem.slug)
        resolution = session.scalar(
            select(PostmortemChunk)
            .where(
                PostmortemChunk.postmortem_id == postmortem.id, PostmortemChunk.kind == "resolution"
            )
            .order_by(PostmortemChunk.ordinal)
        )
        hits.append(
            PostmortemHit(
                cite=format_postmortem(postmortem.slug, postmortem.content_sha, chunk.ordinal),
                resolution_cite=format_postmortem(
                    postmortem.slug, postmortem.content_sha, resolution.ordinal
                )
                if resolution
                else None,
                slug=postmortem.slug,
                title=postmortem.title,
                occurred_at=postmortem.occurred_at,
                snippet=_snippet(chunk.content),
                resolution_md=postmortem.resolution_md,
                similarity=1 - float(value),
            )
        )
        if len(hits) == k:
            break
    return hits


def _snippet(value: str) -> str:
    if len(value) <= 500:
        return value
    return value[:500].rsplit(" ", 1)[0]


def get_incident_diff(
    session: Session,
    *,
    service: str,
    window_start: datetime,
    window_end: datetime,
    lookback_minutes: int = 60,
    include_other_services: bool = True,
) -> IncidentDiff:
    """Return changes for the focus service and active peer services."""
    resolved = resolve_window(window_start, window_end)
    if isinstance(lookback_minutes, bool) or not isinstance(lookback_minutes, int):
        raise QueryError("lookback_minutes must be a non-negative integer")
    if lookback_minutes < 0:
        raise QueryError("lookback_minutes must be a non-negative integer")

    query_window = ResolvedWindow(
        start=resolved.start - timedelta(minutes=lookback_minutes),
        end=resolved.end,
        snapped=resolved.snapped,
    )
    focus_service = _service_by_name(session, service)

    focus = _service_changes(session, focus_service, query_window)
    # A deployment already carries its commit.  The top-level list is therefore
    # for commits that are otherwise standalone in this diff response.

    unattributed = list(
        session.scalars(
            select(InfraChange)
            .where(
                InfraChange.service_id.is_(None),
                InfraChange.applied_at >= query_window.start,
                InfraChange.applied_at < query_window.end,
            )
            .order_by(InfraChange.applied_at.desc(), InfraChange.uid.asc())
        )
    )
    # ``counts`` is focus-only.  Count separately instead of deriving it from
    # ``unattributed``, which is necessarily outside the focus service.
    others = []
    if include_other_services:
        for candidate in session.scalars(
            select(Service).where(Service.id != focus_service.id).order_by(Service.name)
        ):
            changes = _service_changes(session, candidate, query_window)
            if changes.commits or changes.deployments or changes.infra_changes:
                others.append(changes)
    return IncidentDiff(
        window=ResponseWindow.from_ingest(query_window),
        focus=focus,
        other_services=others,
        unattributed=[_infra_change(change) for change in unattributed],
        counts=DiffCounts(
            commits=len(focus.commits),
            deployments=len(focus.deployments),
            infra_changes=len(focus.infra_changes),
        ),
    )


def _service_changes(session: Session, service: Service, window: ResolvedWindow) -> ServiceChanges:
    deployments = list(
        session.execute(
            select(Deployment, Commit)
            .join(Commit, Deployment.commit_sha == Commit.sha)
            .where(
                Deployment.service_id == service.id,
                Deployment.started_at >= window.start,
                Deployment.started_at < window.end,
            )
            .order_by(Deployment.started_at.desc(), Deployment.uid.asc())
        ).tuples()
    )
    deployed_shas = {deployment.commit_sha for deployment, _ in deployments}
    commits = list(
        session.scalars(
            select(Commit)
            .where(
                Commit.service_id == service.id,
                Commit.committed_at >= window.start,
                Commit.committed_at < window.end,
            )
            .order_by(Commit.committed_at.desc(), Commit.sha.asc())
        )
    )
    infra = list(
        session.scalars(
            select(InfraChange)
            .where(
                InfraChange.service_id == service.id,
                InfraChange.applied_at >= window.start,
                InfraChange.applied_at < window.end,
            )
            .order_by(InfraChange.applied_at.desc(), InfraChange.uid.asc())
        )
    )
    return ServiceChanges(
        service=service.name,
        commits=[_commit_ref(row) for row in commits if row.sha not in deployed_shas],
        deployments=[_deployment_ref(deployment, commit) for deployment, commit in deployments],
        infra_changes=[_infra_change(row, service.name) for row in infra],
    )


def get_error_telemetry(
    session: Session,
    *,
    service: str,
    window_start: datetime,
    window_end: datetime,
    top_n: int = 10,
    baseline_sparse_threshold: int,
) -> ErrorTelemetry:
    """Return deterministic rollup telemetry for one service and its baseline."""
    effective = resolve_window(window_start, window_end)
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n <= 0:
        raise QueryError("top_n must be a positive integer")
    focus_service = _service_by_name(session, service)
    baseline = baseline_window(effective)

    current_rows = _window_rollups(session, focus_service.id, effective)
    baseline_rows = _window_rollups(session, focus_service.id, baseline)

    series_by_key: dict[tuple[datetime, str], list[ErrorRollup]] = defaultdict(list)
    for rollup, _ in current_rows:
        series_by_key[(rollup.bucket_start, rollup.status_class)].append(rollup)
    series = [
        SeriesPoint(
            bucket_start=bucket_start,
            status_class=status_class,
            count=sum(row.count for row in rows),
            source_cites=[_rollup_cite(focus_service.name, row) for row in rows],
        )
        for (bucket_start, status_class), rows in sorted(series_by_key.items())
    ]

    current_by_identity = _rollups_by_identity(current_rows)
    baseline_by_identity = _rollups_by_identity(baseline_rows)
    identities = sorted(set(current_by_identity) | set(baseline_by_identity))
    templates = [
        _template_anomaly(
            service_name=focus_service.name,
            identity=identity,
            current=current_by_identity.get(identity, []),
            baseline=baseline_by_identity.get(identity, []),
        )
        for identity in identities
    ]
    for anomaly in templates:
        event_rows = session.scalars(
            select(LogEvent).where(
                LogEvent.service_id == focus_service.id,
                LogEvent.ts >= effective.start,
                LogEvent.ts < effective.end,
                LogEvent.template_hash == anomaly.template_hash,
                LogEvent.level == anomaly.level,
            )
        )
        counts: list[int] = [
            int(row.attrs["occurrence_count"])
            for row in event_rows
            if row.attrs.get("source") == "k8s"
            and isinstance(row.attrs.get("occurrence_count"), int)
        ]
        anomaly.occurrence_count = sum(counts) if counts else None

    breakdown_by_status: dict[str, list[ErrorRollup]] = defaultdict(list)
    for rollup, _ in current_rows:
        breakdown_by_status[rollup.status_class].append(rollup)
    status_breakdown = [
        StatusBreakdownEntry(
            status_class=status_class,
            count=sum(row.count for row in rows),
            source_cites=[_rollup_cite(focus_service.name, row) for row in rows],
        )
        for status_class, rows in sorted(breakdown_by_status.items())
    ]

    trace_candidates = [
        (event.ts, event.uid, event.trace_id)
        for _, event in current_rows
        if event.trace_id is not None and event.trace_id != ""
    ]
    seen_trace_ids: set[str] = set()
    traces: list[str] = []
    for _, _, trace_id in sorted(trace_candidates):
        assert trace_id is not None
        if trace_id not in seen_trace_ids:
            seen_trace_ids.add(trace_id)
            traces.append(trace_id)
            if len(traces) == 5:
                break

    return ErrorTelemetry(
        effective_window=ResponseWindow.from_ingest(effective),
        baseline_window=ResponseWindow.from_ingest(baseline),
        baseline_sparse=sum(row.count for row, _ in baseline_rows) < baseline_sparse_threshold,
        series=series,
        top_templates=rank_top_templates(templates, top_n),
        status_breakdown=status_breakdown,
        sample_trace_ids=traces,
    )


def _service_by_name(session: Session, name: str) -> Service:
    service = session.scalar(select(Service).where(Service.name == name))
    if service is None:
        raise QueryError(f"unknown service: {name}")
    return service


def _commit_ref(commit: Commit) -> CommitRef:
    return CommitRef(
        cite=format_commit(commit.sha),
        sha=commit.sha,
        message=commit.message,
        authored_at=commit.authored_at,
        committed_at=commit.committed_at,
        files_changed=commit.files_changed,
    )


def _deployment_ref(deployment: Deployment, commit: Commit) -> DeploymentRef:
    return DeploymentRef(
        cite=format_deploy(deployment.uid),
        environment=deployment.environment,
        started_at=deployment.started_at,
        status=deployment.status,
        commit=_commit_ref(commit),
    )


def _infra_change(change: InfraChange, service: str | None = None) -> InfraChangeResponse:
    return InfraChangeResponse(
        cite=format_infra(change.uid),
        provider=change.provider,
        resource_type=change.resource_type,
        resource_name=change.resource_name,
        action=change.action,
        attribute_diff=change.attribute_diff,
        applied_at=change.applied_at,
        service=service,
    )


def _window_rollups(
    session: Session, service_id: int, window: ResolvedWindow
) -> list[tuple[ErrorRollup, LogEvent]]:
    """Read a half-open window of rollups with their stored exemplar events.

    Joining ``services`` is deliberate even though this helper does not expose
    the selected name: all rollup reads retain the service-name join required
    for stable rollup citations, and the caller uses that immutable name.
    """
    return list(
        session.execute(
            select(ErrorRollup, LogEvent)
            .join(Service, ErrorRollup.service_id == Service.id)
            .join(LogEvent, ErrorRollup.exemplar_log_event_id == LogEvent.id)
            .where(
                ErrorRollup.service_id == service_id,
                ErrorRollup.bucket_start >= window.start,
                ErrorRollup.bucket_start < window.end,
            )
            .order_by(
                ErrorRollup.bucket_start.asc(),
                ErrorRollup.status_class.asc(),
                ErrorRollup.level.asc(),
                ErrorRollup.template_hash.asc(),
            )
        ).tuples()
    )


def _rollups_by_identity(
    rows: list[tuple[ErrorRollup, LogEvent]],
) -> dict[TemplateIdentity, list[tuple[ErrorRollup, LogEvent]]]:
    grouped: dict[TemplateIdentity, list[tuple[ErrorRollup, LogEvent]]] = defaultdict(list)
    for rollup, event in rows:
        grouped[(rollup.status_class, rollup.level, rollup.template_hash)].append((rollup, event))
    return grouped


def _template_anomaly(
    *,
    service_name: str,
    identity: TemplateIdentity,
    current: list[tuple[ErrorRollup, LogEvent]],
    baseline: list[tuple[ErrorRollup, LogEvent]],
) -> TemplateAnomaly:
    status_class, level, template_hash = identity
    current_count = sum(rollup.count for rollup, _ in current)
    baseline_count = sum(rollup.count for rollup, _ in baseline)
    source_cites = [_rollup_cite(service_name, rollup) for rollup, _ in current]
    baseline_cites = [_rollup_cite(service_name, rollup) for rollup, _ in baseline]
    candidates = current if current_count > 0 else baseline
    exemplar = _exemplar(_richest_exemplar(event for _, event in candidates))
    fields: dict[str, Any] = {
        "template_hash": template_hash,
        "status_class": status_class,
        "level": level,
        "count": current_count,
        "baseline_count": baseline_count,
        "delta": current_count - baseline_count,
        "source_cites": source_cites,
        "baseline_cites": baseline_cites,
        "exemplar": exemplar,
    }
    return TemplateAnomaly(**fields)


def _rollup_cite(service_name: str, rollup: ErrorRollup) -> str:
    return format_rollup(
        service_name,
        rollup.bucket_start,
        rollup.status_class,
        rollup.level,
        rollup.template_hash,
    )


def _richest_exemplar(events: Iterable[LogEvent]) -> LogEvent:
    candidates = list(events)
    if not candidates:
        raise RuntimeError("rollup aggregate unexpectedly has no exemplar")
    return min(candidates, key=lambda event: (-_richness(event), event.uid))


def _richness(event: LogEvent) -> int:
    attrs = event.attrs
    return (
        int(event.trace_id is not None and event.trace_id != "")
        + int(isinstance(attrs.get("stack"), list))
        + int(isinstance(attrs.get("upstream"), str))
        + int(
            isinstance(attrs.get("duration_ms"), int | float)
            and not isinstance(attrs.get("duration_ms"), bool)
        )
        + int(isinstance(attrs.get("exc_type"), str))
    )


def _exemplar(event: LogEvent) -> Exemplar:
    attrs = event.attrs
    return Exemplar(
        cite=format_log(event.uid),
        sample_message=event.message,
        sample_raw=event.raw,
        exc_type=_str_attr(attrs, "exc_type"),
        top_frame=_top_frame(attrs),
        upstream=_str_attr(attrs, "upstream"),
        duration_ms=_number_attr(attrs, "duration_ms"),
        trace_id=event.trace_id,
    )


def _str_attr(attrs: dict[str, Any], key: str) -> str | None:
    value = attrs.get(key)
    return value if isinstance(value, str) else None


def _number_attr(attrs: dict[str, Any], key: str) -> float | None:
    value = attrs.get(key)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _top_frame(attrs: dict[str, Any]) -> Frame | None:
    direct = attrs.get("top_frame")
    if isinstance(direct, dict):
        file, line, func = direct.get("file"), direct.get("line"), direct.get("func")
        if isinstance(file, str) and type(line) is int and isinstance(func, str):
            return Frame(file=file, line=line, func=func)
    stack = attrs.get("stack")
    if not isinstance(stack, list):
        return None
    for candidate in stack:
        if not isinstance(candidate, dict):
            continue
        file = candidate.get("file")
        line = candidate.get("line")
        func = candidate.get("func")
        if isinstance(file, str) and type(line) is int and isinstance(func, str):
            return Frame(file=file, line=line, func=func)
    return None


__all__ = ["QueryError", "get_error_telemetry", "get_incident_diff", "search_similar_postmortems"]

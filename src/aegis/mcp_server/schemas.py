"""Final, deterministic response envelopes for MCP tools.

These models are deliberately a boundary layer: they preserve the values a
tool observed and validate the stable citations that identify that evidence.
They do not turn citations into database pointers; a citation only identifies
the evidence returned for this particular response.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Annotated, Any, Self, TypeAlias

from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator

from aegis.ingest.timewindow import ResolvedWindow as IngestResolvedWindow
from aegis.ingest.timewindow import baseline_window as ingest_baseline_window
from aegis.mcp_server import citations


def _validate_citation(value: str) -> str:
    """Reject values that cannot be used as stable evidence citations."""
    if not citations.is_wellformed(value):
        raise ValueError(f"malformed citation: {value!r}")
    return value


def _as_utc(value: datetime) -> datetime:
    """Keep response timestamps aware and canonically rendered in UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("naive datetimes are not permitted")
    return value.astimezone(UTC)


CitationString: TypeAlias = Annotated[str, AfterValidator(_validate_citation)]
UTCDateTime: TypeAlias = Annotated[datetime, AfterValidator(_as_utc)]
CitationList: TypeAlias = Annotated[list[CitationString], AfterValidator(sorted)]
NonEmptyCitationList: TypeAlias = Annotated[
    list[CitationString], Field(min_length=1), AfterValidator(sorted)
]


def _require_citation_kind(value: str, expected_kind: str) -> str:
    """Check that a syntactically valid citation identifies the expected row type."""
    if citations.parse(value).kind != expected_kind:
        raise ValueError(f"expected a {expected_kind} citation")
    return value


def _require_rollup_citations(values: list[str]) -> list[str]:
    if any(citations.parse(value).kind != "rollup" for value in values):
        raise ValueError("aggregate citations must be rollup citations")
    return values


def _citation_uid(value: str, expected_kind: str) -> str:
    citation = citations.parse(value)
    if citation.kind != expected_kind:
        raise ValueError(f"expected a {expected_kind} citation")
    return citation.uid


def _canonical_json(value: Any) -> Any:
    """Return JSON-safe data with recursively sorted mapping keys.

    JSON columns are otherwise the one place an input mapping's incidental
    insertion order could leak into a response dump.
    """
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        return {key: _canonical_json(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical_json(item) for item in value]
    raise ValueError(f"value is not JSON-safe: {type(value).__name__}")


class Frame(BaseModel):
    file: str
    line: int
    func: str


class Exemplar(BaseModel):
    cite: CitationString
    sample_message: str
    sample_raw: str
    exc_type: str | None
    top_frame: Frame | None
    upstream: str | None
    duration_ms: float | None
    trace_id: str | None

    @field_validator("cite")
    @classmethod
    def _is_log_citation(cls, value: str) -> str:
        return _require_citation_kind(value, "log")


class TemplateAnomaly(BaseModel):
    template_hash: str
    status_class: str
    level: str
    count: int
    baseline_count: int
    delta: int
    source_cites: NonEmptyCitationList
    baseline_cites: CitationList
    occurrence_count: int | None = None
    exemplar: Exemplar

    @field_validator("source_cites", "baseline_cites")
    @classmethod
    def _are_rollup_citations(cls, values: list[str]) -> list[str]:
        return _require_rollup_citations(values)


class SeriesPoint(BaseModel):
    bucket_start: UTCDateTime
    status_class: str
    count: int
    source_cites: NonEmptyCitationList

    @field_validator("source_cites")
    @classmethod
    def _are_rollup_citations(cls, values: list[str]) -> list[str]:
        return _require_rollup_citations(values)


class StatusBreakdownEntry(BaseModel):
    status_class: str
    count: int
    source_cites: NonEmptyCitationList

    @field_validator("source_cites")
    @classmethod
    def _are_rollup_citations(cls, values: list[str]) -> list[str]:
        return _require_rollup_citations(values)


class ResolvedWindow(BaseModel):
    """Pydantic response mirror of ingestion's half-open resolved window."""

    start: UTCDateTime
    end: UTCDateTime
    snapped: bool

    @model_validator(mode="after")
    def _has_positive_length(self) -> Self:
        if self.end <= self.start:
            raise ValueError("window end must be after window start")
        return self

    @classmethod
    def from_ingest(cls, window: IngestResolvedWindow) -> Self:
        """Convert the ingestion dataclass without duplicating its window semantics."""
        return cls(start=window.start, end=window.end, snapped=window.snapped)


class CommitRef(BaseModel):
    cite: CitationString
    sha: str
    message: str
    authored_at: UTCDateTime
    committed_at: UTCDateTime
    files_changed: list[dict[str, Any]]

    @field_validator("cite")
    @classmethod
    def _is_commit_citation(cls, value: str) -> str:
        return _require_citation_kind(value, "commit")

    @field_validator("files_changed")
    @classmethod
    def _canonical_files_changed(cls, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        canonical = [_canonical_json(value) for value in values]
        if any(not isinstance(value.get("path"), str) for value in canonical):
            raise ValueError("each files_changed entry requires a string path")
        return sorted(canonical, key=files_changed_order_key)


class DeploymentRef(BaseModel):
    cite: CitationString
    environment: str
    started_at: UTCDateTime
    status: str
    commit: CommitRef

    @field_validator("cite")
    @classmethod
    def _is_deployment_citation(cls, value: str) -> str:
        return _require_citation_kind(value, "deploy")


class InfraChange(BaseModel):
    cite: CitationString
    provider: str
    resource_type: str
    resource_name: str
    action: str
    attribute_diff: dict[str, Any]
    applied_at: UTCDateTime
    service: str | None

    @field_validator("cite")
    @classmethod
    def _is_infra_citation(cls, value: str) -> str:
        return _require_citation_kind(value, "infra")

    @field_validator("attribute_diff")
    @classmethod
    def _canonical_attribute_diff(cls, value: dict[str, Any]) -> dict[str, Any]:
        canonical = _canonical_json(value)
        assert isinstance(canonical, dict)  # Narrowed by the declared field type.
        return canonical


def top_templates_order_key(value: TemplateAnomaly) -> tuple[int, int, str, str, str]:
    """§10.3 order within each §10.4 template-ranking pool."""
    return (-value.delta, -value.count, value.template_hash, value.status_class, value.level)


def series_order_key(value: SeriesPoint) -> tuple[datetime, str]:
    """§10.3: bucket_start ASC, status_class ASC."""
    return (value.bucket_start, value.status_class)


def status_breakdown_order_key(value: StatusBreakdownEntry) -> str:
    """§10.3: status_class ASC."""
    return value.status_class


def commits_order_key(value: CommitRef) -> tuple[float, str]:
    """§10.3: committed_at DESC, sha ASC."""
    return (-value.committed_at.timestamp(), value.sha)


def deployments_order_key(value: DeploymentRef) -> tuple[float, str]:
    """§10.3: started_at DESC, deployment uid ASC."""
    return (-value.started_at.timestamp(), _citation_uid(value.cite, "deploy"))


def other_services_order_key(value: ServiceChanges) -> str:
    """§10.3: service ASC."""
    return value.service


def unattributed_order_key(value: InfraChange) -> tuple[float, str]:
    """§10.3: applied_at DESC, infra uid ASC."""
    return (-value.applied_at.timestamp(), _citation_uid(value.cite, "infra"))


def files_changed_order_key(value: Mapping[str, Any]) -> str:
    """§10.3: path ASC."""
    path = value.get("path")
    if not isinstance(path, str):
        raise ValueError("each files_changed entry requires a string path")
    return path


# Uppercase aliases make the exported ordering contract easy to discover.
TOP_TEMPLATES_ORDER_KEY = top_templates_order_key
SERIES_ORDER_KEY = series_order_key
STATUS_BREAKDOWN_ORDER_KEY = status_breakdown_order_key
COMMITS_ORDER_KEY = commits_order_key
DEPLOYMENTS_ORDER_KEY = deployments_order_key
OTHER_SERVICES_ORDER_KEY = other_services_order_key
UNATTRIBUTED_ORDER_KEY = unattributed_order_key
FILES_CHANGED_ORDER_KEY = files_changed_order_key


def is_error_template(value: TemplateAnomaly) -> bool:
    """Return whether an anomaly belongs to the §10.4 error pool."""
    return value.status_class == "5xx" or value.level in {"error", "fatal"}


def order_top_templates(values: Iterable[TemplateAnomaly]) -> list[TemplateAnomaly]:
    """Order complete template pools, with errors first and negatives naturally last."""
    error_pool: list[TemplateAnomaly] = []
    other_pool: list[TemplateAnomaly] = []
    for value in values:
        (error_pool if is_error_template(value) else other_pool).append(value)
    return sorted(error_pool, key=top_templates_order_key) + sorted(
        other_pool, key=top_templates_order_key
    )


def rank_top_templates(values: Iterable[TemplateAnomaly], top_n: int) -> list[TemplateAnomaly]:
    """Apply §10.4's independent top-``n`` ranking to the two template pools."""
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 0:
        raise ValueError("top_n must be a non-negative integer")
    ordered = order_top_templates(values)
    error_pool = [value for value in ordered if is_error_template(value)]
    other_pool = [value for value in ordered if not is_error_template(value)]
    return error_pool[:top_n] + other_pool[:top_n]


def sample_trace_ids(values: Iterable[tuple[datetime, str]]) -> list[str]:
    """Return the first five trace ids by the §10.3 ``(ts ASC, uid ASC)`` order."""
    return [trace_id for _, trace_id in sorted(values, key=lambda value: (value[0], value[1]))[:5]]


class ServiceChanges(BaseModel):
    service: str
    commits: list[CommitRef]
    deployments: list[DeploymentRef]

    @model_validator(mode="after")
    def _order_nested_rows(self) -> Self:
        self.commits = sorted(self.commits, key=commits_order_key)
        self.deployments = sorted(self.deployments, key=deployments_order_key)
        return self


class DiffCounts(BaseModel):
    commits: int
    deployments: int
    infra_changes: int


class IncidentDiff(BaseModel):
    window: ResolvedWindow
    focus: ServiceChanges
    other_services: list[ServiceChanges] = Field(default_factory=list)
    unattributed: list[InfraChange] = Field(default_factory=list)
    counts: DiffCounts

    @model_validator(mode="after")
    def _order_response_lists(self) -> Self:
        self.other_services = sorted(self.other_services, key=other_services_order_key)
        self.unattributed = sorted(self.unattributed, key=unattributed_order_key)
        return self


class ErrorTelemetry(BaseModel):
    effective_window: ResolvedWindow
    baseline_window: ResolvedWindow
    baseline_sparse: bool
    series: list[SeriesPoint]
    top_templates: list[TemplateAnomaly]
    status_breakdown: list[StatusBreakdownEntry]
    sample_trace_ids: list[str]

    @model_validator(mode="after")
    def _order_response_lists(self) -> Self:
        self.series = sorted(self.series, key=series_order_key)
        self.top_templates = order_top_templates(self.top_templates)
        self.status_breakdown = sorted(self.status_breakdown, key=status_breakdown_order_key)
        return self


class PostmortemHit(BaseModel):
    cite: CitationString
    resolution_cite: CitationString | None
    slug: str
    title: str
    occurred_at: UTCDateTime | None
    snippet: str
    resolution_md: str | None
    similarity: float

    @field_validator("cite", "resolution_cite")
    @classmethod
    def _is_postmortem_citation(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _require_citation_kind(value, "postmortem")


def baseline_window(window: ResolvedWindow) -> ResolvedWindow:
    """Return the equal-length, disjoint window immediately preceding ``window``.

    The arithmetic is intentionally delegated to ``ingest.timewindow`` so this
    response model remains a mirror of the ingestion contract.
    """
    source = IngestResolvedWindow(start=window.start, end=window.end, snapped=window.snapped)
    return ResolvedWindow.from_ingest(ingest_baseline_window(source))


__all__ = [
    "COMMITS_ORDER_KEY",
    "DEPLOYMENTS_ORDER_KEY",
    "FILES_CHANGED_ORDER_KEY",
    "OTHER_SERVICES_ORDER_KEY",
    "SERIES_ORDER_KEY",
    "STATUS_BREAKDOWN_ORDER_KEY",
    "TOP_TEMPLATES_ORDER_KEY",
    "UNATTRIBUTED_ORDER_KEY",
    "CommitRef",
    "DeploymentRef",
    "DiffCounts",
    "ErrorTelemetry",
    "Exemplar",
    "Frame",
    "IncidentDiff",
    "InfraChange",
    "PostmortemHit",
    "ResolvedWindow",
    "SeriesPoint",
    "ServiceChanges",
    "StatusBreakdownEntry",
    "TemplateAnomaly",
    "baseline_window",
    "commits_order_key",
    "deployments_order_key",
    "files_changed_order_key",
    "is_error_template",
    "order_top_templates",
    "other_services_order_key",
    "rank_top_templates",
    "sample_trace_ids",
    "series_order_key",
    "status_breakdown_order_key",
    "top_templates_order_key",
    "unattributed_order_key",
]

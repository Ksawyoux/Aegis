from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

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
    InfraChange,
    PostmortemHit,
    ResolvedWindow,
    SeriesPoint,
    ServiceChanges,
    StatusBreakdownEntry,
    TemplateAnomaly,
    baseline_window,
    commits_order_key,
    deployments_order_key,
    rank_top_templates,
    series_order_key,
    top_templates_order_key,
    unattributed_order_key,
)

NOW = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
SHA = "a" * 40
LOG_UID = "b" * 32
DEPLOY_UID = "c" * 32
INFRA_UID = "d" * 32
ROLLUP_HASH = "e" * 32


def rollup_cite(*, minute: int = 0, status: str = "5xx", level: str = "error") -> str:
    return format_rollup(
        "api", NOW + timedelta(minutes=minute), status, level, ROLLUP_HASH
    )


def exemplar() -> Exemplar:
    return Exemplar(
        cite=format_log(LOG_UID),
        sample_message="request failed",
        sample_raw="GET /orders 504",
        exc_type="TimeoutError",
        top_frame=Frame(file="orders.py", line=42, func="fetch"),
        upstream="payments",
        duration_ms=120.5,
        trace_id="trace-1",
    )


def anomaly(
    *,
    template_hash: str = ROLLUP_HASH,
    status: str = "5xx",
    level: str = "error",
    count: int = 8,
    baseline_count: int = 1,
) -> TemplateAnomaly:
    return TemplateAnomaly(
        template_hash=template_hash,
        status_class=status,
        level=level,
        count=count,
        baseline_count=baseline_count,
        delta=count - baseline_count,
        source_cites=[rollup_cite(status=status, level=level)],
        baseline_cites=[rollup_cite(minute=-1, status=status, level=level)],
        exemplar=exemplar(),
    )


def commit(*, sha: str = SHA, committed_at: datetime = NOW) -> CommitRef:
    return CommitRef(
        cite=format_commit(sha),
        sha=sha,
        message="Handle payment timeout",
        authored_at=committed_at - timedelta(minutes=1),
        committed_at=committed_at,
        files_changed=[
            {"path": "z.py", "status": "modified", "hunks": None},
            {"path": "a.py", "status": "added", "hunks": "@@"},
        ],
    )


def deployment(*, uid: str = DEPLOY_UID, started_at: datetime = NOW) -> DeploymentRef:
    return DeploymentRef(
        cite=format_deploy(uid),
        environment="production",
        started_at=started_at,
        status="success",
        commit=commit(),
    )


def infra(*, uid: str = INFRA_UID, applied_at: datetime = NOW) -> InfraChange:
    return InfraChange(
        cite=format_infra(uid),
        provider="terraform",
        resource_type="aws_lb_listener",
        resource_name="api",
        action="update",
        attribute_diff={"z": [2, 1], "a": {"y": True, "x": None}},
        applied_at=applied_at,
        service=None,
    )


def window() -> ResolvedWindow:
    return ResolvedWindow(start=NOW, end=NOW + timedelta(minutes=5), snapped=False)


@pytest.mark.parametrize(
    ("factory", "field"),
    [
        (lambda: anomaly(), "source_cites"),
        (
            lambda: SeriesPoint(
                bucket_start=NOW,
                status_class="5xx",
                count=1,
                source_cites=[rollup_cite()],
            ),
            "source_cites",
        ),
        (
            lambda: StatusBreakdownEntry(status_class="5xx", count=1, source_cites=[rollup_cite()]),
            "source_cites",
        ),
    ],
)
def test_aggregate_models_reject_empty_source_cites(factory: object, field: str) -> None:
    model = factory()  # type: ignore[operator]
    with pytest.raises(ValidationError):
        type(model)(**{**model.model_dump(), field: []})


@pytest.mark.parametrize(
    "factory",
    [
        lambda: exemplar().model_copy(update={"cite": "bad"}),
        lambda: commit().model_copy(update={"cite": "bad"}),
        lambda: deployment().model_copy(update={"cite": "bad"}),
        lambda: infra().model_copy(update={"cite": "bad"}),
        lambda: PostmortemHit(
            cite="bad",
            resolution_cite=None,
            slug="outage",
            title="Outage",
            occurred_at=NOW,
            snippet="snippet",
            resolution_md=None,
            similarity=0.8,
        ),
    ],
)
def test_every_cite_field_rejects_malformed_citations(factory: object) -> None:
    with pytest.raises(ValidationError):
        value = factory()  # type: ignore[operator]
        if isinstance(value, BaseException):
            raise value
        type(value).model_validate(value.model_dump())


def test_all_citation_list_fields_reject_malformed_citations() -> None:
    with pytest.raises(ValidationError):
        anomaly(source_cites=["bad"])  # type: ignore[call-arg]


def test_ordering_keys_sort_the_documented_orders() -> None:
    templates = [
        anomaly(template_hash="c" * 32, count=5, baseline_count=2),
        anomaly(template_hash="a" * 32, count=5, baseline_count=2),
        anomaly(template_hash="b" * 32, count=6, baseline_count=1),
    ]
    assert [item.template_hash for item in sorted(templates, key=top_templates_order_key)] == [
        "b" * 32,
        "a" * 32,
        "c" * 32,
    ]

    series = [
        SeriesPoint(bucket_start=NOW + timedelta(minutes=1), status_class="4xx", count=1, source_cites=[rollup_cite(minute=1, status="4xx")]),
        SeriesPoint(bucket_start=NOW, status_class="5xx", count=1, source_cites=[rollup_cite()]),
        SeriesPoint(bucket_start=NOW, status_class="4xx", count=1, source_cites=[rollup_cite(status="4xx")]),
    ]
    assert [(item.bucket_start, item.status_class) for item in sorted(series, key=series_order_key)] == [
        (NOW, "4xx"),
        (NOW, "5xx"),
        (NOW + timedelta(minutes=1), "4xx"),
    ]

    commits = [
        commit(sha="c" * 40, committed_at=NOW),
        commit(sha="a" * 40, committed_at=NOW),
        commit(sha="b" * 40, committed_at=NOW + timedelta(minutes=1)),
    ]
    assert [item.sha for item in sorted(commits, key=commits_order_key)] == [
        "b" * 40,
        "a" * 40,
        "c" * 40,
    ]

    deployments = [
        deployment(uid="c" * 32, started_at=NOW),
        deployment(uid="a" * 32, started_at=NOW),
        deployment(uid="b" * 32, started_at=NOW + timedelta(minutes=1)),
    ]
    assert [item.cite for item in sorted(deployments, key=deployments_order_key)] == [
        format_deploy("b" * 32),
        format_deploy("a" * 32),
        format_deploy("c" * 32),
    ]

    changes = [
        infra(uid="c" * 32, applied_at=NOW),
        infra(uid="a" * 32, applied_at=NOW),
        infra(uid="b" * 32, applied_at=NOW + timedelta(minutes=1)),
    ]
    assert [item.cite for item in sorted(changes, key=unattributed_order_key)] == [
        format_infra("b" * 32),
        format_infra("a" * 32),
        format_infra("c" * 32),
    ]


def test_two_pool_ranking_keeps_new_5xx_and_negative_delta_last() -> None:
    new_5xx = anomaly(template_hash="1" * 32, status="5xx", count=2, baseline_count=0)
    noisy_4xx = anomaly(template_hash="2" * 32, status="4xx", level="warning", count=100, baseline_count=1)
    declining_5xx = anomaly(template_hash="3" * 32, status="5xx", count=1, baseline_count=8)

    ranked = rank_top_templates([declining_5xx, noisy_4xx, new_5xx], top_n=2)

    assert ranked == [new_5xx, declining_5xx, noisy_4xx]


def test_baseline_window_is_equal_length_disjoint_and_immediately_preceding() -> None:
    current = window()
    baseline = baseline_window(current)

    assert baseline.end == current.start
    assert baseline.end - baseline.start == current.end - current.start
    assert baseline.end <= current.start


def test_fully_populated_error_telemetry_dump_is_byte_identical() -> None:
    data = {
        "effective_window": window(),
        "baseline_window": baseline_window(window()),
        "baseline_sparse": False,
        "series": [
            SeriesPoint(bucket_start=NOW, status_class="5xx", count=2, source_cites=[rollup_cite()])
        ],
        "top_templates": [anomaly()],
        "status_breakdown": [
            StatusBreakdownEntry(status_class="5xx", count=2, source_cites=[rollup_cite()])
        ],
        "sample_trace_ids": ["trace-1"],
    }
    first = ErrorTelemetry(**data)
    second = ErrorTelemetry(**data)

    assert json.dumps(first.model_dump(mode="json"), separators=(",", ":")) == json.dumps(
        second.model_dump(mode="json"), separators=(",", ":")
    )


def test_incident_diff_orders_nested_response_lists() -> None:
    response = IncidentDiff(
        window=window(),
        focus=ServiceChanges(service="api", commits=[commit()], deployments=[deployment()]),
        other_services=[
            ServiceChanges(service="worker", commits=[], deployments=[]),
            ServiceChanges(service="api", commits=[], deployments=[]),
        ],
        unattributed=[infra(uid="c" * 32), infra(uid="a" * 32)],
        counts=DiffCounts(commits=1, deployments=1, infra_changes=2),
    )

    assert [entry.service for entry in response.other_services] == ["api", "worker"]
    assert [entry.cite for entry in response.unattributed] == [
        format_infra("a" * 32),
        format_infra("c" * 32),
    ]


def test_postmortem_resolution_citation_is_validated() -> None:
    with pytest.raises(ValidationError):
        PostmortemHit(
            cite=format_postmortem("outage", "f" * 64, 0),
            resolution_cite="bad",
            slug="outage",
            title="Outage",
            occurred_at=NOW,
            snippet="snippet",
            resolution_md="resolution",
            similarity=0.8,
        )

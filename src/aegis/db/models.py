from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Service(CreatedAtMixin, Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    repo: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_keys: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    k8s_names: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    infra_tags: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    log_timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'UTC'"))


class Commit(CreatedAtMixin, Base):
    __tablename__ = "commits"
    __table_args__ = (
        CheckConstraint("sha ~ '^[0-9a-f]{40}$'", name="ck_commits_sha_format"),
        CheckConstraint(
            """
            jsonb_typeof(files_changed) = 'array'
            AND NOT jsonb_path_exists(files_changed,
              '$[*] ? (!(@.status == "added" || @.status == "modified"
                     || @.status == "removed" || @.status == "renamed"))')
            AND NOT jsonb_path_exists(files_changed,
              '$[*].hunks_omitted ? (@ != null && @ != "budget" && @ != "webhook")')
            """,
            name="ck_files_changed_shape",
        ),
        Index("ix_commits_service_committed_at", "service_id", desc("committed_at")),
    )

    sha: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=False
    )
    authored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(Text, nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    files_changed: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    additions: Mapped[int] = mapped_column(Integer, nullable=False)
    deletions: Mapped[int] = mapped_column(Integer, nullable=False)


class Deployment(CreatedAtMixin, Base):
    __tablename__ = "deployments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('in_progress', 'success', 'failed', 'rolled_back')",
            name="ck_deployments_status",
        ),
        Index("ix_deployments_service_started_at", "service_id", desc("started_at")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    uid: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=False
    )
    commit_sha: Mapped[str] = mapped_column(
        ForeignKey("commits.sha", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=False
    )
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)


class InfraChange(CreatedAtMixin, Base):
    __tablename__ = "infra_changes"
    __table_args__ = (
        CheckConstraint(
            "action IN ('create', 'update', 'delete', 'replace')", name="ck_infra_changes_action"
        ),
        Index("ix_infra_changes_service_applied_at", "service_id", desc("applied_at")),
        # Unattributed changes (service_id IS NULL) are queried by time alone.
        Index("ix_infra_changes_applied_at", desc("applied_at")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    uid: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_name: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    attribute_diff: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    apply_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_id: Mapped[int | None] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=True
    )


class LogEvent(CreatedAtMixin, Base):
    __tablename__ = "log_events"
    __table_args__ = (
        CheckConstraint(
            "level IN ('debug', 'info', 'warning', 'error', 'fatal')", name="ck_log_events_level"
        ),
        Index("ix_log_events_service_ts", "service_id", desc("ts")),
        Index("ix_log_events_template_hash", "template_hash"),
        Index(
            "ix_log_events_trace_id",
            "trace_id",
            postgresql_where=text("trace_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    uid: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=False
    )
    level: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    template_hash: Mapped[str] = mapped_column(CHAR(32), nullable=False)
    raw: Mapped[str] = mapped_column(Text, nullable=False)
    attrs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    source_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)


class UnresolvedEvent(CreatedAtMixin, Base):
    __tablename__ = "unresolved_events"
    __table_args__ = (
        CheckConstraint(
            "reason IN ('no_service_match', 'ambiguous_service', 'no_timestamp', 'unparseable')",
            name="ck_unresolved_events_reason",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    uid: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    source_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ErrorRollup(CreatedAtMixin, Base):
    __tablename__ = "error_rollups"
    __table_args__ = (
        PrimaryKeyConstraint(
            "service_id", "bucket_start", "status_class", "level", "template_hash"
        ),
        CheckConstraint(
            "status_class IN ('2xx', '3xx', '4xx', '5xx', 'none')",
            name="ck_error_rollups_status_class",
        ),
        CheckConstraint(
            "level IN ('debug', 'info', 'warning', 'error', 'fatal')",
            name="ck_error_rollups_level",
        ),
        CheckConstraint("count > 0", name="ck_error_rollups_count_positive"),
        Index("ix_error_rollups_service_bucket_start", "service_id", desc("bucket_start")),
    )

    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=False
    )
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status_class: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(Text, nullable=False)
    template_hash: Mapped[str] = mapped_column(CHAR(32), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exemplar_log_event_id: Mapped[int] = mapped_column(
        ForeignKey("log_events.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=False
    )


class Postmortem(CreatedAtMixin, Base):
    __tablename__ = "postmortems"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    services: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_sha: Mapped[str] = mapped_column(Text, nullable=False)
    model_fingerprint: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))


class PostmortemChunk(CreatedAtMixin, Base):
    __tablename__ = "postmortem_chunks"
    __table_args__ = (
        UniqueConstraint(
            "postmortem_id", "ordinal", name="uq_postmortem_chunks_postmortem_ordinal"
        ),
        CheckConstraint("kind IN ('section', 'resolution')", name="ck_postmortem_chunks_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    postmortem_id: Mapped[int] = mapped_column(
        ForeignKey("postmortems.id", ondelete="CASCADE", onupdate="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)


class Incident(CreatedAtMixin, Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'investigating', 'summarized', 'failed')",
            name="ck_incidents_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    dedup_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    service_id: Mapped[int | None] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=True
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    alert_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    summary_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)


class IngestWatermark(CreatedAtMixin, Base):
    __tablename__ = "ingest_watermarks"

    source: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    last_cursor: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CodeReview(CreatedAtMixin, Base):
    __tablename__ = "code_reviews"
    __table_args__ = (
        CheckConstraint(
            "verdict IN ('clean', 'warn', 'fail')", name="ck_code_reviews_verdict"
        ),
        CheckConstraint(
            "source IN ('push', 'pull_request')", name="ck_code_reviews_source"
        ),
        Index("ix_code_reviews_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    sha: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    service_id: Mapped[int | None] = mapped_column(
        ForeignKey("services.id", ondelete="SET NULL", onupdate="RESTRICT"), nullable=True
    )
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    files_changed: Mapped[int] = mapped_column(Integer, nullable=False)
    additions: Mapped[int] = mapped_column(Integer, nullable=False)
    deletions: Mapped[int] = mapped_column(Integer, nullable=False)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    patch: Mapped[str | None] = mapped_column(Text, nullable=True)
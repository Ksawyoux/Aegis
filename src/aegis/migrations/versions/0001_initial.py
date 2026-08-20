"""Create the Aegis Context foundation schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _created_at() -> sa.Column[object]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "services",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("repo", sa.Text(), nullable=True),
        sa.Column(
            "log_keys",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "k8s_names",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "infra_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("log_timezone", sa.Text(), nullable=False, server_default=sa.text("'UTC'")),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_services"),
        sa.UniqueConstraint("name", name="uq_services_name"),
    )
    op.create_table(
        "commits",
        sa.Column("sha", sa.Text(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("authored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("files_changed", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("additions", sa.Integer(), nullable=False),
        sa.Column("deletions", sa.Integer(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name="fk_commits_service_id_services",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("sha", name="pk_commits"),
    )
    op.create_table(
        "deployments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uid", sa.Text(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("commit_sha", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name="fk_deployments_service_id_services",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["commit_sha"],
            ["commits.sha"],
            name="fk_deployments_commit_sha_commits",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_deployments"),
        sa.UniqueConstraint("uid", name="uq_deployments_uid"),
    )
    op.create_table(
        "infra_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uid", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_name", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("attribute_diff", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("apply_id", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("service_id", sa.Integer(), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name="fk_infra_changes_service_id_services",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_infra_changes"),
        sa.UniqueConstraint("uid", name="uq_infra_changes_uid"),
    )
    op.create_table(
        "log_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("uid", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("template_hash", sa.CHAR(length=32), nullable=False),
        sa.Column("raw", sa.Text(), nullable=False),
        sa.Column(
            "attrs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("source_file", sa.Text(), nullable=False),
        sa.Column("source_offset", sa.BigInteger(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name="fk_log_events_service_id_services",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_log_events"),
        sa.UniqueConstraint("uid", name="uq_log_events_uid"),
    )
    op.create_table(
        "unresolved_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("uid", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=False),
        sa.Column("source_offset", sa.BigInteger(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_unresolved_events"),
        sa.UniqueConstraint("uid", name="uq_unresolved_events_uid"),
    )
    op.create_table(
        "error_rollups",
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_class", sa.Text(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("template_hash", sa.CHAR(length=32), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exemplar_log_event_id", sa.BigInteger(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name="fk_error_rollups_service_id_services",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["exemplar_log_event_id"],
            ["log_events.id"],
            name="fk_error_rollups_exemplar_log_event_id_log_events",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "service_id",
            "bucket_start",
            "status_class",
            "level",
            "template_hash",
            name="pk_error_rollups",
        ),
    )
    op.create_table(
        "postmortems",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("services", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("resolution_md", sa.Text(), nullable=True),
        sa.Column("content_sha", sa.Text(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_postmortems"),
        sa.UniqueConstraint("slug", name="uq_postmortems_slug"),
    )
    op.create_table(
        "postmortem_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("postmortem_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["postmortem_id"],
            ["postmortems.id"],
            name="fk_postmortem_chunks_postmortem_id_postmortems",
            ondelete="CASCADE",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_postmortem_chunks"),
        sa.UniqueConstraint(
            "postmortem_id", "ordinal", name="uq_postmortem_chunks_postmortem_ordinal"
        ),
    )
    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("alert_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary_md", sa.Text(), nullable=True),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name="fk_incidents_service_id_services",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_incidents"),
        sa.UniqueConstraint("dedup_key", name="uq_incidents_dedup_key"),
    )
    op.create_table(
        "ingest_watermarks",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("last_cursor", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("source", name="pk_ingest_watermarks"),
    )

    op.create_index(
        "ix_commits_service_committed_at",
        "commits",
        ["service_id", sa.text("committed_at DESC")],
    )
    op.create_index(
        "ix_deployments_service_started_at",
        "deployments",
        ["service_id", sa.text("started_at DESC")],
    )
    op.create_index(
        "ix_infra_changes_service_applied_at",
        "infra_changes",
        ["service_id", sa.text("applied_at DESC")],
    )
    op.create_index(
        "ix_infra_changes_applied_at", "infra_changes", [sa.text("applied_at DESC")]
    )
    op.create_index(
        "ix_log_events_service_ts", "log_events", ["service_id", sa.text("ts DESC")]
    )
    op.create_index("ix_log_events_template_hash", "log_events", ["template_hash"])
    op.create_index(
        "ix_log_events_trace_id",
        "log_events",
        ["trace_id"],
        postgresql_where=sa.text("trace_id IS NOT NULL"),
    )
    op.create_index(
        "ix_error_rollups_service_bucket_start",
        "error_rollups",
        ["service_id", sa.text("bucket_start DESC")],
    )

    op.create_check_constraint("ck_commits_sha_format", "commits", "sha ~ '^[0-9a-f]{40}$'")
    op.create_check_constraint(
        "ck_files_changed_shape",
        "commits",
        """
        jsonb_typeof(files_changed) = 'array'
        AND NOT jsonb_path_exists(files_changed,
          '$[*] ? (!(@.status == "added" || @.status == "modified"
                 || @.status == "removed" || @.status == "renamed"))')
        AND NOT jsonb_path_exists(files_changed,
          '$[*].hunks_omitted ? (@ != null && @ != "budget" && @ != "webhook")')
        """,
    )
    op.create_check_constraint(
        "ck_deployments_status",
        "deployments",
        "status IN ('in_progress', 'success', 'failed', 'rolled_back')",
    )
    op.create_check_constraint(
        "ck_infra_changes_action",
        "infra_changes",
        "action IN ('create', 'update', 'delete', 'replace')",
    )
    op.create_check_constraint(
        "ck_log_events_level",
        "log_events",
        "level IN ('debug', 'info', 'warning', 'error', 'fatal')",
    )
    op.create_check_constraint(
        "ck_unresolved_events_reason",
        "unresolved_events",
        "reason IN ('no_service_match', 'ambiguous_service', 'no_timestamp', 'unparseable')",
    )
    op.create_check_constraint(
        "ck_error_rollups_status_class",
        "error_rollups",
        "status_class IN ('2xx', '3xx', '4xx', '5xx', 'none')",
    )
    op.create_check_constraint(
        "ck_error_rollups_level",
        "error_rollups",
        "level IN ('debug', 'info', 'warning', 'error', 'fatal')",
    )
    op.create_check_constraint("ck_error_rollups_count_positive", "error_rollups", "count > 0")
    op.create_check_constraint(
        "ck_postmortem_chunks_kind",
        "postmortem_chunks",
        "kind IN ('section', 'resolution')",
    )
    op.create_check_constraint(
        "ck_incidents_status",
        "incidents",
        "status IN ('open', 'investigating', 'summarized', 'failed')",
    )


def downgrade() -> None:
    op.drop_table("ingest_watermarks")
    op.drop_table("incidents")
    op.drop_table("postmortem_chunks")
    op.drop_table("postmortems")
    op.drop_table("error_rollups")
    op.drop_table("unresolved_events")
    op.drop_table("log_events")
    op.drop_table("infra_changes")
    op.drop_table("deployments")
    op.drop_table("commits")
    op.drop_table("services")

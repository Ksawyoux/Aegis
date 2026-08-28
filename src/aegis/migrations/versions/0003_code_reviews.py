"""Add code_reviews for automated PR/push review results.

Revision ID: 0003_code_reviews
Revises: 0002_postmortem_fingerprint
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_code_reviews"
down_revision = "0002_postmortem_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "code_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("sha", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column(
            "service_id",
            sa.Integer(),
            sa.ForeignKey("services.id", ondelete="SET NULL", onupdate="RESTRICT"),
            nullable=True,
        ),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("files_changed", sa.Integer(), nullable=False),
        sa.Column("additions", sa.Integer(), nullable=False),
        sa.Column("deletions", sa.Integer(), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False, server_default="[]"),
        sa.CheckConstraint("verdict IN ('clean', 'warn', 'fail')", name="ck_code_reviews_verdict"),
        sa.CheckConstraint("source IN ('push', 'pull_request')", name="ck_code_reviews_source"),
        sa.UniqueConstraint("sha", name="uq_code_reviews_sha"),
    )
    op.create_index("ix_code_reviews_created_at", "code_reviews", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_code_reviews_created_at", table_name="code_reviews")
    op.drop_table("code_reviews")

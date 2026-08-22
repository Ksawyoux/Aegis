"""Store the analyzed diff so a review is auditable without re-fetching GitHub.

Revision ID: 0004_review_patch
Revises: 0003_code_reviews
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_review_patch"
down_revision = "0003_code_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("code_reviews", sa.Column("patch", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("code_reviews", "patch")

"""Store the embedding space used for each postmortem.

Revision ID: 0002_postmortem_fingerprint
Revises: 0001_initial
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_postmortem_fingerprint"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "postmortems",
        sa.Column("model_fingerprint", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("postmortems", "model_fingerprint")

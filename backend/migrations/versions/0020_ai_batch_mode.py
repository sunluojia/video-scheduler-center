"""Store the AI Batch inference setting for each batch.

Revision ID: 0020
Revises: 0019
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "batches", sa.Column("ai_batch_mode", sa.Boolean, nullable=False, server_default=sa.false())
    )


def downgrade() -> None:
    op.drop_column("batches", "ai_batch_mode")

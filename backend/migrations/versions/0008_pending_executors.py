"""执行程序可在第一次心跳前登记。

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "executors", "last_seen_at", existing_type=sa.DateTime(timezone=True), nullable=True
    )


def downgrade() -> None:
    op.execute(
        "UPDATE executors SET last_seen_at = registered_at, "
        "withdrawn_at = COALESCE(withdrawn_at, registered_at) WHERE last_seen_at IS NULL"
    )
    op.alter_column(
        "executors", "last_seen_at", existing_type=sa.DateTime(timezone=True), nullable=False
    )

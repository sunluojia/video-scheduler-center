"""代理测速：单路和几路同时下载的速度。

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

COLUMNS = ("speed_at", "speed_mbps", "parallel_mbps", "speed_tested_at", "speed_error")


def upgrade() -> None:
    op.add_column("proxies", sa.Column("speed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("proxies", sa.Column("speed_mbps", sa.Float(), nullable=True))
    op.add_column("proxies", sa.Column("parallel_mbps", sa.Float(), nullable=True))
    op.add_column(
        "proxies", sa.Column("speed_tested_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("proxies", sa.Column("speed_error", sa.Text(), nullable=True))


def downgrade() -> None:
    for column in reversed(COLUMNS):
        op.drop_column("proxies", column)

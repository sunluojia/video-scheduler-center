"""按爬取设备设置身份预热并发。

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "executors", sa.Column("warm_slots", sa.SmallInteger, nullable=False, server_default="1")
    )


def downgrade() -> None:
    op.drop_column("executors", "warm_slots")

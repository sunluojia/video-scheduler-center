"""执行程序：爬取程序的并发（同时获取几个达人、同时下载几条），执行程序页设置，随心跳下发。

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "executors", sa.Column("crawl_slots", sa.SmallInteger, nullable=False, server_default="2")
    )
    op.add_column(
        "executors",
        sa.Column("download_slots", sa.SmallInteger, nullable=False, server_default="4"),
    )


def downgrade() -> None:
    op.drop_column("executors", "download_slots")
    op.drop_column("executors", "crawl_slots")

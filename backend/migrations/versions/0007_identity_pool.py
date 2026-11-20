"""账号池：要保持的可用数量，由页面设置，爬取程序按心跳分到的数量自动补充。

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_pool",
        sa.Column("id", sa.SmallInteger, primary_key=True),
        sa.Column("target", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="identity_pool_single_row"),
    )


def downgrade() -> None:
    op.drop_table("identity_pool")

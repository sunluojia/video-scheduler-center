"""执行程序：保存爬取程序心跳带来的身份池快照，供账号池页面显示。

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-19
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("executors", sa.Column("identities", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("executors", "identities")

"""达人：保存爬取程序回传的平台资料。

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-19
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("creators", sa.Column("profile", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("creators", "profile")

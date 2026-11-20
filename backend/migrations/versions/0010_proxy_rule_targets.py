"""代理用途规则支持多个分组和单个代理。

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("proxy_rules", sa.Column("targets", postgresql.JSONB(none_as_null=True)))


def downgrade() -> None:
    op.execute("UPDATE proxy_rules SET mode = 'priority' WHERE mode = 'weighted'")
    op.drop_column("proxy_rules", "targets")

"""记录代理配置版本及租约领用时的版本。

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("proxies", sa.Column("config_revision", sa.Uuid()))
    op.add_column("proxy_leases", sa.Column("proxy_revision", sa.Uuid()))


def downgrade() -> None:
    op.drop_column("proxy_leases", "proxy_revision")
    op.drop_column("proxies", "config_revision")

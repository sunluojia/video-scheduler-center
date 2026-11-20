"""代理：平台限流单独记，测试领取可到期。

以前限流也写在 cooldown_until 上、backoff 为 0；现在挪到 throttled_until，与失败冷却分开。

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "proxy_lines", sa.Column("throttled_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        "UPDATE proxy_lines SET throttled_until = cooldown_until, cooldown_until = NULL "
        "WHERE backoff = 0 AND cooldown_until IS NOT NULL"
    )
    op.add_column("proxies", sa.Column("testing_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("proxies", "testing_until")
    op.execute(
        "UPDATE proxy_lines SET cooldown_until = throttled_until "
        "WHERE throttled_until IS NOT NULL AND backoff = 0"
    )
    op.drop_column("proxy_lines", "throttled_until")

"""在代理租约上记录作品列表读取结果。

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("proxy_leases", sa.Column("listing_result", sa.String(32)))
    op.create_index(
        "ix_proxy_leases_listing_result",
        "proxy_leases",
        ["use", "line", "released_at"],
        postgresql_where=sa.text("listing_result IN ('complete', 'empty_response')"),
    )


def downgrade() -> None:
    op.drop_index("ix_proxy_leases_listing_result", table_name="proxy_leases")
    op.drop_column("proxy_leases", "listing_result")

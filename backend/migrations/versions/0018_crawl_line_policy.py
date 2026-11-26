"""新增采集出口策略状态及租约标记。"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proxy_crawl_line_policy",
        sa.Column("policy_epoch", sa.String(128), primary_key=True),
        sa.Column("line_key", sa.String(160), primary_key=True),
        sa.Column("evidence_since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("empty_step", sa.Integer(), nullable=False),
        sa.Column("empty_until", sa.DateTime(timezone=True)),
        sa.Column("probe_lease_id", sa.Uuid()),
        sa.Column("probe_granted_at", sa.DateTime(timezone=True)),
        sa.Column("last_crawl_acquired_at", sa.DateTime(timezone=True)),
        sa.Column("last_valid_crawl_released_at", sa.DateTime(timezone=True)),
        sa.Column("last_bootstrap_at", sa.DateTime(timezone=True)),
    )
    op.add_column("proxy_leases", sa.Column("crawl_policy_epoch", sa.String(128)))


def downgrade() -> None:
    op.drop_column("proxy_leases", "crawl_policy_epoch")
    op.drop_table("proxy_crawl_line_policy")

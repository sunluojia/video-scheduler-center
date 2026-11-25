"""添加设备专属代理和设备线路并发覆盖。"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("proxies", sa.Column("bound_device", sa.String(128)))
    op.add_column("proxy_rules", sa.Column("device_line_limit", sa.Integer()))


def downgrade() -> None:
    op.drop_column("proxy_rules", "device_line_limit")
    op.drop_column("proxies", "bound_device")

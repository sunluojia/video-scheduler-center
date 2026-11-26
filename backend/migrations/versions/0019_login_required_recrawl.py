"""把历史需要登录的达人移到待重跑。"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

creators = sa.table("creators", sa.column("status", sa.String()), sa.column("note", sa.Text()))


def _move_login_required(before: str, after: str) -> None:
    op.execute(
        creators.update()
        .where(creators.c.status == before, creators.c.note == "需要登录")
        .values(status=after)
    )


def upgrade() -> None:
    _move_login_required("failed", "recrawl")


def downgrade() -> None:
    _move_login_required("recrawl", "failed")

"""发件箱：发给执行程序的任务与业务数据在同一事务写入，提交后由 Worker 投递。"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Column,
    Identity,
    Index,
    Row,
    String,
    Table,
    delete,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB

from scheduler_center.core.common.clock import utcnow
from scheduler_center.core.database.mapping import metadata, timestamp
from scheduler_center.core.database.repository import Repository

outbox = Table(
    "outbox",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("queue", String(64), nullable=False),
    Column("actor", String(64), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", timestamp(), nullable=False),
    Column("published_at", timestamp()),
    Index("ix_outbox_pending", "id", postgresql_where=text("published_at IS NULL")),
)


@dataclass(frozen=True)
class Route:
    """执行程序约定的队列名和处理器名。"""

    queue: str
    actor: str


class Outbox(Repository):
    async def put(self, route: Route, payload: dict) -> None:
        await self._session.execute(
            insert(outbox).values(
                queue=route.queue, actor=route.actor, payload=payload, created_at=utcnow()
            )
        )

    async def pending(self, limit: int) -> list[Row]:
        """按写入顺序取出待投递的消息；其他 Worker 正在投递的行会被跳过。"""
        stmt = (
            select(outbox.c.id, outbox.c.queue, outbox.c.actor, outbox.c.payload)
            .where(outbox.c.published_at.is_(None))
            .order_by(outbox.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True, key_share=True)
        )
        return list((await self._session.execute(stmt)).all())

    async def withdraw(self, batch_id: UUID) -> None:
        """撤回批次还没投递的任务（发给执行程序的任务都带 batch_id）。

        正在投递的行已被锁住，等投递提交后就不再符合条件，由执行程序按取消处理。
        """
        await self._session.execute(
            delete(outbox).where(
                outbox.c.published_at.is_(None),
                outbox.c.payload["batch_id"].astext == str(batch_id),
            )
        )

    async def mark_published(self, message_ids: Sequence[int], now: datetime) -> None:
        await self._session.execute(
            update(outbox).where(outbox.c.id.in_(message_ids)).values(published_at=now)
        )

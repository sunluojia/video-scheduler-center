"""执行程序回传的事件：按事件 ID 去重，原样保存，作为任务的经过供页面查看。"""

from collections.abc import Sequence
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Column,
    Identity,
    Integer,
    Row,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, insert

from scheduler_center.core.common.clock import utcnow
from scheduler_center.core.database.mapping import enum_column, metadata, reference, timestamp
from scheduler_center.core.database.repository import Repository
from scheduler_center.modules.analysis.messages import AnalysisEvent
from scheduler_center.modules.crawl.messages import CrawlerEvent


class Source(StrEnum):
    CRAWLER = "crawler"
    ANALYSIS = "analysis"


events = Table(
    "events",
    metadata,
    Column("seq", BigInteger, Identity(), primary_key=True),
    Column("source", enum_column(Source), nullable=False),
    Column("event_id", Uuid, nullable=False),
    Column("task_id", Uuid, nullable=False, index=True),  # 爬取程序的任务 ID，或分析任务 ID
    Column("run", Integer, nullable=False),
    Column("type", String(32), nullable=False),
    Column("batch_id", reference("batches.id"), nullable=False, index=True),
    Column("payload", JSONB, nullable=False),
    Column("occurred_at", timestamp(), nullable=False),
    Column("received_at", timestamp(), nullable=False),
    UniqueConstraint("source", "event_id"),
)


class EventLog(Repository):
    async def record(
        self, source: Source, task_id: UUID, batch_id: UUID, event: CrawlerEvent | AnalysisEvent
    ) -> bool:
        """保存事件；已经收到过的返回 False。"""
        stmt = (
            insert(events)
            .values(
                source=source,
                event_id=event.event_id,
                task_id=task_id,
                run=event.run,
                type=event.type,
                batch_id=batch_id,
                payload=event.model_dump(mode="json"),
                occurred_at=event.occurred_at,
                received_at=utcnow(),
            )
            .on_conflict_do_nothing(index_elements=["source", "event_id"])
            .returning(events.c.seq)
        )
        return (await self._session.scalar(stmt)) is not None

    async def of_tasks(self, task_ids: Sequence[UUID]) -> list[Row]:
        stmt = (
            select(events.c.source, events.c.task_id, events.c.payload, events.c.received_at)
            .where(events.c.task_id.in_(task_ids))
            .order_by(events.c.occurred_at, events.c.seq)
        )
        return list((await self._session.execute(stmt)).all())

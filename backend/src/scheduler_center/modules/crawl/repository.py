from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update

from scheduler_center.core.database.repository import Repository
from scheduler_center.modules.crawl.models import CrawlerTask, CrawlerTaskKind, CrawlerTaskStatus
from scheduler_center.modules.crawl.tables import crawler_tasks

ACTIVE = (CrawlerTaskStatus.PUBLISHED, CrawlerTaskStatus.ACCEPTED)


class CrawlerTaskRepository(Repository):
    async def crawl_task(self, creator_id: UUID) -> CrawlerTask | None:
        stmt = select(CrawlerTask).where(
            crawler_tasks.c.creator_id == creator_id,
            crawler_tasks.c.kind == CrawlerTaskKind.CRAWL,
        )
        return await self._session.scalar(stmt)

    async def download_task(self, work_id: UUID) -> CrawlerTask | None:
        stmt = select(CrawlerTask).where(crawler_tasks.c.work_id == work_id)
        return await self._session.scalar(stmt)

    async def of_works(self, work_ids: Sequence[UUID]) -> dict[UUID, CrawlerTask]:
        stmt = select(CrawlerTask).where(crawler_tasks.c.work_id.in_(work_ids))
        return {task.work_id: task for task in await self._session.scalars(stmt)}

    async def cancel_batch(self, batch_id: UUID, now: datetime) -> None:
        await self._cancel(crawler_tasks.c.batch_id == batch_id, now)

    async def cancel_downloads(self, creator_id: UUID, now: datetime) -> None:
        await self._cancel(
            (crawler_tasks.c.creator_id == creator_id)
            & (crawler_tasks.c.kind == CrawlerTaskKind.DOWNLOAD),
            now,
        )

    async def _cancel(self, scope, now: datetime) -> None:
        await self._session.execute(
            update(crawler_tasks)
            .where(scope, crawler_tasks.c.status.in_(ACTIVE))
            .values(status=CrawlerTaskStatus.CANCELLED, finished_at=now, current_attempt_id=None)
        )

    async def unaccepted(self, before: datetime, limit: int) -> list[CrawlerTask]:
        """发布后长时间没有受理的任务；其他 Worker 正在处理的行会被跳过。"""
        stmt = (
            select(CrawlerTask)
            .where(
                crawler_tasks.c.status == CrawlerTaskStatus.PUBLISHED,
                crawler_tasks.c.published_at < before,
            )
            .order_by(crawler_tasks.c.published_at)
            .limit(limit)
            .with_for_update(skip_locked=True, key_share=True)
        )
        return list(await self._session.scalars(stmt))

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update

from scheduler_center.core.database.repository import Repository
from scheduler_center.modules.crawl.models import (
    CrawlerRunKind,
    CrawlerRunStatus,
    CrawlerTask,
    CrawlerTaskKind,
    CrawlerTaskStatus,
)
from scheduler_center.modules.crawl.tables import crawler_task_runs, crawler_tasks

ACTIVE = (CrawlerTaskStatus.PUBLISHED, CrawlerTaskStatus.ACCEPTED)


class CrawlerTaskRepository(Repository):
    async def crawl_task(self, creator_id: UUID) -> CrawlerTask | None:
        stmt = select(CrawlerTask).where(
            crawler_tasks.c.creator_id == creator_id,
            crawler_tasks.c.kind == CrawlerTaskKind.CRAWL,
        )
        return await self._session.scalar(stmt)

    async def latest_crawl_finished_at(
        self, batch_id: UUID, creator_ids: Sequence[UUID] | None = None
    ) -> dict[UUID, datetime]:
        """批量读取最近一次成功采集的完成时间，兼容轮次记录上线前的任务。"""
        tasks, runs = crawler_tasks.c, crawler_task_runs.c
        current = select(tasks.creator_id, tasks.finished_at).where(
            tasks.batch_id == batch_id,
            tasks.kind == CrawlerTaskKind.CRAWL,
            tasks.status == CrawlerTaskStatus.SUCCEEDED,
            tasks.finished_at.is_not(None),
        )
        # 成功轮次的 updated_at 与任务完成时间同时写入，重爬时会保留该轮次。
        history = select(runs.creator_id, runs.updated_at).where(
            runs.batch_id == batch_id,
            runs.kind == CrawlerRunKind.CRAWL,
            runs.status == CrawlerRunStatus.SUCCEEDED,
        )
        if creator_ids is not None:
            if not creator_ids:
                return {}
            current = current.where(tasks.creator_id.in_(creator_ids))
            history = history.where(runs.creator_id.in_(creator_ids))
        finished = current.union_all(history).subquery().c
        stmt = select(finished.creator_id, func.max(finished.finished_at)).group_by(
            finished.creator_id
        )
        return dict((await self._session.execute(stmt)).all())

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

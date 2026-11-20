from collections import defaultdict
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, func, insert, or_, select

from scheduler_center.core.database.repository import Repository
from scheduler_center.modules.batch.models import (
    CREATOR_FINAL,
    Batch,
    BatchStatus,
    Creator,
    CreatorProgress,
    CreatorStatus,
    Work,
    WorkKind,
    WorkStatus,
)
from scheduler_center.modules.batch.progress import creator_progress
from scheduler_center.modules.batch.tables import batches, creators, qr_codes, works

type Counts = dict[UUID, dict[str, int]]


class BatchRepository(Repository):
    async def page(
        self, status: BatchStatus | None, offset: int, limit: int
    ) -> tuple[list[Batch], int]:
        stmt = select(Batch).order_by(batches.c.created_at.desc())
        if status is not None:
            stmt = stmt.where(batches.c.status == status)
        return await self._page(stmt, offset, limit)

    async def running_ids(self) -> list[UUID]:
        stmt = select(batches.c.id).where(batches.c.status == BatchStatus.RUNNING)
        return list(await self._session.scalars(stmt))

    async def creator_counts(self, batch_ids: Sequence[UUID]) -> tuple[Counts, Counts]:
        """一次查询同时获取各批次的业务状态和工作台进度计数。"""
        progress = creator_progress().label("progress")
        stmt = (
            select(creators.c.batch_id, creators.c.status, progress, func.count())
            .where(creators.c.batch_id.in_(batch_ids))
            .group_by(creators.c.batch_id, creators.c.status, "progress")
        )
        statuses: Counts = defaultdict(dict)
        stages: Counts = defaultdict(dict)
        for batch_id, status, stage, count in await self._session.execute(stmt):
            by_status, by_stage = statuses[batch_id], stages[batch_id]
            by_status[status] = by_status.get(status, 0) + count
            by_stage[stage] = by_stage.get(stage, 0) + count
        return statuses, stages

    async def video_counts(self, batch_ids: Sequence[UUID]) -> Counts:
        """各批次每种状态的视频数。"""
        return await self._counts(
            works, works.c.batch_id.in_(batch_ids), works.c.kind == WorkKind.VIDEO
        )

    async def _counts(self, table, *conditions) -> Counts:
        stmt = (
            select(table.c.batch_id, table.c.status, func.count())
            .where(*conditions)
            .group_by(table.c.batch_id, table.c.status)
        )
        counts: Counts = defaultdict(dict)
        for batch_id, status, count in await self._session.execute(stmt):
            counts[batch_id][status] = count
        return counts


class CreatorRepository(Repository):
    async def of_batch(
        self, batch_id: UUID, *, status: CreatorStatus | None = None, lock: bool = False
    ) -> list[Creator]:
        """加锁时按 ID 顺序加锁，多个操作同时锁一批达人也不会互相等待成环。"""
        stmt = select(Creator).where(creators.c.batch_id == batch_id)
        if status is not None:
            stmt = stmt.where(creators.c.status == status)
        if lock:
            stmt = (
                stmt.order_by(creators.c.id)
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            )
        else:
            stmt = stmt.order_by(creators.c.position)
        return list(await self._session.scalars(stmt))

    async def search(
        self,
        batch_id: UUID,
        status: CreatorStatus | None,
        keyword: str | None,
        offset: int,
        limit: int,
        *,
        progress: CreatorProgress | None = None,
    ) -> tuple[list[tuple[Creator, CreatorProgress]], int]:
        stage = creator_progress().label("progress")
        stmt = (
            select(Creator, stage)
            .where(creators.c.batch_id == batch_id)
            .order_by(creators.c.position)
        )
        if status is not None:
            stmt = stmt.where(creators.c.status == status)
        if progress is not None:
            stmt = stmt.where(stage == progress.value)
        if keyword:
            pattern = f"%{keyword}%"
            stmt = stmt.where(
                or_(
                    creators.c.name.ilike(pattern),
                    creators.c.handle.ilike(pattern),
                    creators.c.oecuid.ilike(pattern),
                )
            )
        total = await self._session.scalar(
            stmt.with_only_columns(func.count(), maintain_column_froms=True).order_by(None)
        )
        rows = await self._session.execute(stmt.offset(offset).limit(limit))
        return [(item, CreatorProgress(value)) for item, value in rows], total or 0

    async def unfinished(self, batch_id: UUID) -> int:
        stmt = select(func.count()).where(
            creators.c.batch_id == batch_id, creators.c.status.not_in(CREATOR_FINAL)
        )
        return await self._session.scalar(stmt) or 0

    async def add_qr_codes(self, rows: list[dict]) -> None:
        """rows：creator_id、content、media_type。"""
        if rows:
            await self._session.execute(insert(qr_codes), rows)

    async def qr(self, creator_id: UUID) -> tuple[bytes, str] | None:
        stmt = select(qr_codes.c.content, qr_codes.c.media_type).where(
            qr_codes.c.creator_id == creator_id
        )
        row = (await self._session.execute(stmt)).first()
        return None if row is None else (row.content, row.media_type)

    async def qr_of_batch(self, batch_id: UUID) -> dict[UUID, bytes]:
        stmt = (
            select(qr_codes.c.creator_id, qr_codes.c.content)
            .join(creators, creators.c.id == qr_codes.c.creator_id)
            .where(creators.c.batch_id == batch_id)
        )
        return dict((await self._session.execute(stmt)).all())


class WorkRepository(Repository):
    async def of_creators(self, creator_ids: Sequence[UUID]) -> list[Work]:
        stmt = (
            select(Work)
            .where(works.c.creator_id.in_(creator_ids))
            .order_by(works.c.creator_id, works.c.position)
        )
        return list(await self._session.scalars(stmt))

    async def of_creator(self, creator_id: UUID) -> list[Work]:
        return await self.of_creators([creator_id])

    async def of_batch(self, batch_id: UUID) -> list[Work]:
        stmt = select(Work).where(works.c.batch_id == batch_id).order_by(works.c.position)
        return list(await self._session.scalars(stmt))

    async def remove_of_creator(self, creator_id: UUID) -> None:
        """删除达人的作品，连同下载任务、分析任务和素材记录。"""
        await self._session.execute(delete(works).where(works.c.creator_id == creator_id))

    async def search(
        self, statuses: Sequence[WorkStatus], batch_id: UUID | None, offset: int, limit: int
    ) -> tuple[list[tuple[Work, Creator, Batch]], int]:
        stmt = (
            select(Work, Creator, Batch)
            .join(Creator, creators.c.id == works.c.creator_id)
            .join(Batch, batches.c.id == works.c.batch_id)
            .where(works.c.status.in_(statuses))
            .order_by(works.c.updated_at.desc())
        )
        if batch_id is not None:
            stmt = stmt.where(works.c.batch_id == batch_id)
        total = await self._session.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = (await self._session.execute(stmt.offset(offset).limit(limit))).all()
        return list(rows), total or 0

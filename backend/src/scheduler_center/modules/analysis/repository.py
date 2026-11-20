from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update

from scheduler_center.core.database.repository import Repository
from scheduler_center.modules.analysis.models import AnalysisJob, AnalysisStage, JobKind, JobStatus
from scheduler_center.modules.analysis.tables import analysis_jobs, analysis_stages

ACTIVE = (
    JobStatus.SUBMITTED,
    JobStatus.ACCEPTED,
    JobStatus.PROCESSING,
    JobStatus.FAILED,
    JobStatus.NEEDS_REVIEW,
)
"""还可能继续推进的任务，取消批次时一并取消。"""


class AnalysisJobRepository(Repository):
    async def video_job(self, work_id: UUID) -> AnalysisJob | None:
        stmt = select(AnalysisJob).where(analysis_jobs.c.work_id == work_id)
        return await self._session.scalar(stmt)

    async def of_ids(self, job_ids: Sequence[UUID]) -> list[AnalysisJob]:
        stmt = select(AnalysisJob).where(analysis_jobs.c.id.in_(job_ids))
        return list(await self._session.scalars(stmt))

    async def of_works(self, work_ids: Sequence[UUID]) -> dict[UUID, AnalysisJob]:
        stmt = select(AnalysisJob).where(analysis_jobs.c.work_id.in_(work_ids))
        return {job.work_id: job for job in await self._session.scalars(stmt)}

    async def summary(self, creator_id: UUID) -> AnalysisJob | None:
        stmt = select(AnalysisJob).where(
            analysis_jobs.c.creator_id == creator_id, analysis_jobs.c.kind == JobKind.SUMMARY
        )
        return await self._session.scalar(stmt)

    async def stage(self, job_id: UUID, run: int, stage: str, segment: int | None):
        stmt = select(AnalysisStage).where(
            analysis_stages.c.job_id == job_id,
            analysis_stages.c.run == run,
            analysis_stages.c.stage == stage,
            analysis_stages.c.segment.is_not_distinct_from(segment),
        )
        return await self._session.scalar(stmt)

    async def stages(self, job_id: UUID, run: int) -> list[AnalysisStage]:
        stmt = (
            select(AnalysisStage)
            .where(analysis_stages.c.job_id == job_id, analysis_stages.c.run == run)
            .order_by(analysis_stages.c.started_at.nulls_last(), analysis_stages.c.occurred_at)
        )
        return list(await self._session.scalars(stmt))

    async def cancel_batch(self, batch_id: UUID, now: datetime) -> None:
        await self._session.execute(
            update(analysis_jobs)
            .where(analysis_jobs.c.batch_id == batch_id, analysis_jobs.c.status.in_(ACTIVE))
            .values(status=JobStatus.CANCELLED, finished_at=now)
        )

    async def unaccepted(self, before: datetime, limit: int) -> list[AnalysisJob]:
        """提交后长时间没有受理的任务；其他 Worker 正在处理的行会被跳过。"""
        stmt = (
            select(AnalysisJob)
            .where(
                analysis_jobs.c.status == JobStatus.SUBMITTED,
                analysis_jobs.c.submitted_at < before,
            )
            .order_by(analysis_jobs.c.submitted_at)
            .limit(limit)
            .with_for_update(skip_locked=True, key_share=True)
        )
        return list(await self._session.scalars(stmt))

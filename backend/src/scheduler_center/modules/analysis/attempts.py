from collections.abc import Sequence
from dataclasses import asdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from scheduler_center.core.database.repository import Repository
from scheduler_center.modules.analysis.messages import AnalysisEvent, JobEvent, StageEvent
from scheduler_center.modules.analysis.models import (
    AnalysisJob,
    AnalysisStageAttempt,
    JobStatus,
    StageStatus,
)
from scheduler_center.modules.analysis.tables import analysis_stage_attempts as attempts
from scheduler_center.modules.batch.tables import works

JOB_STAGE = "job"
JOB_IN_PROGRESS = (JobStatus.SUBMITTED, JobStatus.ACCEPTED, JobStatus.PROCESSING)


class AnalysisAttempts(Repository):
    async def record(
        self, job: AnalysisJob, event: AnalysisEvent, attempt_id: UUID
    ) -> AnalysisStageAttempt | None:
        """同一任务的事件由调用方串行处理，阶段明细优先于同 ID 的任务级事件。"""
        incoming = _attempt_of(event, attempt_id)
        data = asdict(incoming)
        data.update(batch_id=job.batch_id, creator_id=job.creator_id)
        if job.work_id is not None:
            data["work_id"] = (
                select(works.c.platform_id).where(works.c.id == job.work_id).scalar_subquery()
            )
        stmt = (
            insert(attempts)
            .values(**data)
            .on_conflict_do_nothing(index_elements=[attempts.c.attempt_id])
            .returning(attempts.c.attempt_id)
        )
        if await self._session.scalar(stmt):
            return incoming
        attempt = await self.require(AnalysisStageAttempt, attempt_id)
        if (attempt.job_id, attempt.run) != (incoming.job_id, incoming.run):
            return None
        if attempt.stage != JOB_STAGE:
            if (
                event.retry_generation is not None
                and attempt.retry_generation != event.retry_generation
            ):
                return None
            if isinstance(event, JobEvent):
                return attempt
            if (attempt.stage, attempt.segment, attempt.attempt_no) != (
                incoming.stage,
                incoming.segment,
                incoming.attempt_no,
            ):
                return None
        else:
            if event.retry_generation is not None:
                attempt.retry_generation = event.retry_generation
            if isinstance(event, StageEvent):
                attempt.stage, attempt.segment, attempt.attempt_no = (
                    incoming.stage,
                    incoming.segment,
                    incoming.attempt_no,
                )
                attempt.started_at = attempt.ended_at = attempt.error = attempt.model = None

        if incoming.started_at and (
            not attempt.started_at or incoming.started_at < attempt.started_at
        ):
            attempt.started_at = incoming.started_at
        if attempt.ended_at is None or (
            incoming.ended_at and incoming.ended_at >= attempt.ended_at
        ):
            if not (
                isinstance(event, JobEvent)
                and attempt.status == JobStatus.PROCESSING
                and event.status in (JobStatus.SUBMITTED, JobStatus.ACCEPTED)
            ):
                attempt.status, attempt.ended_at, attempt.error = (
                    incoming.status,
                    incoming.ended_at,
                    incoming.error,
                )
            attempt.executor_id = incoming.executor_id or attempt.executor_id
            attempt.executor_session_id = (
                incoming.executor_session_id or attempt.executor_session_id
            )
            attempt.model = incoming.model or attempt.model
        else:
            attempt.executor_id = attempt.executor_id or incoming.executor_id
            attempt.executor_session_id = (
                attempt.executor_session_id or incoming.executor_session_id
            )
            attempt.model = attempt.model or incoming.model
        return attempt

    async def of_jobs(self, job_ids: Sequence[UUID]) -> list[AnalysisStageAttempt]:
        stmt = (
            select(AnalysisStageAttempt)
            .where(attempts.c.job_id.in_(job_ids))
            .order_by(
                attempts.c.run.desc(),
                attempts.c.retry_generation.desc(),
                attempts.c.started_at.desc().nulls_last(),
                attempts.c.ended_at.desc().nulls_last(),
                attempts.c.attempt_id,
            )
        )
        return list(await self._session.scalars(stmt))


def _attempt_of(event: AnalysisEvent, attempt_id: UUID) -> AnalysisStageAttempt:
    stage, segment, number, model = JOB_STAGE, None, 1, None
    in_progress = event.status in JOB_IN_PROGRESS
    if isinstance(event, StageEvent):
        stage, segment, number = event.stage, event.segment, event.attempt
        model = event.model[:128] if event.model else None
        in_progress = event.status == StageStatus.STARTED
    return AnalysisStageAttempt(
        attempt_id,
        event.job_id,
        event.run,
        event.retry_generation or 0,
        stage,
        segment,
        number,
        event.executor_id,
        event.executor_session_id,
        event.status,
        event.occurred_at if in_progress else None,
        None if in_progress else event.occurred_at,
        event.error,
        model,
    )

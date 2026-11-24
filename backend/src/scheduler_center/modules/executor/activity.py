from datetime import datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from scheduler_center.modules.analysis.attempts import JOB_IN_PROGRESS
from scheduler_center.modules.analysis.models import JobStatus, StageStatus
from scheduler_center.modules.analysis.tables import (
    analysis_jobs,
    analysis_stage_attempts,
    analysis_stages,
)
from scheduler_center.modules.crawl.attempts import ACTIVE_ATTEMPTS
from scheduler_center.modules.crawl.models import CrawlerAttemptStatus
from scheduler_center.modules.crawl.tables import crawler_task_attempts
from scheduler_center.modules.executor.models import Executor, ExecutorKind


class ExecutorActivity(BaseModel):
    active: int = 0
    completed_24h: int = 0
    expired_24h: int = 0
    takeovers_24h: int = 0


async def executor_activity(
    session: AsyncSession, executors: list[Executor], now: datetime
) -> dict[tuple[ExecutorKind, str], ExecutorActivity]:
    since = now - timedelta(hours=24)
    crawler = [item.instance_id for item in executors if item.kind == ExecutorKind.CRAWLER]
    analysis = [item.instance_id for item in executors if item.kind == ExecutorKind.ANALYSIS]
    counts: dict[tuple[ExecutorKind, str], ExecutorActivity] = {}
    if crawler:
        for instance_id, active, completed, expired, takeovers in await session.execute(
            _crawler_activity(crawler, now, since)
        ):
            counts[ExecutorKind.CRAWLER, instance_id] = ExecutorActivity(
                active=active, completed_24h=completed, expired_24h=expired, takeovers_24h=takeovers
            )
    if analysis:
        for instance_id, active, completed in await session.execute(
            _analysis_activity(analysis, since)
        ):
            counts[ExecutorKind.ANALYSIS, instance_id] = ExecutorActivity(
                active=active, completed_24h=completed
            )
    return counts


def _crawler_activity(devices: list[str], now: datetime, since: datetime):
    current = crawler_task_attempts.c
    previous = crawler_task_attempts.alias("previous_attempt")
    active = and_(current.status.in_(ACTIVE_ATTEMPTS), current.lease_expires_at > now)
    ended_recently = current.ended_at >= since
    claimed_recently = current.claimed_at >= since
    return (
        select(
            current.executor_id,
            func.count().filter(active),
            func.count().filter(ended_recently, current.status == CrawlerAttemptStatus.SUCCEEDED),
            func.count().filter(ended_recently, current.status == CrawlerAttemptStatus.EXPIRED),
            func.count().filter(claimed_recently, previous.c.attempt_id.is_not(None)),
        )
        .select_from(
            crawler_task_attempts.outerjoin(
                previous,
                and_(
                    previous.c.task_id == current.task_id,
                    previous.c.run == current.run,
                    previous.c.attempt_no == current.attempt_no - 1,
                    previous.c.status == CrawlerAttemptStatus.EXPIRED,
                    previous.c.executor_id != current.executor_id,
                ),
            )
        )
        .where(current.executor_id.in_(devices), or_(active, ended_recently, claimed_recently))
        .group_by(current.executor_id)
    )


def _analysis_activity(devices: list[str], since: datetime):
    attempt = analysis_stage_attempts.c
    job = analysis_jobs.c
    stage = analysis_stages.c
    active = and_(
        attempt.status == StageStatus.STARTED,
        job.run == attempt.run,
        job.status.in_(JOB_IN_PROGRESS),
        func.coalesce(stage.retry_generation, 0) == attempt.retry_generation,
        stage.attempt == attempt.attempt_no,
    )
    completed = and_(
        attempt.ended_at >= since, attempt.status.in_((StageStatus.SUCCEEDED, JobStatus.COMPLETED))
    )
    return (
        select(attempt.executor_id, func.count().filter(active), func.count().filter(completed))
        .select_from(
            analysis_stage_attempts.outerjoin(analysis_jobs, job.id == attempt.job_id).outerjoin(
                analysis_stages,
                and_(
                    stage.job_id == attempt.job_id,
                    stage.run == attempt.run,
                    stage.stage == attempt.stage,
                    stage.segment.is_not_distinct_from(attempt.segment),
                ),
            )
        )
        .where(attempt.executor_id.in_(devices), or_(active, completed))
        .group_by(attempt.executor_id)
    )

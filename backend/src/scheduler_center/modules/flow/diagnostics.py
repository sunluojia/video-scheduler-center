import csv
from collections.abc import Iterable
from datetime import datetime
from io import StringIO
from uuid import UUID

from sqlalchemy import Integer, Row, String, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from scheduler_center.modules.analysis.tables import analysis_jobs, analysis_stage_attempts
from scheduler_center.modules.batch.models import Batch
from scheduler_center.modules.batch.tables import creators, works
from scheduler_center.modules.crawl.tables import crawler_task_attempts, crawler_task_runs


async def batch_attempts(session: AsyncSession, batch_id: UUID) -> list[Row]:
    attempts = crawler_task_attempts.c
    runs = crawler_task_runs.c
    crawler = (
        select(
            literal("crawler").label("source"),
            runs.creator_id,
            creators.c.name.label("creator"),
            runs.work_id,
            runs.kind.cast(String).label("stage"),
            literal(None, Integer).label("segment"),
            runs.run,
            literal(None, Integer).label("retry_generation"),
            attempts.attempt_id,
            attempts.attempt_no,
            attempts.executor_id,
            attempts.executor_session_id,
            attempts.claimed_at.label("started_at"),
            attempts.ended_at,
            attempts.status.cast(String).label("status"),
            attempts.error,
            attempts.route_kind,
        )
        .select_from(
            crawler_task_attempts.join(
                crawler_task_runs, ((attempts.task_id == runs.task_id) & (attempts.run == runs.run))
            ).join(creators, creators.c.id == runs.creator_id)
        )
        .where(runs.batch_id == batch_id)
    )
    stages = analysis_stage_attempts.c
    jobs = analysis_jobs.c
    creator_id = func.coalesce(stages.creator_id, jobs.creator_id)
    analysis = (
        select(
            literal("analysis"),
            creator_id,
            creators.c.name,
            func.coalesce(stages.work_id, works.c.platform_id),
            stages.stage,
            stages.segment,
            stages.run,
            stages.retry_generation,
            stages.attempt_id,
            stages.attempt_no,
            stages.executor_id,
            stages.executor_session_id,
            stages.started_at,
            stages.ended_at,
            stages.status,
            stages.error,
            literal(None, String),
        )
        .select_from(
            analysis_stage_attempts.outerjoin(analysis_jobs, jobs.id == stages.job_id)
            .outerjoin(works, works.c.id == jobs.work_id)
            .join(creators, creators.c.id == creator_id)
        )
        .where(
            or_(
                stages.batch_id == batch_id, stages.batch_id.is_(None) & (jobs.batch_id == batch_id)
            )
        )
    )
    report = union_all(crawler, analysis).subquery()
    order = func.coalesce(report.c.started_at, report.c.ended_at)
    return list((await session.execute(select(report).order_by(order, report.c.attempt_id))).all())


def build_attempt_csv(batch: Batch, rows: Iterable[Row]) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "批次ID",
            "批次",
            "来源",
            "达人ID",
            "达人",
            "平台作品ID",
            "阶段",
            "分段",
            "轮次",
            "补跑代次",
            "认领ID",
            "尝试序号",
            "执行器",
            "执行器会话ID",
            "开始时间",
            "结束时间",
            "状态",
            "原因",
            "线路类型",
        ]
    )
    for row in rows:
        writer.writerow(_cell(value) for value in (batch.id, batch.name, *row))
    return output.getvalue().encode("utf-8-sig")


def _cell(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value  # 防止用户输入在电子表格中被当作公式执行
    return value

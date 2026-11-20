from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from scheduler_center.core.common.errors import NotFound
from scheduler_center.core.web.dependencies import DatabaseDep, PagingDep
from scheduler_center.core.web.schemas import Page
from scheduler_center.modules.analysis.messages import JobState
from scheduler_center.modules.analysis.repository import AnalysisJobRepository
from scheduler_center.modules.batch.models import Creator, Work, WorkStatus
from scheduler_center.modules.batch.repository import CreatorRepository, WorkRepository
from scheduler_center.modules.batch.schemas import WorkDetail, WorkRow, WorkView
from scheduler_center.modules.crawl.repository import CrawlerTaskRepository
from scheduler_center.modules.executor.dependencies import GatewayDep
from scheduler_center.modules.executor.models import ExecutorKind
from scheduler_center.modules.flow.history import EventLog
from scheduler_center.modules.flow.service import Flow

router = APIRouter(prefix="/works", tags=["视频"])
ATTENTION = [WorkStatus.NEEDS_REVIEW, WorkStatus.FAILED]


@router.get("")
async def list_works(
    database: DatabaseDep,
    paging: PagingDep,
    status: Annotated[list[WorkStatus], Query()] = ATTENTION,
    batch_id: UUID | None = None,
) -> Page[WorkRow]:
    """按状态查找视频，默认列出待核查和失败的，最近变化的在前。"""
    async with database.transaction() as session:
        rows, total = await WorkRepository(session).search(
            status, batch_id, paging.offset, paging.size
        )
    items = [WorkRow(work=work, creator=creator, batch=batch) for work, creator, batch in rows]
    return Page(items=items, total=total)


@router.get("/{work_id}")
async def get_work(work_id: UUID, database: DatabaseDep) -> WorkDetail:
    async with database.transaction() as session:
        work = await WorkRepository(session).require(Work, work_id)
        download = await CrawlerTaskRepository(session).download_task(work.id)
        jobs = AnalysisJobRepository(session)
        job = await jobs.video_job(work.id)
        task_ids = [task.id for task in (download, job) if task]
        return WorkDetail(
            work=work,
            creator=await CreatorRepository(session).require(Creator, work.creator_id),
            download=download,
            analysis=job,
            stages=await jobs.stages(job.id, job.run) if job else [],
            history=await EventLog(session).of_tasks(task_ids),
        )


@router.post("/{work_id}/redownload")
async def redownload(work_id: UUID, database: DatabaseDep) -> WorkView:
    async with database.transaction() as session:
        return WorkView.model_validate(await Flow(session).redownload(work_id))


@router.post("/{work_id}/reanalyze")
async def reanalyze(work_id: UUID, database: DatabaseDep) -> WorkView:
    """新一轮分析，不复用已有结果；原视频已清理时先重新下载。"""
    async with database.transaction() as session:
        return WorkView.model_validate(await Flow(session).reanalyze(work_id))


@router.post("/{work_id}/abandon")
async def abandon(work_id: UUID, database: DatabaseDep) -> WorkView:
    async with database.transaction() as session:
        return WorkView.model_validate(await Flow(session).abandon(work_id))


@router.post("/{work_id}/retry")
async def retry(work_id: UUID, database: DatabaseDep, gateway: GatewayDep) -> WorkView:
    """补跑：分析服务只重做失败和待核查的阶段。"""
    async with database.transaction() as session:
        job = await Flow(session).retry_target(work_id)
    content = await gateway.call(ExecutorKind.ANALYSIS, "POST", f"/api/jobs/{job.id}/retry")
    async with database.transaction() as session:
        await Flow(session).retried(job.batch_id, [JobState.model_validate(content)])
        return WorkView.model_validate(await WorkRepository(session).require(Work, work_id))


@router.get("/{work_id}/analysis")
async def analysis_detail(work_id: UUID, database: DatabaseDep, gateway: GatewayDep) -> Response:
    """分析服务中的任务详情：讲话文本、识别结果、模型调用记录。"""
    return await _forward(database, gateway, work_id, "")


@router.get("/{work_id}/frames/{index}")
async def frame(work_id: UUID, index: int, database: DatabaseDep, gateway: GatewayDep):
    return await _forward(database, gateway, work_id, f"/frames/{index}")


@router.get("/{work_id}/audio/{index}")
async def audio(work_id: UUID, index: int, database: DatabaseDep, gateway: GatewayDep):
    return await _forward(database, gateway, work_id, f"/audio/{index}")


async def _forward(database, gateway, work_id: UUID, path: str) -> Response:
    async with database.transaction() as session:
        job = await AnalysisJobRepository(session).video_job(work_id)
    if job is None:
        raise NotFound("视频还没有提交分析")
    response = await gateway.send(ExecutorKind.ANALYSIS, "GET", f"/api/jobs/{job.id}{path}")
    return Response(
        response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type"),
    )

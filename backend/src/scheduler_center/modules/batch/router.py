from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

import anyio
from fastapi import APIRouter, Form, Response, UploadFile
from pydantic import TypeAdapter

from scheduler_center.core.database.engine import Database
from scheduler_center.core.web.dependencies import DatabaseDep, PagingDep
from scheduler_center.core.web.schemas import Page
from scheduler_center.modules.analysis.messages import JobState
from scheduler_center.modules.batch.models import Batch, BatchStatus, CreatorProgress, CreatorStatus
from scheduler_center.modules.batch.repository import (
    BatchRepository,
    CreatorRepository,
    WorkRepository,
)
from scheduler_center.modules.batch.schemas import (
    Affected,
    BatchAction,
    BatchRow,
    BatchView,
    CreatorRow,
    CreatorView,
    WorkView,
)
from scheduler_center.modules.batch.service import create_batch
from scheduler_center.modules.executor.dependencies import GatewayDep
from scheduler_center.modules.executor.gateway import Gateway
from scheduler_center.modules.executor.models import ExecutorKind
from scheduler_center.modules.flow.service import Flow
from scheduler_center.modules.report.exporter import CreatorResult, build_workbook
from scheduler_center.modules.report.importer import read_roster

router = APIRouter(prefix="/batches", tags=["批次"])
job_states = TypeAdapter(list[JobState])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("")
async def list_batches(
    database: DatabaseDep, paging: PagingDep, status: BatchStatus | None = None
) -> Page[BatchRow]:
    async with database.transaction() as session:
        batches = BatchRepository(session)
        items, total = await batches.page(status, paging.offset, paging.size)
        rows = await _rows(batches, items)
    return Page(items=rows, total=total)


@router.post("", status_code=201)
async def create(
    database: DatabaseDep,
    name: Annotated[str, Form(min_length=1, max_length=128)],
    file: UploadFile | None = None,
    links: Annotated[str | None, Form()] = None,
    start: Annotated[bool, Form()] = False,
) -> BatchView:
    """上传 Excel 名单或粘贴主页链接建批次；可以同时开始。"""
    content = await file.read() if file else None
    rows, source = await anyio.to_thread.run_sync(
        read_roster, file and file.filename, content, links
    )
    async with database.transaction() as session:
        batch = await create_batch(session, name.strip(), source, rows)
        if start:
            await Flow(session).start(batch.id)
    return BatchView.model_validate(batch)


@router.get("/{batch_id}")
async def get_batch(batch_id: UUID, database: DatabaseDep) -> BatchRow:
    async with database.transaction() as session:
        batches = BatchRepository(session)
        batch = await batches.require(Batch, batch_id)
        return (await _rows(batches, [batch]))[0]


@router.post("/{batch_id}/start")
async def start(batch_id: UUID, database: DatabaseDep) -> BatchView:
    async with database.transaction() as session:
        batch = await Flow(session).start(batch_id)
    return BatchView.model_validate(batch)


@router.post("/{batch_id}/pause")
async def pause(batch_id: UUID, database: DatabaseDep, gateway: GatewayDep) -> BatchAction:
    return await _control(database, gateway, batch_id, "pause", Flow.pause)


@router.post("/{batch_id}/resume")
async def resume(batch_id: UUID, database: DatabaseDep, gateway: GatewayDep) -> BatchAction:
    return await _control(database, gateway, batch_id, "resume", Flow.resume)


@router.post("/{batch_id}/cancel")
async def cancel(batch_id: UUID, database: DatabaseDep, gateway: GatewayDep) -> BatchAction:
    return await _control(database, gateway, batch_id, "cancel", Flow.cancel)


@router.post("/{batch_id}/retry")
async def retry(batch_id: UUID, database: DatabaseDep, gateway: GatewayDep) -> Affected:
    """补跑批次中分析失败和待核查的任务。"""
    async with database.transaction() as session:
        (await BatchRepository(session).require(Batch, batch_id)).reopen()
    content = await gateway.call(ExecutorKind.ANALYSIS, "POST", f"/api/batches/{batch_id}/retry")
    states = job_states.validate_python(content)
    async with database.transaction() as session:
        await Flow(session).retried(batch_id, states)
    return Affected(count=len(states))


@router.post("/{batch_id}/recrawl")
async def recrawl(batch_id: UUID, database: DatabaseDep) -> Affected:
    """重新爬取批次中需要重新抓取的达人。"""
    async with database.transaction() as session:
        return Affected(count=await Flow(session).recrawl_batch(batch_id))


@router.delete("/{batch_id}", status_code=204)
async def delete(batch_id: UUID, database: DatabaseDep) -> None:
    async with database.transaction() as session:
        await Flow(session).delete(batch_id)


@router.get("/{batch_id}/creators")
async def list_creators(
    batch_id: UUID,
    database: DatabaseDep,
    paging: PagingDep,
    status: CreatorStatus | None = None,
    q: str | None = None,
    progress: CreatorProgress | None = None,
) -> Page[CreatorRow]:
    async with database.transaction() as session:
        items, total = await CreatorRepository(session).search(
            batch_id, status, q, paging.offset, paging.size, progress=progress
        )
        works = await WorkRepository(session).of_creators([item.id for item, _ in items])
    works_of: dict[UUID, list[WorkView]] = defaultdict(list)
    for work in works:
        works_of[work.creator_id].append(WorkView.model_validate(work))
    rows = [
        CreatorRow(
            **CreatorView.model_validate(item).model_dump(),
            progress=stage,
            works=works_of.get(item.id, []),
        )
        for item, stage in items
    ]
    return Page(items=rows, total=total)


@router.get("/{batch_id}/export")
async def export(batch_id: UUID, database: DatabaseDep) -> Response:
    """导出交付表；批次进行中也可以导出当前结果。"""
    async with database.transaction() as session:
        batch = await BatchRepository(session).require(Batch, batch_id)
        creators = CreatorRepository(session)
        members = await creators.of_batch(batch.id)
        works = await WorkRepository(session).of_batch(batch.id)
        qr_codes = await creators.qr_of_batch(batch.id)
    works_of = defaultdict(list)
    for work in works:
        works_of[work.creator_id].append(work)
    results = [
        CreatorResult(creator, works_of[creator.id], qr_codes.get(creator.id))
        for creator in members
    ]
    content = await anyio.to_thread.run_sync(build_workbook, results)
    filename = quote(f"{batch.name}-达人标签结果.xlsx")
    return Response(
        content,
        media_type=XLSX,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


async def _rows(batches: BatchRepository, items: list[Batch]) -> list[BatchRow]:
    ids = [item.id for item in items]
    creators, progress = await batches.creator_counts(ids)
    videos = await batches.video_counts(ids)
    return [
        BatchRow(
            **BatchView.model_validate(item).model_dump(),
            creators=creators.get(item.id, {}),
            creator_progress=progress.get(item.id, {}),
            videos=videos.get(item.id, {}),
        )
        for item in items
    ]


async def _control(
    database: Database,
    gateway: Gateway,
    batch_id: UUID,
    action: str,
    change: Callable[[Flow, UUID], Awaitable[Batch]],
) -> BatchAction:
    """先改本地状态，再通知执行程序；通知不到的返回说明，不影响本地状态。"""
    async with database.transaction() as session:
        batch = await change(Flow(session), batch_id)
    warnings = await gateway.broadcast(f"/api/batches/{batch_id}/{action}")
    return BatchAction(batch=BatchView.model_validate(batch), warnings=warnings)

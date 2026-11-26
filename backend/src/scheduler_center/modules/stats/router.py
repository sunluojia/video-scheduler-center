from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Response

from scheduler_center.core.common.clock import BEIJING
from scheduler_center.core.common.errors import Invalid, Rejected, Unavailable
from scheduler_center.core.web.dependencies import ApiSettingsDep, DatabaseDep, PagingDep
from scheduler_center.core.web.schemas import Page
from scheduler_center.modules.batch.models import Batch
from scheduler_center.modules.batch.repository import BatchRepository
from scheduler_center.modules.executor.dependencies import GatewayDep
from scheduler_center.modules.executor.models import ExecutorKind
from scheduler_center.modules.report.stats_workbook import AiUsage, build_stats_workbook
from scheduler_center.modules.stats.crawl_ips import CrawlIpRepository
from scheduler_center.modules.stats.repository import StatsRepository
from scheduler_center.modules.stats.schemas import Bucket, CrawlIpBatch, CrawlIpStats, Stats

router = APIRouter(prefix="/stats", tags=["数据看板"])
MAX_BUCKETS = 800
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass
class Period:
    """统计的时间段，可以只看一个批次。不带时区的时间按北京时间。"""

    since: datetime
    until: datetime
    bucket: Bucket = Bucket.HOUR
    batch_id: UUID | None = None

    def __post_init__(self) -> None:
        self.since, self.until = (
            value if value.tzinfo else value.replace(tzinfo=BEIJING)
            for value in (self.since, self.until)
        )
        if self.until <= self.since:
            raise Invalid("结束时间要晚于开始时间")
        step = 3600 if self.bucket == Bucket.HOUR else 86400
        if (self.until - self.since).total_seconds() / step > MAX_BUCKETS:
            raise Invalid("时间段太长，请改为按天统计或缩短时间段")


PeriodDep = Annotated[Period, Depends()]


@router.get("")
async def get_stats(period: PeriodDep, database: DatabaseDep) -> Stats:
    """各环节在这段时间里的完成数、成功率、用时和失败原因，以及当前的达人和视频分布。

    AI 调用的次数、token 和缓存命中由分析服务的 /api/ai/usage 提供。
    """
    async with database.transaction() as session:
        return await _stats(StatsRepository(session), period)


@router.get("/crawl-ips")
async def get_crawl_ips(
    database: DatabaseDep, settings: ApiSettingsDep, batch_id: UUID | None = None
) -> CrawlIpStats:
    """按批次统计仍在保留期内的采集租约，默认优先选择有租约的运行批次。"""
    async with database.transaction() as session:
        return await CrawlIpRepository(session).stats(batch_id, settings)


@router.get("/crawl-ip-batches")
async def list_crawl_ip_batches(database: DatabaseDep, paging: PagingDep) -> Page[CrawlIpBatch]:
    """采集 IP 看板的可选批次，包含租约明细已清理的历史批次。"""
    async with database.transaction() as session:
        return await CrawlIpRepository(session).batch_page(paging.offset, paging.size)


@router.get("/export")
async def export(period: PeriodDep, database: DatabaseDep, gateway: GatewayDep) -> Response:
    """把这段时间的统计导出为 Excel：概览、分时数据、分析各阶段、用时、失败原因和 AI 调用。"""
    async with database.transaction() as session:
        stats = await _stats(StatsRepository(session), period)
        batch = period.batch_id and await BatchRepository(session).require(Batch, period.batch_id)
    ai = await _ai_usage(gateway, period)
    content = await anyio.to_thread.run_sync(build_stats_workbook, stats, batch, ai)
    start, end = (
        value.astimezone(BEIJING).strftime("%m%d-%H%M") for value in (period.since, period.until)
    )
    filename = quote(f"{batch.name + '-' if batch else ''}数据统计-{start}至{end}.xlsx")
    return Response(
        content,
        media_type=XLSX,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


async def _stats(repository: StatsRepository, period: Period) -> Stats:
    return await repository.stats(period.since, period.until, period.bucket, period.batch_id)


async def _ai_usage(gateway, period: Period) -> AiUsage:
    """分析服务不在线时照样导出，AI 调用表里写明原因。"""
    params: dict[str, Any] = {
        "since": period.since.isoformat(),
        "until": period.until.isoformat(),
        "bucket": period.bucket.value,
    }
    if period.batch_id:
        params["batch_id"] = str(period.batch_id)
    try:
        rows = await gateway.call(ExecutorKind.ANALYSIS, "GET", "/api/ai/usage", params=params)
        models = await gateway.call(ExecutorKind.ANALYSIS, "GET", "/api/ai/models")
    except (Unavailable, Rejected) as error:
        return AiUsage(rows=[], capacity={}, error=str(error))
    return AiUsage(rows=rows, capacity={item["id"]: item["max_concurrency"] for item in models})

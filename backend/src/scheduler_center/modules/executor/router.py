import asyncio
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, Self
from uuid import UUID

import httpx
from fastapi import APIRouter, Request, Response
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    Field,
    StringConstraints,
    TypeAdapter,
    UrlConstraints,
    field_validator,
)

from scheduler_center.core.common.clock import utcnow
from scheduler_center.core.common.errors import Conflict, Unavailable
from scheduler_center.core.web.dependencies import DatabaseDep
from scheduler_center.modules.executor.dependencies import GatewayDep
from scheduler_center.modules.executor.gateway import Gateway
from scheduler_center.modules.executor.models import Executor, ExecutorKind
from scheduler_center.modules.executor.repository import ExecutorRepository
from scheduler_center.modules.identity.repository import IdentityPoolRepository
from scheduler_center.modules.identity.schemas import IdentitySnapshot
from scheduler_center.modules.proxy.repository import LeaseRepository

registration = APIRouter(tags=["执行程序注册"])
"""执行程序调用，使用服务凭据。"""
router = APIRouter(tags=["执行程序"])
"""由调度中心页面调用。"""


class Registration(BaseModel):
    url: str = Field(min_length=1, max_length=512)
    """调度中心访问这个实例的地址。"""
    version: str = Field(max_length=64)
    leases: list[UUID] = Field(default_factory=list, max_length=500)
    """爬取程序正在用的下载线路（租约），随心跳续期；60 秒没有续期即收回。"""
    identities: IdentitySnapshot | None = None
    """爬取程序的身份池快照，账号池页面显示。"""


class ExecutorIn(BaseModel):
    kind: ExecutorKind
    instance_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ]
    url: Annotated[AnyHttpUrl, UrlConstraints(max_length=512)]

    @field_validator("instance_id")
    @classmethod
    def valid_instance_id(cls, value: str) -> str:
        if value in {".", ".."} or any(
            ch in "/\\" or ord(ch) < 32 or ord(ch) == 127 for ch in value
        ):
            raise ValueError("实例 ID 必须是单个有效的路径段")
        return value

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if any(
            part is not None
            for part in (value.username, value.password, value.query, value.fragment)
        ):
            raise ValueError("访问地址仅填写协议、主机、端口和可选路径")
        return value


class ExecutorView(BaseModel):
    kind: ExecutorKind
    instance_id: str
    url: str
    version: str
    registered_at: datetime
    last_seen_at: datetime | None
    withdrawn_at: datetime | None
    online: bool
    crawl_slots: int
    download_slots: int
    warm_slots: int

    @classmethod
    def of(cls, item: Executor, now: datetime, timeout: timedelta) -> Self:
        return cls(
            kind=item.kind,
            instance_id=item.instance_id,
            url=item.url,
            version=item.version,
            registered_at=item.registered_at,
            last_seen_at=item.last_seen_at,
            withdrawn_at=item.withdrawn_at,
            online=item.online(now, timeout),
            crawl_slots=item.crawl_slots,
            download_slots=item.download_slots,
            warm_slots=item.warm_slots,
        )


class Slots(BaseModel):
    crawl_slots: int = Field(ge=1, le=16)
    """同时获取几个达人，每个开一个 Chrome 窗口（约 400 MB）。"""
    download_slots: int = Field(ge=1, le=32)
    """同时下载几条视频。"""
    warm_slots: int | None = Field(default=None, ge=1, le=16)
    """同时预热几个身份。省略时保留当前设置。"""


class RegistrationReply(BaseModel):
    identity_target: int | None = None
    """这台爬取程序要保持的可用身份数：账号池的目标按在线的爬取程序平均分摊，向上取整。
    分析服务和不报身份池的程序没有。"""
    crawl_slots: int | None = None
    download_slots: int | None = None
    warm_slots: int | None = None
    """爬取程序的并发，执行程序页设置；分析服务没有。"""


class CrawlProgress(BaseModel):
    task_id: UUID
    handle: str
    stage: Literal["waiting_line", "profile", "listing"]
    pages_read: int = Field(ge=0)
    works_found: int = Field(ge=0)
    max_works: int = Field(gt=0)


class CrawlerCrawlView(BaseModel):
    instance_id: str
    crawls: list[CrawlProgress] | None
    """旧版本或暂时无法访问的实例返回 null。"""


class DownloadTransfer(BaseModel):
    task_id: UUID
    work_id: str
    stage: Literal["preparing", "downloading", "uploading"]
    received_bytes: int = Field(ge=0)
    total_bytes: int | None = Field(gt=0)


class CrawlerDownloadView(BaseModel):
    instance_id: str
    downloads: list[DownloadTransfer] | None
    """旧版本或暂时无法访问的实例返回 null。"""
    download_bytes_per_second: int | None = Field(default=None, ge=0)
    """该设备最近 5 秒的平均视频下载速度。"""


_CRAWLS = TypeAdapter(list[CrawlProgress])
_DOWNLOADS = TypeAdapter(list[DownloadTransfer])


@registration.put("/{kind}-services/{instance_id}")
async def register(
    kind: ExecutorKind,
    instance_id: str,
    body: Registration,
    request: Request,
    database: DatabaseDep,
) -> RegistrationReply:
    """注册兼心跳，爬取程序和分析服务定期调用。"""
    now = utcnow()
    async with database.transaction() as session:
        executors = ExecutorRepository(session)
        snapshot = body.identities and body.identities.model_dump(mode="json")
        await executors.heartbeat(kind, instance_id, body.url, body.version, now, snapshot)
        await LeaseRepository(session).renew(instance_id, body.leases, now)
        if kind != ExecutorKind.CRAWLER:
            return RegistrationReply()
        item = await executors.require(Executor, (kind, instance_id))
        pool = IdentityPoolRepository(session)
        return RegistrationReply(
            identity_target=(
                await pool.share(now, _heartbeat_timeout(request)) if snapshot else None
            ),
            crawl_slots=item.crawl_slots,
            download_slots=item.download_slots,
            warm_slots=item.warm_slots,
        )


@registration.delete("/{kind}-services/{instance_id}", status_code=204)
async def withdraw(kind: ExecutorKind, instance_id: str, database: DatabaseDep) -> None:
    async with database.transaction() as session:
        await ExecutorRepository(session).withdraw(kind, instance_id, utcnow())


@router.get("/executors")
async def list_executors(request: Request, database: DatabaseDep) -> list[ExecutorView]:
    timeout = _heartbeat_timeout(request)
    now = utcnow()
    async with database.transaction() as session:
        executors = await ExecutorRepository(session).all()
    return [ExecutorView.of(item, now, timeout) for item in executors]


@router.post("/executors", status_code=201)
async def add_executor(body: ExecutorIn, request: Request, database: DatabaseDep) -> ExecutorView:
    """登记一个等待首次心跳的执行程序。"""
    now = utcnow()
    item = Executor(
        kind=body.kind,
        instance_id=body.instance_id,
        url=str(body.url).rstrip("/"),
        version="",
        registered_at=now,
        last_seen_at=None,
    )
    async with database.transaction() as session:
        if not await ExecutorRepository(session).add_pending(item):
            raise Conflict("该类型下已有相同实例 ID 的执行程序")
    return ExecutorView.of(item, now, _heartbeat_timeout(request))


@router.delete("/executors/{kind}/{instance_id}", status_code=204)
async def remove_executor(
    kind: ExecutorKind, instance_id: str, request: Request, database: DatabaseDep
) -> None:
    """删除离线或尚未发送心跳的实例记录。"""
    async with database.transaction() as session:
        repo = ExecutorRepository(session)
        item = await repo.require(Executor, (kind, instance_id), lock=True)
        if item.online(utcnow(), _heartbeat_timeout(request)):
            raise Conflict("执行程序仍在线，请先停止程序，等它离线后再删除")
        await repo.delete(item)


@router.put("/executors/crawler/{instance_id}/slots")
async def set_slots(
    instance_id: str, body: Slots, request: Request, database: DatabaseDep
) -> ExecutorView:
    """改爬取程序的并发；它下一次心跳（15 秒内）拿到，不用重启。"""
    async with database.transaction() as session:
        repo = ExecutorRepository(session)
        item = await repo.require(Executor, (ExecutorKind.CRAWLER, instance_id), lock=True)
        item.crawl_slots, item.download_slots = body.crawl_slots, body.download_slots
        if body.warm_slots is not None:
            item.warm_slots = body.warm_slots
    return ExecutorView.of(item, utcnow(), _heartbeat_timeout(request))


@router.get("/executors/{kind}/status")
async def executor_status(kind: ExecutorKind, gateway: GatewayDep) -> Any:
    """执行程序的运行状态：排队、进行中、额度占用等，内容由执行程序决定。"""
    return await gateway.call(kind, "GET", "/api/status")


@router.get("/executors/crawler/crawl-progress")
async def crawler_crawl_progress(gateway: GatewayDep) -> list[CrawlerCrawlView]:
    """汇总在线爬取设备的达人资料和作品列表获取进度。"""
    online = await gateway.online(ExecutorKind.CRAWLER)

    async def read(executor: Executor) -> CrawlerCrawlView:
        try:
            response = await _request_progress(gateway, executor, "/api/crawl-progress")
            crawls = _CRAWLS.validate_json(response.content)
        except Unavailable, TimeoutError, httpx.HTTPStatusError, ValueError:
            crawls = None
        return CrawlerCrawlView(instance_id=executor.instance_id, crawls=crawls)

    return await asyncio.gather(*(read(executor) for executor in online))


@router.get("/executors/crawler/download-progress")
async def crawler_download_progress(gateway: GatewayDep) -> list[CrawlerDownloadView]:
    """汇总在线爬取设备的下载进度；一台设备暂不可用时继续显示其他设备。"""
    online = await gateway.online(ExecutorKind.CRAWLER)

    async def read(executor: Executor) -> CrawlerDownloadView:
        try:
            response = await _request_progress(gateway, executor, "/api/download-progress")
            downloads = _DOWNLOADS.validate_json(response.content)
        except Unavailable, TimeoutError, httpx.HTTPStatusError, ValueError:
            return CrawlerDownloadView(instance_id=executor.instance_id, downloads=None)
        try:
            rate = int(response.headers["X-Download-Bytes-Per-Second"])
        except KeyError, ValueError:
            rate = -1
        return CrawlerDownloadView(
            instance_id=executor.instance_id,
            downloads=downloads,
            download_bytes_per_second=rate if rate >= 0 else None,
        )

    return await asyncio.gather(*(read(executor) for executor in online))


@router.api_route(
    "/ai/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def ai_config(path: str, request: Request, gateway: GatewayDep) -> Response:
    """AI 供应商、模型、路由和提示词由分析服务管理，原样转发；接口说明见分析服务的文档。"""
    response = await gateway.send(
        ExecutorKind.ANALYSIS,
        request.method,
        f"/api/ai/{path}",
        params=request.query_params,
        content=await request.body(),
        headers={"Content-Type": request.headers.get("content-type", "application/json")},
    )
    return Response(
        response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type"),
    )


def _heartbeat_timeout(request: Request) -> timedelta:
    return timedelta(seconds=request.app.state.settings.heartbeat_timeout_seconds)


async def _request_progress(gateway: Gateway, executor: Executor, path: str) -> httpx.Response:
    async with asyncio.timeout(2):
        response = await gateway.request(executor, "GET", path)
    return response.raise_for_status()

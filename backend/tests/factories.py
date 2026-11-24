"""测试共用的配置、数据准备、事件构造与结果读取。"""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from dramatiq.brokers.stub import StubBroker
from pydantic import SecretStr
from sqlalchemy import select

from scheduler_center.core.common.clock import utcnow
from scheduler_center.core.config.settings import ApiSettings, WorkerSettings
from scheduler_center.core.database.engine import Database
from scheduler_center.core.messaging.outbox import outbox
from scheduler_center.entrypoints.worker import Worker
from scheduler_center.integrations.mq.publisher import OutboxPublisher
from scheduler_center.modules.analysis.messages import analysis_event
from scheduler_center.modules.batch.models import Batch, Creator, Work
from scheduler_center.modules.batch.repository import CreatorRepository, WorkRepository
from scheduler_center.modules.batch.service import create_batch
from scheduler_center.modules.crawl.messages import crawler_event
from scheduler_center.modules.executor.gateway import Gateway
from scheduler_center.modules.executor.models import ExecutorKind
from scheduler_center.modules.executor.repository import ExecutorRepository
from scheduler_center.modules.flow.service import Flow
from scheduler_center.modules.report.importer import read_links

SERVICE_TOKEN = "test-service-token"
CRAWLER_URL, ANALYSIS_URL = "http://crawler.test", "http://analysis.test"


def api_settings(**values) -> ApiSettings:
    return ApiSettings(
        **{"service_token": SecretStr(SERVICE_TOKEN), "static_root": "missing-frontend", **values}
    )


def build_worker(
    database: Database,
    database_url: str,
    tmp_path,
    executors,
    *,
    legacy_crawler_dispatch: bool = True,
) -> Worker:
    """Worker 访问执行程序时走模拟的 HTTP 接口。"""
    settings = WorkerSettings(
        database_url=database_url,
        material_root=tmp_path / "materials",
        service_token=SecretStr(SERVICE_TOKEN),
        legacy_crawler_dispatch=legacy_crawler_dispatch,
    )
    timeout = timedelta(seconds=settings.heartbeat_timeout_seconds)
    gateway = Gateway(database, SERVICE_TOKEN, timeout, httpx.MockTransport(executors.handle))
    return Worker(
        settings,
        database,
        OutboxPublisher(database, StubBroker(), legacy_crawler_dispatch=legacy_crawler_dispatch),
        gateway,
    )


class FakeExecutors:
    """模拟爬取程序和分析服务的 HTTP 接口：记录收到的请求，按方法和路径返回预设的响应。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.responses: dict[tuple[str, str], httpx.Response] = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.get((request.method, request.url.path)) or httpx.Response(204)

    def calls(self, host: str) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests if r.url.host == host]


async def register(
    database: Database,
    kind: ExecutorKind,
    url: str,
    seen: datetime | None = None,
    instance_id: str | None = None,
):
    async with database.transaction() as session:
        await ExecutorRepository(session).heartbeat(
            kind, instance_id or f"{kind}-1", url, "1.0", seen or utcnow()
        )


async def new_batch(
    database: Database, *handles: str, start: bool = True, legacy_crawler_dispatch: bool = True
) -> Batch:
    async with database.transaction() as session:
        batch = await create_batch(session, "测试批次", "粘贴链接", read_links("\n".join(handles)))
        if start:
            await Flow(session, legacy_crawler_dispatch=legacy_crawler_dispatch).start(batch.id)
    return batch


async def creators(database: Database, batch: Batch) -> list[Creator]:
    async with database.transaction() as session:
        return await CreatorRepository(session).of_batch(batch.id)


async def works(database: Database, creator: Creator) -> list[Work]:
    async with database.transaction() as session:
        return await WorkRepository(session).of_creator(creator.id)


async def load[T](database: Database, entity: type[T], key: Any) -> T:
    async with database.transaction() as session:
        return await session.get(entity, key)


async def messages(database: Database, actor: str | None = None) -> list[dict]:
    """写入发件箱的任务，按写入顺序。"""
    stmt = select(outbox.c.actor, outbox.c.payload).order_by(outbox.c.id)
    async with database.engine.connect() as connection:
        rows = (await connection.execute(stmt)).all()
    return [payload for name, payload in rows if actor in (None, name)]


def at(seconds: float = 0) -> str:
    return (datetime(2026, 11, 17, tzinfo=UTC) + timedelta(seconds=seconds)).isoformat()


def crawler_message(task_id: str | UUID, type_: str, run=1, **fields):
    return crawler_event.validate_python(
        {
            "event_id": str(uuid4()),
            "task_id": str(task_id),
            "run": run,
            "type": type_,
            "occurred_at": at(),
            **fields,
        }
    )


async def crawler_says(
    database: Database,
    task_id: str | UUID,
    type_: str,
    run=1,
    *,
    legacy_crawler_dispatch: bool = True,
    **fields,
):
    event = crawler_message(task_id, type_, run, **fields)
    async with database.transaction() as session:
        await Flow(session, legacy_crawler_dispatch=legacy_crawler_dispatch).on_crawler_event(event)
    return event


def video(position: int, work_id: str | None = None) -> dict:
    work_id = work_id or f"7{position:018d}"
    return {
        "position": position,
        "work_id": work_id,
        "url": f"https://www.tiktok.com/@alice/video/{work_id}",
        "type": "video",
        "description": f"第 {position} 条",
        "topics": ["#makeup"],
    }


def photo(position: int) -> dict:
    return {**video(position), "type": "photo"}


async def crawl_task(database: Database, creator: Creator, run: int = 1) -> str:
    [message] = [
        message
        for message in await messages(database, "crawl_creator")
        if message["creator"]["handle"] == creator.handle and message["run"] == run
    ]
    return message["task_id"]


async def crawl(database: Database, creator: Creator, *items: dict, run: int = 1) -> None:
    """爬取程序回传锁定的作品清单。"""
    task_id = await crawl_task(database, creator, run)
    await crawler_says(database, task_id, "crawled", run=run, conclusion="ok", works=list(items))


async def analysis_says(database: Database, payload: dict) -> None:
    event = analysis_event.validate_python({"event_id": str(uuid4()), **payload})
    async with database.transaction() as session:
        await Flow(session).on_analysis_event(event)


def job_event(job_id: str, status: str, *, run=1, seconds=1, kind="video", **fields) -> dict:
    return {
        "type": "job",
        "job_id": job_id,
        "run": run,
        "kind": kind,
        "status": status,
        "occurred_at": at(seconds),
        **fields,
    }


def recognized(*tags: str) -> dict:
    return {
        "listening": {"sounds": ["speech"], "transcript": "大家好"},
        "recognition": {
            "tags": list(tags),
            "products": [],
            "summary": "",
            "topic_translations": [],
        },
    }


def content(text: str = "video") -> tuple[bytes, dict[str, str]]:
    """视频内容和上传时的请求头。"""
    data = text.encode()
    return data, {"X-Content-SHA256": hashlib.sha256(data).hexdigest()}


async def upload(service: httpx.AsyncClient, task_id: str, run: int = 1, text: str = "video"):
    data, headers = content(text)
    return await service.put(
        f"/api/download-tasks/{task_id}/runs/{run}/video", content=data, headers=headers
    )

"""转发到执行程序：浏览器不直接访问爬取程序和分析服务，由后端带服务凭据调用。"""

import asyncio
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

import httpx

from scheduler_center.core.common.clock import utcnow
from scheduler_center.core.common.errors import Rejected, Unavailable
from scheduler_center.core.database.engine import Database
from scheduler_center.modules.executor.models import Executor, ExecutorKind
from scheduler_center.modules.executor.repository import ExecutorRepository


class Gateway:
    def __init__(
        self,
        database: Database,
        token: str,
        heartbeat_timeout: timedelta,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._database = database
        self._heartbeat_timeout = heartbeat_timeout
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, timeout=30, transport=transport
        )

    async def send(self, kind: ExecutorKind, method: str, path: str, **options) -> httpx.Response:
        """发给该类最近有心跳的实例，返回原始响应。"""
        async with self._database.transaction() as session:
            executor = await ExecutorRepository(session).latest(kind)
        if executor is None:
            raise Unavailable(f"{kind.label}不在线")
        return await self.request(executor, method, path, **options)

    async def request(
        self, executor: Executor, method: str, path: str, **options
    ) -> httpx.Response:
        """发给指定的实例，返回原始响应。"""
        if not executor.online(utcnow(), self._heartbeat_timeout):
            raise Unavailable(f"{executor.kind.label} {executor.instance_id} 不在线")
        try:
            return await self._client.request(method, executor.url.rstrip("/") + path, **options)
        except httpx.HTTPError as error:
            raise Unavailable(f"{executor.kind.label}无法访问：{error}") from error

    async def call(self, kind: ExecutorKind, method: str, path: str, **options) -> Any:
        """控制和查询：出错时报告原因，成功时返回 JSON 内容。"""
        return _content(kind, await self.send(kind, method, path, **options))

    async def call_on(self, executor: Executor, method: str, path: str) -> Any:
        """同上，发给指定的实例。"""
        return _content(executor.kind, await self.request(executor, method, path))

    async def online(self, kind: ExecutorKind) -> list[Executor]:
        """该类全部在线的实例。"""
        now = utcnow()
        async with self._database.transaction() as session:
            return [
                item
                for item in await ExecutorRepository(session).all()
                if item.kind == kind and item.online(now, self._heartbeat_timeout)
            ]

    async def broadcast(self, path: str, kinds: Iterable[ExecutorKind]) -> list[str]:
        """暂停、继续、取消批次时通知每个在线实例；返回没能送达的说明。"""

        async def notify(executor: Executor) -> str | None:
            try:
                await self.call_on(executor, "POST", path)
            except (Unavailable, Rejected) as error:
                return f"{error}（{executor.instance_id}）"
            return None

        warnings = []
        for kind in kinds:
            targets = await self.online(kind)
            if not targets:
                warnings.append(f"{kind.label}不在线")
            results = await asyncio.gather(*(notify(executor) for executor in targets))
            warnings += [warning for warning in results if warning]
        return warnings

    async def aclose(self) -> None:
        await self._client.aclose()


def _content(kind: ExecutorKind, response: httpx.Response) -> Any:
    if response.is_error:
        raise Rejected(f"{kind.label}返回 {response.status_code}：{_detail(response)}")
    return response.json() if response.content else None


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", response.text))
    except ValueError, AttributeError:
        return response.text

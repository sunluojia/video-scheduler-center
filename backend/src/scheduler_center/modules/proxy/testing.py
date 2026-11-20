"""代理测试和测速：交给在线的爬取程序执行，访问代理的请求都从设备的网络发出，与实际使用时一致。"""

import asyncio
import logging
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from scheduler_center.core.common.clock import utcnow
from scheduler_center.core.common.errors import Rejected, Unavailable
from scheduler_center.core.config.settings import Settings
from scheduler_center.core.database.engine import Database
from scheduler_center.modules.executor.gateway import Gateway
from scheduler_center.modules.executor.models import Executor, ExecutorKind
from scheduler_center.modules.executor.repository import ExecutorRepository
from scheduler_center.modules.live.notifier import Live
from scheduler_center.modules.proxy.messages import (
    CheckItem,
    CheckReply,
    CheckRequest,
    SpeedReply,
    SpeedRequest,
)
from scheduler_center.modules.proxy.models import ProxyStatus
from scheduler_center.modules.proxy.repository import ProxyRepository

log = logging.getLogger(__name__)
BATCH = 20
"""每台设备一次测试的代理数。"""
RETRY = timedelta(seconds=60)
"""设备没能完成测试时，过这么久再交给设备。"""
TEST_CLAIM = timedelta(minutes=3)
"""领走的测试最多等这么久（设备测一批最多 90 秒）；Worker 中途退出时，到期由别的 Worker 重新领。"""
SPEED_STREAMS = 4
"""测速时先单路下载一次，再这么多路同时下载，看一条线路能同时扛住多少下载。"""
SPEED_TIMEOUT = timedelta(minutes=5)
"""一次测速最多等这么久；慢的代理单路加并发要一两分钟。"""
replies = TypeAdapter(list[CheckReply])


async def run_tests(
    gateway: Gateway, device: Executor, echo_url: str, items: list[CheckItem]
) -> list[CheckReply]:
    """请设备测试一批代理；设备不在线或出错时报告原因。"""
    body = CheckRequest(echo_url=echo_url, items=items).model_dump(mode="json")
    response = await gateway.request(device, "POST", "/api/proxy-tests", json=body, timeout=90)
    if response.is_error:
        raise Rejected(f"{device.instance_id} 测试代理返回 {response.status_code}")
    try:
        return replies.validate_python(response.json())
    except (ValueError, ValidationError) as error:
        raise Rejected(f"{device.instance_id} 返回的测试结果无法识别") from error


class ProxyTester:
    """Worker 定期把到了测试或测速时间的代理分给在线设备。

    新添加、改过、手动测试、不通后到期复测和刷新出口的代理都会到测试时间；
    测速只在页面上点了才做。
    """

    def __init__(self, database: Database, gateway: Gateway, settings: Settings) -> None:
        self._database = database
        self._gateway = gateway
        self._settings = settings
        self._heartbeat_timeout = timedelta(seconds=settings.heartbeat_timeout_seconds)

    async def test_due(self) -> int:
        now = utcnow()
        async with self._database.transaction() as session:
            devices = await self._online(session, now)
            if not devices:
                return 0
            due = await ProxyRepository(session).due_tests(now, BATCH * len(devices))
            items = [
                CheckItem.of(proxy, refresh=proxy.status == ProxyStatus.REFRESHING) for proxy in due
            ]
            claim = now + TEST_CLAIM
            for proxy in due:
                proxy.testing_until = claim
        if not items:
            return 0
        shares = [(device, items[index :: len(devices)]) for index, device in enumerate(devices)]
        shares = [(device, share) for device, share in shares if share]
        results = await asyncio.gather(*(self._run(device, share) for device, share in shares))
        async with self._database.transaction() as session:
            found = {
                str(proxy.id): proxy
                for proxy in await ProxyRepository(session).of_ids(
                    [UUID(item.key) for item in items], lock=True
                )
            }
            finished = utcnow()
            for (device, share), result in zip(shares, results, strict=True):
                replied = {reply.key: reply for reply in result}
                for item in share:
                    proxy, reply = found.get(item.key), replied.get(item.key)
                    if not proxy or proxy.testing_until != claim:
                        continue  # 删了、测试期间改过配置或被别的 Worker 重新领走：这份结果不算
                    if reply:
                        proxy.tested(
                            reply.result,
                            finished,
                            device.instance_id,
                            protocol=reply.protocol,
                            exit_ip=reply.exit_ip,
                            latency_ms=reply.latency_ms,
                            host_key=reply.host_key,
                            error=reply.error,
                        )
                    else:
                        proxy.test_at, proxy.testing_until = finished + RETRY, None
            await Live(session).proxies_changed()
        return len(items)

    async def speed_due(self) -> int:
        """每台在线设备一次只测一个代理：同一台设备上同时测会互相抢带宽，测不准。"""
        now = utcnow()
        async with self._database.transaction() as session:
            devices = await self._online(session, now)
            due = await ProxyRepository(session).due_speed_tests(now, len(devices))
            pairs = [
                (device, CheckItem.of(proxy, refresh=False))
                for device, proxy in zip(devices, due, strict=False)
            ]
            claim = now + SPEED_TIMEOUT  # 测速期间页面仍显示在测；Worker 中途退出时到期重测
            for proxy in due:
                proxy.speed_at = claim
        if not pairs:
            return 0
        results = await asyncio.gather(*(self._speed(device, item) for device, item in pairs))
        async with self._database.transaction() as session:
            found = {
                str(proxy.id): proxy
                for proxy in await ProxyRepository(session).of_ids(
                    [UUID(item.key) for _, item in pairs], lock=True
                )
            }
            finished = utcnow()
            for (_, item), reply in zip(pairs, results, strict=True):
                proxy = found.get(item.key)
                if not proxy or proxy.speed_at != claim:
                    continue  # 删了、测速期间改过配置或被重新领走：这份结果不算
                if reply:
                    proxy.speed_tested(
                        finished, reply.single_mbps, reply.parallel_mbps, reply.error
                    )
                else:
                    proxy.speed_at = finished + RETRY
            await Live(session).proxies_changed()
        return len(pairs)

    async def _online(self, session: AsyncSession, now: datetime) -> list[Executor]:
        return [
            executor
            for executor in await ExecutorRepository(session).all()
            if executor.kind == ExecutorKind.CRAWLER
            and executor.online(now, self._heartbeat_timeout)
        ]

    async def _run(self, device: Executor, items: list[CheckItem]) -> list[CheckReply]:
        try:
            return await run_tests(self._gateway, device, self._settings.proxy_echo_url, items)
        except (Unavailable, Rejected) as error:
            log.warning("测试代理没有完成，稍后重试：%s", error)
            return []

    async def _speed(self, device: Executor, item: CheckItem) -> SpeedReply | None:
        settings = self._settings
        body = SpeedRequest(
            url=settings.proxy_speed_url,
            size=settings.proxy_speed_size,
            streams=SPEED_STREAMS,
            item=item,
        )
        try:
            response = await self._gateway.request(
                device,
                "POST",
                "/api/proxy-speed",
                json=body.model_dump(mode="json"),
                timeout=SPEED_TIMEOUT.total_seconds(),
            )
            if response.is_error:
                raise Rejected(f"{device.instance_id} 测速返回 {response.status_code}")
            return SpeedReply.model_validate(response.json())
        except (Unavailable, Rejected, ValueError) as error:  # 校验失败也是 ValueError
            log.warning("测速没有完成，稍后重试：%s", error)
            return None

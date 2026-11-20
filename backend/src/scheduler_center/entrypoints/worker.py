"""Worker：投递发件箱、对账、判断批次完成、测试代理、清理原视频和线路记录。可以启动多个实例。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

from scheduler_center.core.common.clock import utcnow
from scheduler_center.core.config.logging_setup import configure_logging
from scheduler_center.core.config.settings import WorkerSettings
from scheduler_center.core.database.engine import Database
from scheduler_center.integrations.mq.broker import create_broker
from scheduler_center.integrations.mq.publisher import BATCH, OutboxPublisher
from scheduler_center.modules.batch.repository import BatchRepository
from scheduler_center.modules.executor.gateway import Gateway
from scheduler_center.modules.executor.models import ExecutorKind
from scheduler_center.modules.executor.repository import ExecutorRepository
from scheduler_center.modules.flow.reconcile import (
    republish_analysis_jobs,
    republish_crawler_tasks,
)
from scheduler_center.modules.flow.service import Flow
from scheduler_center.modules.material.repository import MaterialRepository
from scheduler_center.modules.material.store import MaterialStore
from scheduler_center.modules.proxy.repository import LeaseRepository
from scheduler_center.modules.proxy.testing import ProxyTester
from scheduler_center.modules.tables import metadata  # noqa: F401  加载全部表映射

log = logging.getLogger(__name__)
UPLOAD_GRACE_SECONDS = 600
"""刚写入、可能还没登记的文件不清理。"""
LEASE_RETENTION = timedelta(hours=24)
"""线路租约同时是线路表现的记录，保留 24 小时。"""


class Worker:
    def __init__(
        self,
        settings: WorkerSettings,
        database: Database,
        publisher: OutboxPublisher,
        gateway: Gateway,
    ):
        self._settings = settings
        self._database = database
        self._publisher = publisher
        self._store = MaterialStore(settings.material_root)
        self.proxies = ProxyTester(database, gateway, settings)

    async def run(self) -> None:
        settings = self._settings
        async with asyncio.TaskGroup() as group:
            group.create_task(self._publish())
            group.create_task(_every(5, self.complete_batches))
            group.create_task(_every(5, self.test_proxies))
            group.create_task(_every(5, self.speed_test_proxies))
            group.create_task(_every(60, self.reconcile))
            group.create_task(_every(settings.cleanup_interval_minutes * 60, self.clean_up))

    async def _publish(self) -> None:
        """有积压时连续投递，否则按间隔轮询。"""
        while True:
            try:
                if await self._publisher.publish_pending() == BATCH:
                    continue
            except Exception as error:
                log.error("投递任务失败，任务保留在发件箱，稍后重试", exc_info=error)
            await asyncio.sleep(self._settings.poll_interval_seconds)

    async def complete_batches(self) -> None:
        async with self._database.transaction() as session:
            running = await BatchRepository(session).running_ids()
        for batch_id in running:
            async with self._database.transaction() as session:
                await Flow(session).complete_if_done(batch_id)

    async def reconcile(self) -> None:
        """执行程序在线时，重发长时间没有受理的任务；离线时等它回来再说。"""
        now = utcnow()
        before = now - timedelta(minutes=self._settings.reconcile_after_minutes)
        timeout = timedelta(seconds=self._settings.heartbeat_timeout_seconds)
        republish = {
            ExecutorKind.CRAWLER: republish_crawler_tasks,
            ExecutorKind.ANALYSIS: republish_analysis_jobs,
        }
        async with self._database.transaction() as session:
            executors = ExecutorRepository(session)
            for kind, action in republish.items():
                executor = await executors.latest(kind)
                if not executor or not executor.online(now, timeout):
                    continue
                if count := await action(session, before):
                    log.warning("%s有 %d 个任务长时间未受理，已重发", kind.label, count)

    async def test_proxies(self) -> None:
        """新添加、改过、手动测试、不通后到期复测和刷新的代理，交给在线设备测试。"""
        if count := await self.proxies.test_due():
            log.info("测试了 %d 个代理", count)

    async def speed_test_proxies(self) -> None:
        """页面上点了测速的代理，交给在线设备测速；测速慢，单独一个循环，不耽误测试。"""
        if count := await self.proxies.speed_due():
            log.info("测速了 %d 个代理", count)

    async def clean_up(self) -> None:
        async with self._database.transaction() as session:
            live = await MaterialRepository(session).live_ids()
            await LeaseRepository(session).purge(utcnow() - LEASE_RETENTION)
        removed = await asyncio.to_thread(self._store.sweep, live, UPLOAD_GRACE_SECONDS)
        if removed:
            log.info("清理了 %d 个不再需要的原视频文件", removed)


async def _every(seconds: float, job: Callable[[], Awaitable[None]]) -> None:
    while True:
        try:
            await job()
        except Exception as error:
            log.error("定时任务 %s 失败，稍后重试", job.__name__, exc_info=error)
        await asyncio.sleep(seconds)


async def run(settings: WorkerSettings) -> None:
    database = Database(settings.database_url)
    publisher = OutboxPublisher(database, create_broker(settings))
    timeout = timedelta(seconds=settings.heartbeat_timeout_seconds)
    gateway = Gateway(database, settings.service_token.get_secret_value(), timeout)
    try:
        await Worker(settings, database, publisher, gateway).run()
    finally:
        await gateway.aclose()
        await database.dispose()


def main() -> None:
    # noinspection PyArgumentList
    settings = WorkerSettings()  # 服务凭据从环境变量读取
    configure_logging(settings.log_level)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()

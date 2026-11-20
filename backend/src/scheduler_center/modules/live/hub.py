"""API 进程内的实时更新：监听数据库通知，转给各个页面连接。"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from sqlalchemy import make_url

from scheduler_center.modules.live.notifier import CHANNEL

log = logging.getLogger(__name__)
EVERYTHING = "*"
"""可能漏掉了通知（例如监听重连），页面应整体刷新。"""


class Subscription:
    """一个页面连接：积攒发生变化的批次，页面取走时合并成一次推送。"""

    def __init__(self) -> None:
        self._changed: set[str] = set()
        self._ready = asyncio.Event()

    def push(self, batch_id: str) -> None:
        self._changed.add(batch_id)
        self._ready.set()

    async def next(self) -> list[str]:
        await self._ready.wait()
        self._ready.clear()
        changed, self._changed = self._changed, set()
        return sorted(changed)


class LiveHub:
    def __init__(self, database_url: str) -> None:
        self._dsn = (
            make_url(database_url)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
        self._subscriptions: set[Subscription] = set()

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[Subscription]:
        subscription = Subscription()
        self._subscriptions.add(subscription)
        try:
            yield subscription
        finally:
            self._subscriptions.discard(subscription)

    async def run(self) -> None:
        """保持一个监听连接；断开后重连，并让页面整体刷新一次。"""
        while True:
            try:
                connection = await asyncpg.connect(self._dsn)
                try:
                    await connection.add_listener(CHANNEL, self._received)
                    self._broadcast(EVERYTHING)
                    while True:
                        await asyncio.sleep(30)
                        await connection.fetchval("SELECT 1")
                finally:
                    await connection.close(timeout=5)
            except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as error:
                log.warning("实时更新的数据库监听断开，稍后重连：%s", error)
            await asyncio.sleep(5)

    def _received(self, _connection, _pid, _channel, payload: str) -> None:
        self._broadcast(json.loads(payload)["batch_id"])

    def _broadcast(self, batch_id: str) -> None:
        for subscription in self._subscriptions:
            subscription.push(batch_id)

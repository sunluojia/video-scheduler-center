from datetime import datetime, timedelta
from math import ceil

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from scheduler_center.core.database.repository import Repository
from scheduler_center.modules.executor.models import ExecutorKind
from scheduler_center.modules.executor.repository import ExecutorRepository
from scheduler_center.modules.identity.tables import identity_pool

DEFAULT_TARGET = 8
"""没设置过时账号池至少保持 8 个可用，约等于一台设备同时获取作品和下载的数量。"""


class IdentityPoolRepository(Repository):
    async def target(self) -> int:
        return await self._session.scalar(select(identity_pool.c.target)) or DEFAULT_TARGET

    async def set_target(self, target: int, now: datetime) -> None:
        stmt = insert(identity_pool).values(id=1, target=target, updated_at=now)
        stmt = stmt.on_conflict_do_update(
            index_elements=[identity_pool.c.id], set_={"target": target, "updated_at": now}
        )
        await self._session.execute(stmt)

    async def share(self, now: datetime, timeout: timedelta) -> int:
        """每台在线的爬取程序要保持几个：账号池目标平均分摊，向上取整。"""
        devices = [
            item
            for item in await ExecutorRepository(self._session).all()
            if item.kind == ExecutorKind.CRAWLER and item.identities and item.online(now, timeout)
        ]
        return ceil(await self.target() / max(len(devices), 1))

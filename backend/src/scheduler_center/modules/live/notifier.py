"""页面实时更新：数据变化时在同一事务里发出 PostgreSQL 通知，提交后送达 API 进程。"""

import json
from uuid import UUID

from sqlalchemy import func, select

from scheduler_center.core.database.repository import Repository

CHANNEL = "scheduler_live"


class Live(Repository):
    async def changed(self, batch_id: UUID, creator_id: UUID | None = None) -> None:
        await self._notify(
            {"batch_id": str(batch_id), "creator_id": creator_id and str(creator_id)}
        )

    async def proxies_changed(self) -> None:
        """代理或线路的状态变了：页面同样整体刷新。"""
        await self._notify({"batch_id": "proxies"})

    async def _notify(self, payload: dict) -> None:
        await self._session.execute(select(func.pg_notify(CHANNEL, json.dumps(payload))))

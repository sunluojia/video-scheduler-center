from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from scheduler_center.core.common.errors import NotFound


class Repository:
    """各业务模块仓储的公共操作；同一事务内的仓储共用一个会话。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, entity: object) -> None:
        self._session.add(entity)

    async def get[T](self, entity: type[T], key: Any, *, lock: bool = False) -> T | None:
        """加锁读取时重新载入行，拿到的是加锁之后的最新数据。

        业务不修改主键，行锁都用 FOR NO KEY UPDATE：它与外键检查的 FOR KEY SHARE 兼容，
        锁住批次时，其他事务照样能写入引用该批次的行，不会因加锁顺序不同而死锁。
        """
        return await self._session.get(
            entity,
            key,
            with_for_update={"key_share": True} if lock else None,
            populate_existing=lock,
        )

    async def require[T](self, entity: type[T], key: Any, *, lock: bool = False) -> T:
        found = await self.get(entity, key, lock=lock)
        if found is None:
            raise NotFound(f"{entity.__name__} {key} 不存在")
        return found

    async def delete(self, entity: object) -> None:
        await self._session.delete(entity)

    async def _page[T](
        self, stmt: Select[tuple[T]], offset: int, limit: int
    ) -> tuple[list[T], int]:
        """一页记录和符合条件的总数。"""
        total = await self._session.scalar(select(func.count()).select_from(stmt.subquery()))
        items = list(await self._session.scalars(stmt.offset(offset).limit(limit)))
        return items, total or 0

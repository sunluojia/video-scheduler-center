from datetime import datetime

from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert

from scheduler_center.core.database.repository import Repository
from scheduler_center.modules.executor.models import Executor, ExecutorKind
from scheduler_center.modules.executor.tables import executors


class ExecutorRepository(Repository):
    async def add_pending(self, executor: Executor) -> bool:
        """手动登记时保留已有实例，等执行程序自己发送首次心跳。"""
        stmt = (
            insert(executors)
            .values(
                kind=executor.kind,
                instance_id=executor.instance_id,
                url=executor.url,
                version=executor.version,
                registered_at=executor.registered_at,
                last_seen_at=None,
            )
            .on_conflict_do_nothing(index_elements=[executors.c.kind, executors.c.instance_id])
            .returning(executors.c.instance_id)
        )
        return await self._session.scalar(stmt) is not None

    async def heartbeat(
        self,
        kind: ExecutorKind,
        instance_id: str,
        url: str,
        version: str,
        now: datetime,
        identities: dict | None = None,
    ) -> None:
        """注销后再次心跳视为重新注册。"""
        stmt = insert(executors).values(
            kind=kind,
            instance_id=instance_id,
            url=url,
            version=version,
            registered_at=now,
            last_seen_at=now,
            identities=identities,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[executors.c.kind, executors.c.instance_id],
            set_={
                "url": url,
                "version": version,
                "last_seen_at": now,
                "identities": identities,
                "withdrawn_at": None,
                "registered_at": case(
                    (executors.c.withdrawn_at.is_(None), executors.c.registered_at), else_=now
                ),
            },
        )
        await self._session.execute(stmt)

    async def withdraw(self, kind: ExecutorKind, instance_id: str, now: datetime) -> None:
        await self._session.execute(
            update(executors)
            .where(executors.c.kind == kind, executors.c.instance_id == instance_id)
            .values(withdrawn_at=now)
        )

    async def all(self) -> list[Executor]:
        stmt = select(Executor).order_by(executors.c.kind, executors.c.instance_id)
        return list(await self._session.scalars(stmt))

    async def latest(self, kind: ExecutorKind) -> Executor | None:
        """该类执行程序中最近一次心跳的在册实例。"""
        stmt = (
            select(Executor)
            .where(
                executors.c.kind == kind,
                executors.c.withdrawn_at.is_(None),
                executors.c.last_seen_at.is_not(None),
            )
            .order_by(executors.c.last_seen_at.desc())
            .limit(1)
        )
        return await self._session.scalar(stmt)

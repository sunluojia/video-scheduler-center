from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Text, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.dialects.postgresql import insert as upsert

from scheduler_center.core.database.repository import Repository
from scheduler_center.modules.proxy.models import (
    CLUSTER,
    LEASE_TTL,
    Lease,
    Line,
    ListingResult,
    Outcome,
    Proxy,
    ProxyFolder,
    ProxyProtocol,
    ProxyRule,
    ProxyStatus,
    Use,
)
from scheduler_center.modules.proxy.tables import (
    proxies,
    proxy_folders,
    proxy_leases,
    proxy_lines,
    proxy_rules,
)

RECENT = timedelta(hours=1)
"""页面显示最近一小时的使用结果。"""


class ProxyRepository(Repository):
    async def page(
        self,
        offset: int,
        limit: int,
        *,
        keyword: str | None = None,
        folders: set[UUID] | None = None,
        unfiled: bool = False,
        use: Use | None = None,
        protocol: ProxyProtocol | None = None,
        status: ProxyStatus | None = None,
        enabled: bool | None = None,
        ip_version: int | None = None,
        dynamic: bool | None = None,
        line: str | None = None,
    ) -> tuple[list[Proxy], int]:
        stmt = select(Proxy).order_by(proxies.c.created_at, proxies.c.id)
        if keyword:
            pattern = f"%{keyword}%"
            stmt = stmt.where(
                or_(
                    proxies.c.host.ilike(pattern),
                    proxies.c.name.ilike(pattern),
                    proxies.c.exit_ip.ilike(pattern),
                )
            )
        if folders is not None:
            stmt = stmt.where(proxies.c.folder_id.in_(folders))
        if unfiled:
            stmt = stmt.where(proxies.c.folder_id.is_(None))
        if use:
            stmt = stmt.where(_serving(use))
        if protocol:
            stmt = stmt.where(proxies.c.protocol == protocol)
        if status:
            stmt = stmt.where(proxies.c.status == status)
        if enabled is not None:
            stmt = stmt.where(proxies.c.enabled == enabled)
        if ip_version == 4:
            stmt = stmt.where(proxies.c.exit_ip.not_like("%:%"))
        elif ip_version == 6:
            stmt = stmt.where(proxies.c.exit_ip.like("%:%"))
        if dynamic is not None:
            stmt = stmt.where(proxies.c.refresh_url.isnot(None) == dynamic)
        if line:
            stmt = stmt.where(proxies.c.line == line)
        return await self._page(stmt, offset, limit)

    async def of_ids(self, ids: Sequence[UUID], *, lock: bool = False) -> list[Proxy]:
        stmt = select(Proxy).where(proxies.c.id.in_(ids)).order_by(proxies.c.id)
        if lock:
            stmt = stmt.with_for_update(key_share=True).execution_options(populate_existing=True)
        return list(await self._session.scalars(stmt))

    async def serving(self, use: Use, *, device: str | None = None) -> list[Proxy]:
        """能分配给这个用途的代理：带这个用途、已启用、测试通过且出口已知。"""
        stmt = (
            select(Proxy)
            .where(
                _serving(use),
                proxies.c.enabled,
                proxies.c.status == ProxyStatus.AVAILABLE,
                proxies.c.line.isnot(None),
            )
            .order_by(proxies.c.created_at)
        )
        if device is not None:
            stmt = stmt.where(self._for_devices([device]))
        return list(await self._session.scalars(stmt))

    @staticmethod
    def _for_devices(devices: Sequence[str]):
        return or_(proxies.c.bound_device.is_(None), proxies.c.bound_device.in_(devices))

    async def existing(self) -> set[tuple[str, int, str]]:
        """已有代理的主机、端口和用户名，用来标出重复。"""
        stmt = select(proxies.c.host, proxies.c.port, proxies.c.username)
        return {tuple(row) for row in await self._session.execute(stmt)}

    async def add_new(self, items: Sequence[Proxy]) -> list[UUID]:
        """新增代理，与已有代理重复的跳过；返回实际新增的代理。"""
        if not items:
            return []
        rows = [
            {column.name: getattr(item, column.name) for column in proxies.columns}
            for item in items
        ]
        stmt = upsert(proxies).values(rows).on_conflict_do_nothing().returning(proxies.c.id)
        return list(await self._session.scalars(stmt))

    async def duplicate(self, host: str, port: int, username: str, exclude: UUID) -> bool:
        stmt = select(func.count()).where(
            proxies.c.host == host,
            proxies.c.port == port,
            proxies.c.username == username,
            proxies.c.id != exclude,
        )
        return bool(await self._session.scalar(stmt))

    async def remove(self, ids: Sequence[UUID]) -> None:
        await self._session.execute(delete(proxies).where(proxies.c.id.in_(ids)))

    async def move(self, ids: Sequence[UUID], folder_id: UUID | None) -> int:
        stmt = update(proxies).where(proxies.c.id.in_(ids)).values(folder_id=folder_id)
        return (await self._session.execute(stmt)).rowcount

    async def due_tests(self, now: datetime, limit: int, devices: Sequence[str]) -> list[Proxy]:
        """到了测试时间的代理；刷新中的要等它的线路都归还。其他 Worker 领走、还没到期的跳过。"""
        busy = select(proxy_leases.c.proxy_id).where(
            _open(now), proxy_leases.c.proxy_id.isnot(None)
        )
        stmt = (
            select(Proxy)
            .where(
                proxies.c.test_at <= now,
                self._for_devices(devices),
                or_(proxies.c.testing_until.is_(None), proxies.c.testing_until <= now),
                or_(proxies.c.status != ProxyStatus.REFRESHING, proxies.c.id.not_in(busy)),
            )
            .order_by(proxies.c.test_at)
            .limit(limit)
            .with_for_update(skip_locked=True, key_share=True)
        )
        return list(await self._session.scalars(stmt))

    async def due_speed_tests(
        self, now: datetime, limit: int, devices: Sequence[str]
    ) -> list[Proxy]:
        """到了测速时间的代理；其他 Worker 正在测的跳过。"""
        stmt = (
            select(Proxy)
            .where(proxies.c.speed_at <= now, self._for_devices(devices))
            .order_by(proxies.c.speed_at)
            .limit(limit)
            .with_for_update(skip_locked=True, key_share=True)
        )
        return list(await self._session.scalars(stmt))

    async def count_status(self, status: ProxyStatus) -> int:
        stmt = select(func.count()).where(proxies.c.status == status, proxies.c.enabled)
        return await self._session.scalar(stmt) or 0

    async def count_by_folder(self) -> dict[UUID | None, int]:
        stmt = select(proxies.c.folder_id, func.count()).group_by(proxies.c.folder_id)
        return dict((await self._session.execute(stmt)).all())


class FolderRepository(Repository):
    async def all(self) -> list[ProxyFolder]:
        return list(await self._session.scalars(select(ProxyFolder)))

    async def siblings(self, parent_id: UUID | None) -> list[ProxyFolder]:
        stmt = (
            select(ProxyFolder)
            .where(proxy_folders.c.parent_id.is_not_distinct_from(parent_id))
            .order_by(proxy_folders.c.position)
        )
        return list(await self._session.scalars(stmt))

    async def remove(self, folder: ProxyFolder) -> None:
        """删除文件夹：里面的代理和子文件夹移到上一级，只用这个文件夹的规则改用上一级。"""
        parent = folder.parent_id
        await self._session.execute(
            update(proxy_folders)
            .where(proxy_folders.c.parent_id == folder.id)
            .values(parent_id=parent)
        )
        await self._session.execute(
            update(proxies).where(proxies.c.folder_id == folder.id).values(folder_id=parent)
        )
        await self._session.execute(
            update(proxy_rules).where(proxy_rules.c.folder_id == folder.id).values(folder_id=parent)
        )
        await self.delete(folder)


class RuleRepository(Repository):
    async def cluster(self, use: Use, *, lock: bool = False) -> ProxyRule:
        """集群规则；还没配置过时是直连。"""
        return await self.get(ProxyRule, (use, CLUSTER), lock=lock) or ProxyRule(use, CLUSTER)

    async def effective(self, use: Use, device: str) -> ProxyRule:
        """设备在这个用途上生效的规则：有例外用例外，否则跟随集群。"""
        return await self.get(ProxyRule, (use, device)) or await self.cluster(use)

    async def devices(self, use: Use) -> dict[str, ProxyRule]:
        stmt = select(ProxyRule).where(proxy_rules.c.use == use, proxy_rules.c.device != CLUSTER)
        return {rule.device: rule for rule in await self._session.scalars(stmt)}

    async def save(self, rule: ProxyRule) -> None:
        await self._session.merge(rule)


class LineRepository(Repository):
    async def lock(
        self, use: Use, keys: Sequence[str], device: str | None, now: datetime
    ) -> dict[str, Line]:
        """锁住这个用途上的这些线路，按键顺序加锁，同时分配不会互相等待成环；第一次用到的先登记。"""
        if not keys:
            return {}
        rows = [
            {"use": use, "key": key, "device": device, "score": 0, "backoff": 0, "updated_at": now}
            for key in sorted(keys)
        ]
        await self._session.execute(upsert(proxy_lines).values(rows).on_conflict_do_nothing())
        stmt = (
            select(Line)
            .where(proxy_lines.c.use == use, proxy_lines.c.key.in_(keys))
            .order_by(proxy_lines.c.key)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        return {line.key: line for line in await self._session.scalars(stmt)}

    async def all(self) -> dict[tuple[Use, str], Line]:
        return {(line.use, line.key): line for line in await self._session.scalars(select(Line))}


class LeaseRepository(Repository):
    async def tasks_with_empty_lists(self, lease: Lease, since: datetime) -> int:
        """同一线路最近一份完整列表之后，返回空正文的不同任务数。"""
        column = proxy_leases.c
        scope = (
            column.use == lease.use,
            column.line == lease.line,
            column.acquired_at >= since,
            column.released_at >= since,
            column.task_id.is_not(None),
        )
        last_complete = (
            select(func.max(column.released_at))
            .where(*scope, column.listing_result == ListingResult.COMPLETE)
            .scalar_subquery()
        )
        stmt = select(func.count(column.task_id.distinct())).where(
            *scope,
            column.listing_result == ListingResult.EMPTY_RESPONSE,
            column.released_at > func.coalesce(last_complete, since),
        )
        return await self._session.scalar(stmt) or 0

    async def in_use(
        self,
        now: datetime,
        *,
        by=proxy_leases.c.line,
        use: Use | None = None,
        keys: Sequence | None = None,
    ) -> dict:
        """正在使用的租约数，默认按线路统计；可以只看一个用途。"""
        stmt = select(by, func.count()).where(_open(now)).group_by(by)
        if use:
            stmt = stmt.where(proxy_leases.c.use == use)
        if keys is not None:
            stmt = stmt.where(by.in_(keys))
        return dict((await self._session.execute(stmt)).all())

    async def renew(self, device: str, ids: Sequence[UUID], now: datetime) -> None:
        """只续这台设备还在用、没过期的租约：过期的名额可能已经给了别的设备，迟到的心跳不能让它复活。"""
        if not ids:
            return
        await self._session.execute(
            update(proxy_leases)
            .where(proxy_leases.c.id.in_(ids), proxy_leases.c.device == device, _open(now))
            .values(expires_at=now + LEASE_TTL)
        )

    async def recent(
        self, now: datetime, *, by=proxy_leases.c.line, use: Use | None = None
    ) -> dict:
        """最近一小时归还的租约，按表现统计：{键: (正常数, 异常数)}。"""
        stmt = (
            select(by, proxy_leases.c.outcome, func.count())
            .where(proxy_leases.c.released_at >= now - RECENT, by.isnot(None))
            .group_by(by, proxy_leases.c.outcome)
        )
        if use:
            stmt = stmt.where(proxy_leases.c.use == use)
        counts: dict = defaultdict(lambda: [0, 0])
        for key, outcome, count in await self._session.execute(stmt):
            counts[key][0 if outcome == Outcome.NORMAL else 1] += count
        return {key: tuple(value) for key, value in counts.items()}

    async def purge(self, before: datetime) -> int:
        stmt = delete(proxy_leases).where(proxy_leases.c.acquired_at < before)
        return (await self._session.execute(stmt)).rowcount


def _serving(use: Use):
    """用途标签里有这个用途，或有它所属的整个服务（jsonb ?| text[]）。"""
    return proxies.c.uses.has_any(array([use.value, use.service.value], type_=Text))


def _open(now: datetime):
    """还没归还、也没过期的租约。"""
    return proxy_leases.c.released_at.is_(None) & (proxy_leases.c.expires_at > now)

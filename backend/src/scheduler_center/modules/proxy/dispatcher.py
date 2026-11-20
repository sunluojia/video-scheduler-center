"""线路分配：服务每次要访问外网前按用途申请一条线路，用完归还并报告线路表现。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from scheduler_center.modules.proxy.models import (
    LEASE_TTL,
    Lease,
    Line,
    LineState,
    Outcome,
    PickMode,
    Proxy,
    ProxyRule,
    RuleSource,
    Use,
    device_line,
    subtree,
    tree_order,
)
from scheduler_center.modules.proxy.repository import (
    FolderRepository,
    LeaseRepository,
    LineRepository,
    ProxyRepository,
    RuleRepository,
)

EARLY_PROBE = timedelta(seconds=60)
"""线路都在冷却时，最多每 60 秒提前放一个申请去试探。"""


@dataclass
class Grant:
    lease: Lease
    proxy: Proxy | None
    local_port: int | None


@dataclass
class Wait:
    seconds: int
    reason: str


class Dispatcher:
    def __init__(self, session: AsyncSession) -> None:
        self._proxies = ProxyRepository(session)
        self._folders = FolderRepository(session)
        self._rules = RuleRepository(session)
        self._lines = LineRepository(session)
        self._leases = LeaseRepository(session)
        self._session = session

    async def acquire(
        self, device: str, use: Use, task_id: UUID | None, avoid: set[str], now: datetime
    ) -> Grant | Wait:
        """按设备在这个用途上生效的规则挑一条线路：避开这次任务失败过的线路，除非别无选择。"""
        rule, cluster = await self._rules.effective(use, device), await self._rules.cluster(use)
        candidates = await self.candidates(use, device, rule)
        if not candidates:
            return Wait(60, "规则里没有可用的代理")
        own = rule.source in (RuleSource.DIRECT, RuleSource.LOCAL_PORT)
        lines = await self._lines.lock(use, list(candidates), device if own else None, now)
        in_use = await self._leases.in_use(now, use=use, keys=list(candidates))
        usable = [key for key in candidates if lines[key].state(now) in _USABLE]
        preferred = [key for key in usable if key not in avoid] or usable

        def capacity(key: str) -> int:
            return 1 if lines[key].state(now) == LineState.PROBING else cluster.line_limit

        free = [key for key in preferred if in_use.get(key, 0) < capacity(key)]
        if free:
            key = self._pick(cluster.mode, rule.cursor, list(candidates), free)
            if cluster.mode == PickMode.ROTATE:
                rule.cursor = key
            probe = lines[key].state(now) == LineState.PROBING
            return self._grant(device, use, task_id, key, candidates[key], rule, probe, now)
        if usable:
            return Wait(5, "线路都占满了")
        if line := _early_probe(lines.values(), in_use, now):
            line.probe_at = now
            proxy = candidates[line.key]
            return self._grant(device, use, task_id, line.key, proxy, rule, True, now)
        earliest = min(line.resume_at() for line in lines.values() if line.resume_at())
        seconds = int((earliest - now).total_seconds())
        return Wait(min(max(seconds, 5), 60), "线路都在冷却或限流")

    async def release(
        self,
        lease_id: UUID,
        outcome: Outcome,
        detail: str | None,
        retry_after: int | None,
        now: datetime,
    ) -> None:
        """归还线路：池里的代理连不上或认证失败只记在代理上，其他表现记在线路上。重复归还忽略。"""
        lease = await self._leases.require(Lease, lease_id, lock=True)
        if lease.released_at:
            return
        lease.release(outcome, detail, now)
        line = (await self._lines.lock(lease.use, [lease.line], None, now))[lease.line]
        proxy = lease.proxy_id and await self._proxies.get(Proxy, lease.proxy_id, lock=True)
        if outcome == Outcome.THROTTLED:
            line.throttle(now, retry_after or 60, detail)
        elif lease.proxy_id and outcome in (Outcome.PROXY_DOWN, Outcome.AUTH_FAILED):
            if proxy and outcome == Outcome.PROXY_DOWN:
                proxy.unreachable(now, detail)
            elif proxy:
                proxy.auth_failed(detail)
        else:
            line.report(outcome, now, probe=lease.probe, detail=detail)
            if proxy and outcome == Outcome.NORMAL:
                proxy.worked()

    async def renew(self, device: str, lease_ids: list[UUID], now: datetime) -> None:
        await self._leases.renew(device, lease_ids, now)

    async def candidates(self, use: Use, device: str, rule: ProxyRule) -> dict[str, Proxy | None]:
        """候选线路及线路内取用的代理。池里的代理按文件夹的先后顺序排，同一文件夹里按添加顺序；
        出口相同的代理合并成一条线路。"""
        if rule.source == RuleSource.DIRECT:
            return {device_line(device): None}
        if rule.source == RuleSource.LOCAL_PORT:
            return {device_line(device, rule.local_port): None}
        folders = await self._folders.all()
        rank = {folder.id: index for index, folder in enumerate(tree_order(folders))}
        scope = subtree(folders, rule.folder_id) if rule.folder_id else None
        found = [
            proxy
            for proxy in await self._proxies.serving(use)
            if scope is None or proxy.folder_id in scope
        ]
        found.sort(key=lambda proxy: rank.get(proxy.folder_id, len(rank)))  # 没放文件夹的排最后
        candidates: dict[str, Proxy | None] = {}
        for proxy in found:
            candidates.setdefault(proxy.line, proxy)
        return candidates

    @staticmethod
    def _pick(mode: PickMode, cursor: str | None, order: list[str], free: list[str]) -> str:
        """优先级取第一条有空位的；轮询从上一次所用线路的下一条开始取。"""
        if mode != PickMode.ROTATE or cursor not in order:
            return free[0]
        start = order.index(cursor) + 1
        return min(free, key=lambda key: (order.index(key) - start) % len(order))

    def _grant(
        self,
        device: str,
        use: Use,
        task_id: UUID | None,
        key: str,
        proxy: Proxy | None,
        rule: ProxyRule,
        probe: bool,
        now: datetime,
    ) -> Grant:
        lease = Lease(
            device,
            use,
            key,
            proxy and proxy.id,
            task_id,
            probe,
            acquired_at=now,
            expires_at=now + LEASE_TTL,
        )
        self._session.add(lease)
        local_port = rule.local_port if rule.source == RuleSource.LOCAL_PORT else None
        return Grant(lease, proxy, local_port)


_USABLE = (LineState.OPEN, LineState.PROBING)


def _early_probe(lines, in_use: dict[str, int], now: datetime) -> Line | None:
    """线路都在冷却时，把最早到期的失败冷却线路提前拿出来试探；限流期内的不提前。"""
    failing = [
        line for line in lines if line.state(now) == LineState.COOLING and not in_use.get(line.key)
    ]
    if not failing:
        return None
    line = min(failing, key=lambda item: item.cooldown_until)
    if line.probe_at and now - line.probe_at < EARLY_PROBE:
        return None
    return line

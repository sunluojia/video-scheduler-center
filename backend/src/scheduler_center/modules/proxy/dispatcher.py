"""线路分配：服务每次要访问外网前按用途申请一条线路，用完归还并报告线路表现。"""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from random import choices
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from scheduler_center.modules.proxy.models import (
    LEASE_TTL,
    Lease,
    Line,
    LineState,
    ListingResult,
    Outcome,
    PickMode,
    Proxy,
    ProxyFolder,
    ProxyRule,
    RuleSource,
    RuleTarget,
    Use,
    device_line,
    serves,
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
LISTING_WINDOW = timedelta(minutes=2)
EMPTY_LIST_TASK_LIMIT = 3


@dataclass
class Grant:
    lease: Lease
    proxy: Proxy | None
    local_port: int | None


@dataclass
class Wait:
    seconds: int
    reason: str


@dataclass(frozen=True)
class Candidate:
    proxy: Proxy | None
    target: RuleTarget | None = None


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
        candidates = await self.candidates(use, device, rule, cluster.mode)
        if not candidates:
            return Wait(60, "规则里没有可用的代理")
        own = rule.source in (RuleSource.DIRECT, RuleSource.LOCAL_PORT)
        lines = await self._lines.lock(use, list(candidates), device if own else None, now)
        in_use = await self._leases.in_use(now, use=use, keys=list(candidates))
        usable = [key for key in candidates if lines[key].state(now) in _USABLE]

        def capacity(key: str) -> int:
            return 1 if lines[key].state(now) == LineState.PROBING else cluster.line_limit

        free = [key for key in usable if in_use.get(key, 0) < capacity(key)]
        if free:
            preferred = [key for key in free if key not in avoid] or free
            key = self._pick(cluster.mode, rule.cursor, candidates, preferred)
            probe = lines[key].state(now) == LineState.PROBING
            result = await self._grant(
                device, use, task_id, key, candidates[key].proxy, rule, probe, now
            )
            if isinstance(result, Grant) and cluster.mode == PickMode.ROTATE:
                rule.cursor = key
            return result
        if usable:
            return Wait(5, "线路都占满了")
        if line := _early_probe(lines.values(), in_use, now):
            proxy = candidates[line.key].proxy
            result = await self._grant(device, use, task_id, line.key, proxy, rule, True, now)
            if isinstance(result, Grant):
                line.probe_at = now
            return result
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
        *,
        listing_result: ListingResult | None = None,
    ) -> None:
        """归还线路：池里的代理连不上或认证失败只记在代理上，其他表现记在线路上。重复归还忽略。"""
        lease = await self._leases.require(Lease, lease_id, lock=True)
        if lease.released_at:
            return
        lease.release(outcome, detail, now)
        if lease.use == Use.CRAWL and lease.task_id is not None:
            if outcome == Outcome.NORMAL and listing_result == ListingResult.COMPLETE:
                lease.listing_result = listing_result
            elif outcome == Outcome.SUSPECT and listing_result in (
                ListingResult.READ_FAILED,
                ListingResult.EMPTY_RESPONSE,
            ):
                lease.listing_result = listing_result
                if listing_result == ListingResult.EMPTY_RESPONSE:
                    await self._report_empty_list(lease, now)
                return
        line = (await self._lines.lock(lease.use, [lease.line], None, now))[lease.line]
        proxy = lease.proxy_id and await self._proxies.get(Proxy, lease.proxy_id, lock=True)
        if proxy and (proxy.config_revision != lease.proxy_revision or proxy.line != lease.line):
            proxy = None  # 旧连接的结果不能覆盖当前代理的测试状态
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

    async def _report_empty_list(self, lease: Lease, now: datetime) -> None:
        line = (await self._lines.lock(lease.use, [lease.line], None, now))[lease.line]
        if line.state(now) != LineState.OPEN:
            return
        since = now - LISTING_WINDOW
        if line.cooldown_until is not None:
            since = max(since, line.cooldown_until)
        if line.throttled_until is not None:
            since = max(since, line.throttled_until)
        if await self._leases.tasks_with_empty_lists(lease, since) >= EMPTY_LIST_TASK_LIMIT:
            line.cool_after_empty_lists(now)

    async def renew(self, device: str, lease_ids: list[UUID], now: datetime) -> None:
        await self._leases.renew(device, lease_ids, now)

    async def candidates(
        self, use: Use, device: str, rule: ProxyRule, mode: PickMode = PickMode.PRIORITY
    ) -> dict[str, Candidate]:
        """按规则限定代理范围，同一出口归并为一条候选线路。"""
        if rule.source == RuleSource.DIRECT:
            return {device_line(device): Candidate(None)}
        if rule.source == RuleSource.LOCAL_PORT:
            return {device_line(device, rule.local_port): Candidate(None)}
        folders = tree_order(await self._folders.all())
        rank = {folder.id: index for index, folder in enumerate(folders)}
        proxies = await self._proxies.serving(use)
        proxies.sort(key=lambda proxy: rank.get(proxy.folder_id, len(rank)))
        if rule.targets is None:
            scope = subtree(folders, rule.folder_id) if rule.folder_id else None
            found = [
                Candidate(proxy) for proxy in proxies if scope is None or proxy.folder_id in scope
            ]
        else:
            found = _selected_candidates(folders, proxies, rule.targets, mode)
        candidates: dict[str, Candidate] = {}
        for candidate in found:
            if candidate.proxy and candidate.proxy.line is not None:
                candidates.setdefault(candidate.proxy.line, candidate)
        return candidates

    @staticmethod
    def _pick(
        mode: PickMode, cursor: str | None, candidates: dict[str, Candidate], free: list[str]
    ) -> str:
        if mode == PickMode.WEIGHTED and len(free) > 1:
            # 一个分组的权重由其中有空位的线路均分。
            groups = [
                (target["kind"], target["id"]) if (target := candidates[key].target) else key
                for key in free
            ]
            counts = Counter(groups)
            weights = [
                (target["weight"] if (target := candidates[key].target) else 1) / counts[group]
                for key, group in zip(free, groups, strict=True)
            ]
            return choices(free, weights, k=1)[0]
        if mode == PickMode.ROTATE and cursor in candidates:
            order = {key: index for index, key in enumerate(candidates)}
            return min(free, key=lambda key: (order[key] - order[cursor] - 1) % len(order))
        return free[0]

    async def _grant(
        self,
        device: str,
        use: Use,
        task_id: UUID | None,
        key: str,
        proxy: Proxy | None,
        rule: ProxyRule,
        probe: bool,
        now: datetime,
    ) -> Grant | Wait:
        if proxy is not None:
            folder_id = proxy.folder_id
            # 线路已经加锁。锁住代理直到租约发出，刷新出口才能等到所有已发出的租约归还。
            proxy = await self._proxies.get(Proxy, proxy.id, lock=True)
            if (
                proxy is None
                or not proxy.usable
                or proxy.line != key
                or proxy.folder_id != folder_id
                or not serves(proxy.uses, use)
            ):
                return Wait(1, "代理配置已变更，稍后重新分配")
        lease = Lease(
            device,
            use,
            key,
            proxy and proxy.id,
            task_id,
            probe,
            acquired_at=now,
            expires_at=now + LEASE_TTL,
            proxy_revision=proxy.config_revision if proxy else None,
        )
        self._session.add(lease)
        local_port = rule.local_port if rule.source == RuleSource.LOCAL_PORT else None
        return Grant(lease, proxy, local_port)


_USABLE = (LineState.OPEN, LineState.PROBING)


def _selected_candidates(
    folders: list[ProxyFolder], proxies: list[Proxy], targets: list[RuleTarget], mode: PickMode
) -> list[Candidate]:
    """单选代理覆盖分组设置，子分组覆盖其父分组。"""
    selected = {(target["kind"], target["id"]): target for target in targets}
    scope: dict[UUID, RuleTarget] = {}
    for folder in folders:
        target = selected.get(("folder", str(folder.id))) or scope.get(folder.parent_id)
        if target:
            scope[folder.id] = target

    matches = []
    for proxy in proxies:
        target = selected.get(("proxy", str(proxy.id))) or scope.get(proxy.folder_id)
        if target:
            matches.append((proxy, target))
    if mode == PickMode.PRIORITY:
        matches.sort(key=lambda pair: pair[1]["priority"])
    elif mode == PickMode.WEIGHTED:
        matches.sort(key=lambda pair: pair[1]["kind"] != "proxy")
    return [Candidate(proxy, target) for proxy, target in matches]


def _early_probe(lines, in_use: dict[str, int], now: datetime) -> Line | None:
    """线路都在冷却时，把最早到期的失败冷却线路提前拿出来试探；限流期内的不提前。"""
    failing = [
        line
        for line in lines
        if line.backoff and line.state(now) == LineState.COOLING and not in_use.get(line.key)
    ]
    if not failing:
        return None
    line = min(failing, key=lambda item: item.cooldown_until)
    if line.probe_at and now - line.probe_at < EARLY_PROBE:
        return None
    return line

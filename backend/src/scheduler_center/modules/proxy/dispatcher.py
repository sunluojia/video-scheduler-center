"""线路分配：服务每次要访问外网前按用途申请一条线路，用完归还并报告线路表现。"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from random import choices
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from scheduler_center.core.common.clock import utcnow
from scheduler_center.core.config.settings import Settings
from scheduler_center.modules.proxy.crawl_policy import BUSY_RECHECK, CrawlPolicy, CrawlRole
from scheduler_center.modules.proxy.models import (
    LEASE_TTL,
    CrawlLinePolicy,
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
    code: str | None = None


@dataclass(frozen=True)
class Candidate:
    proxy: Proxy | None
    target: RuleTarget | None = None


@dataclass
class CrawlAccess:
    free: dict[str, int]
    bootstrap: list[str]
    early: Line | None
    wait: Wait | None


class Dispatcher:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._proxies = ProxyRepository(session)
        self._folders = FolderRepository(session)
        self._rules = RuleRepository(session)
        self._lines = LineRepository(session)
        self._leases = LeaseRepository(session)
        self._session = session
        self._policy = (
            CrawlPolicy(session, settings) if settings and settings.crawl_policy_enabled else None
        )
        self._clock = clock or utcnow

    async def acquire(
        self,
        device: str,
        use: Use,
        task_id: UUID | None,
        avoid: set[str],
        now: datetime,
        *,
        crawl_role: CrawlRole | None = None,
        warm_bootstrap: bool = False,
    ) -> Grant | Wait:
        """按设备在这个用途上生效的规则挑一条线路：避开这次任务失败过的线路，除非别无选择。"""
        rule, cluster = await self._rules.effective(use, device), await self._rules.cluster(use)
        candidates = await self.candidates(use, device, rule, cluster.mode)
        if not candidates:
            policy = self._policy
            code = None
            if (
                use == Use.CRAWL
                and crawl_role == CrawlRole.LISTING
                and policy
                and policy.settings.crawl_policy_applies(device)
            ):
                code = "crawl_line_busy"
            return Wait(60, "规则里没有可用的代理", code)
        own = rule.source in (RuleSource.DIRECT, RuleSource.LOCAL_PORT)
        lines = await self._lines.lock(use, list(candidates), device if own else None, now)
        controlled = self._controlled(device, use, crawl_role, candidates)
        line_limit = rule.device_line_limit or cluster.line_limit
        if controlled:
            assert self._policy is not None and crawl_role is not None
            states = await self._policy.states.for_keys(
                self._policy.epoch, controlled, self._clock(), lock=True
            )
            now = self._clock()
            access = await self._crawl_access(
                candidates,
                lines,
                states,
                controlled,
                line_limit,
                crawl_role,
                now,
                lock=True,
                warm_bootstrap=warm_bootstrap,
            )
            free = list(access.free) or access.bootstrap
            if free:
                preferred = [key for key in free if key not in avoid] or free
                key = self._pick(cluster.mode, rule.cursor, candidates, preferred)
            elif access.early:
                key = access.early.key
            else:
                assert access.wait is not None
                return access.wait
            probe = access.early is not None or lines[key].state(now) == LineState.PROBING
            result = await self._grant(
                device,
                use,
                task_id,
                key,
                candidates[key].proxy,
                rule,
                probe,
                now,
                refresh_clock=True,
            )
            if isinstance(result, Grant):
                if key in states:
                    self._policy.granted(states[key], result.lease, crawl_role)
                if access.early:
                    access.early.probe_at = result.lease.acquired_at
                elif cluster.mode == PickMode.ROTATE:
                    rule.cursor = key
            elif crawl_role == CrawlRole.LISTING:
                result.code = "crawl_line_busy"
            return result

        in_use = await self._leases.in_use(now, use=use, keys=list(candidates))
        usable = [key for key in candidates if lines[key].state(now) in _USABLE]

        def capacity(key: str) -> int:
            return 1 if lines[key].state(now) == LineState.PROBING else line_limit

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

    async def limit_crawl_claims(self, device: str, limit: int) -> tuple[int, Wait | None]:
        """只读预判。认领和线路租约各自独立，发放时仍会持锁复核。"""
        policy = self._policy
        if not limit or policy is None or not policy.settings.crawl_policy_applies(device):
            return limit, None
        use = Use.CRAWL
        rule, cluster = await self._rules.effective(use, device), await self._rules.cluster(use)
        candidates = await self.candidates(use, device, rule, cluster.mode)
        if not candidates:
            return 0, Wait(60, "规则里没有可用的代理", "crawl_line_busy")
        controlled = self._controlled(device, use, CrawlRole.LISTING, candidates)
        if not controlled:
            return limit, None
        lines = await self._lines.for_keys(use, list(candidates))
        states = await policy.states.for_keys(policy.epoch, controlled, self._clock(), lock=False)
        access = await self._crawl_access(
            candidates,
            lines,
            states,
            controlled,
            rule.device_line_limit or cluster.line_limit,
            CrawlRole.LISTING,
            self._clock(),
            lock=False,
        )
        slots = sum(access.free.values()) + int(access.early is not None)
        return min(limit, slots), access.wait if slots == 0 else None

    def _controlled(
        self,
        device: str,
        use: Use,
        role: CrawlRole | None,
        candidates: dict[str, Candidate],
    ) -> list[str]:
        policy = self._policy
        if use != Use.CRAWL or role is None or policy is None:
            return []
        settings = policy.settings
        if not settings.crawl_policy_applies(device):
            return []
        if role == CrawlRole.WARM and not (policy.steps and settings.crawl_empty_warm_guard):
            return []
        return [
            key
            for key, item in candidates.items()
            if item.proxy is not None and key in settings.crawl_policy_exits
        ]

    async def _crawl_access(
        self,
        candidates: dict[str, Candidate],
        lines: dict[str, Line],
        states: dict[str, CrawlLinePolicy],
        controlled: list[str],
        line_limit: int,
        role: CrawlRole,
        now: datetime,
        *,
        lock: bool,
        warm_bootstrap: bool = False,
    ) -> CrawlAccess:
        policy = self._policy
        assert policy is not None
        probe_waits = await policy.probe_waits(states, now, lock=lock)
        in_use = await self._leases.in_use(now, use=Use.CRAWL, keys=list(candidates))
        free, bootstrap, early_lines, waits = {}, [], [], []
        usable_but_full = False
        for key in candidates:
            line, state = lines.get(key), states.get(key)
            recovering = policy.recovers(state)
            general = line.state(now) if line else LineState.OPEN
            delays = (
                policy.delays(
                    state,
                    role,
                    now,
                    probe_waits.get(key),
                    warm_bootstrap=warm_bootstrap,
                )
                if state
                else []
            )
            limit = 1 if recovering or general == LineState.PROBING else line_limit
            slots = limit - in_use.get(key, 0)
            if general not in _USABLE:
                assert line is not None
                if not recovering and not delays:
                    early_lines.append(line)
                delays.append((line.resume_at(), "crawl_line_busy"))
            if slots <= 0:
                usable_but_full |= general in _USABLE and not delays and not recovering
                delays.append((now + BUSY_RECHECK, "crawl_line_busy"))
            if delays:
                until, code = max(delays, key=lambda delay: delay[0])
                if role == CrawlRole.WARM:
                    code = "warm_empty_wait" if recovering else None
                waits.append((until, code))
            elif role == CrawlRole.WARM and recovering:
                bootstrap.append(key)
            else:
                free[key] = min(slots, 1) if key in controlled and policy.pacing else slots
        if free or bootstrap:
            return CrawlAccess(free, bootstrap, None, None)
        if not usable_but_full and (early := _early_probe(early_lines, in_use, now)):
            return CrawlAccess({}, [], early, None)
        until, code = min(waits, key=lambda wait: wait[0])
        wait = Wait(max(1, ceil((until - now).total_seconds())), _CRAWL_WAIT_REASONS[code], code)
        return CrawlAccess({}, [], None, wait)

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
        policy = self._policy
        if not (
            policy
            and lease.use == Use.CRAWL
            and lease.task_id is not None
            and lease.crawl_policy_epoch
            and policy.settings.crawl_policy_applies(lease.device)
            and lease.line in policy.settings.crawl_policy_exits
        ):
            policy = None
        line, proxy = None, None
        if policy:
            line, proxy = await self._lock_feedback(lease, now)
            now = self._clock()
        lease.release(outcome, detail, now)
        listing_failure = False
        if lease.use == Use.CRAWL and lease.task_id is not None:
            if outcome == Outcome.NORMAL and listing_result == ListingResult.COMPLETE:
                lease.listing_result = listing_result
            elif outcome == Outcome.SUSPECT and listing_result in (
                ListingResult.READ_FAILED,
                ListingResult.EMPTY_RESPONSE,
            ):
                lease.listing_result = listing_result
                listing_failure = True
        if policy and lease.crawl_policy_epoch == policy.epoch and proxy and now < lease.expires_at:
            await policy.released(lease, now)
        if listing_failure:
            if listing_result == ListingResult.EMPTY_RESPONSE and not (policy and policy.steps):
                await self._report_empty_list(lease, now, line=line)
            return
        if line is None:
            line, proxy = await self._lock_feedback(lease, now)
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

    async def _lock_feedback(self, lease: Lease, now: datetime) -> tuple[Line, Proxy | None]:
        line = (await self._lines.lock(lease.use, [lease.line], None, now))[lease.line]
        proxy = lease.proxy_id and await self._proxies.get(Proxy, lease.proxy_id, lock=True)
        if proxy and (proxy.config_revision != lease.proxy_revision or proxy.line != lease.line):
            proxy = None  # 旧连接的结果不能覆盖当前代理的测试状态
        return line, proxy

    async def _report_empty_list(
        self, lease: Lease, now: datetime, *, line: Line | None = None
    ) -> None:
        if line is None:
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
        proxies = await self._proxies.serving(use, device=device)
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
        *,
        refresh_clock: bool = False,
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
                or (refresh_clock and proxy.bound_device not in (None, device))
            ):
                return Wait(1, "代理配置已变更，稍后重新分配")
        if refresh_clock:
            now = self._clock()
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
_CRAWL_WAIT_REASONS = {
    "crawl_paced": "采集线路正在节流",
    "crawl_empty_cooldown": "作品列表空正文，采集线路正在冷却",
    "crawl_probe_busy": "采集线路正在等作品列表探针",
    "crawl_line_busy": "采集线路正在使用、冷却或限流",
    "warm_empty_wait": "采集线路正在冷却或等作品列表探针，稍后预热",
    None: "预热线路正在使用、冷却或限流",
}


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

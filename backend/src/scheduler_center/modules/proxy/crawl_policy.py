"""按实际出口约束达人列表租约，独立于取链和下载使用的线路健康状态。"""

import logging
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from scheduler_center.core.config.settings import Settings
from scheduler_center.modules.proxy.models import LEASE_TTL, CrawlLinePolicy, Lease, ListingResult
from scheduler_center.modules.proxy.repository import CrawlPolicyRepository, LeaseRepository

log = logging.getLogger(__name__)
BUSY_RECHECK = timedelta(seconds=5)
LOST_PROBE_GUARD = timedelta(seconds=150)


class CrawlRole(StrEnum):
    LISTING = "listing"
    WARM = "warm"


class CrawlPolicy:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.settings = settings
        self.states = CrawlPolicyRepository(session)
        self._leases = LeaseRepository(session)
        self.epoch = settings.crawl_policy_epoch
        self.steps = settings.crawl_empty_backoff_steps
        self.pacing = timedelta(seconds=settings.crawl_pacing_min_seconds)

    def recovers(self, state: CrawlLinePolicy | None) -> bool:
        return bool(self.steps and state and state.empty_step)

    async def probe_waits(
        self, states: dict[str, CrawlLinePolicy], now: datetime, *, lock: bool
    ) -> dict[str, tuple[datetime, str]]:
        probes = [
            state for state in states.values() if self.recovers(state) and state.probe_lease_id
        ]
        leases = await self._leases.probe_leases([state.probe_lease_id for state in probes])
        waits = {}
        for state in probes:
            lease = leases.get(state.probe_lease_id)
            if lease and lease.released_at is None and lease.expires_at > now:
                waits[state.line_key] = now + BUSY_RECHECK, "crawl_probe_busy"
                continue
            granted_at = state.probe_granted_at
            assert granted_at is not None
            expired_at = lease.expires_at if lease else granted_at + LEASE_TTL
            until = max(expired_at + self._step(state.empty_step), granted_at + LOST_PROBE_GUARD)
            if lock:
                self._cool(state, now, state.empty_step, until=until, event="probe_expired")
                # 识别失联的这次请求只更新状态。预判仍可在截止后给下一次认领名额。
                until = max(until, now + timedelta(seconds=1))
            waits[state.line_key] = until, "crawl_empty_cooldown"
        return waits

    def delays(
        self,
        state: CrawlLinePolicy,
        role: CrawlRole,
        now: datetime,
        probe_wait: tuple[datetime, str] | None,
        *,
        warm_bootstrap: bool,
    ) -> list[tuple[datetime, str]]:
        delays = []
        if role == CrawlRole.LISTING and self.pacing:
            anchors = (state.last_crawl_acquired_at, state.last_valid_crawl_released_at)
            last = max((anchor for anchor in anchors if anchor is not None), default=None)
            if last is not None and last + self.pacing > now:
                delays.append((last + self.pacing, "crawl_paced"))
        if not self.recovers(state):
            return delays
        if state.empty_until is not None and state.empty_until > now:
            delays.append((state.empty_until, "crawl_empty_cooldown"))
        if probe_wait and probe_wait[0] > now:
            delays.append(probe_wait)
        if role == CrawlRole.WARM:
            if not warm_bootstrap and not delays:
                delays.append((now + BUSY_RECHECK, "warm_empty_wait"))
            if warm_bootstrap and state.last_bootstrap_at is not None:
                until = state.last_bootstrap_at + timedelta(
                    seconds=self.settings.crawl_warm_bootstrap_min_seconds
                )
                if until > now:
                    delays.append((until, "warm_empty_wait"))
        return delays

    def granted(self, state: CrawlLinePolicy, lease: Lease, role: CrawlRole) -> None:
        if role == CrawlRole.WARM:
            if self.recovers(state):
                state.last_bootstrap_at = lease.acquired_at
                self._log(state, "warm_bootstrap", lease.id)
            return
        lease.crawl_policy_epoch = self.epoch
        state.last_crawl_acquired_at = lease.acquired_at
        if self.recovers(state):
            state.probe_lease_id, state.probe_granted_at = lease.id, lease.acquired_at
            self._log(state, "listing_probe", lease.id)

    async def released(self, lease: Lease, now: datetime) -> None:
        state = await self.states.get(CrawlLinePolicy, (self.epoch, lease.line), lock=True)
        if state is None:
            return
        state.last_valid_crawl_released_at = max(state.last_valid_crawl_released_at or now, now)
        if not self.steps:
            return
        if self.recovers(state):
            if lease.id != state.probe_lease_id:
                return
            if lease.listing_result == ListingResult.COMPLETE:
                state.empty_step, state.empty_until = 0, None
                state.probe_lease_id = state.probe_granted_at = None
                state.evidence_since = now
                self._log(state, "listing_recovered", lease.id)
                return
            empty = lease.listing_result == ListingResult.EMPTY_RESPONSE
            next_step = min(state.empty_step + int(empty), len(self.steps))
            self._cool(
                state, now, next_step, event="probe_empty" if empty else "probe_inconclusive"
            )
            return
        if lease.acquired_at < state.evidence_since:
            return
        if lease.listing_result == ListingResult.COMPLETE:
            state.evidence_since = now
        elif lease.listing_result == ListingResult.EMPTY_RESPONSE:
            since = now - timedelta(seconds=self.settings.crawl_empty_window_seconds)
            if await self.states.empty_tasks(state, since) >= self.settings.crawl_empty_tasks:
                self._cool(state, now, 1, event="empty_threshold")

    def _step(self, step: int) -> timedelta:
        return timedelta(seconds=self.steps[min(step, len(self.steps)) - 1])

    def _cool(
        self,
        state: CrawlLinePolicy,
        now: datetime,
        step: int,
        *,
        event: str,
        until: datetime | None = None,
    ) -> None:
        probe_id = state.probe_lease_id
        state.empty_step, state.empty_until = step, until or now + self._step(step)
        state.evidence_since = now
        state.probe_lease_id = state.probe_granted_at = None
        self._log(state, event, probe_id)

    @staticmethod
    def _log(state: CrawlLinePolicy, event: str, lease_id=None) -> None:
        log.info(
            "crawl_policy event=%s epoch=%s line=%s step=%s until=%s lease_id=%s",
            event,
            state.policy_epoch,
            state.line_key,
            state.empty_step,
            state.empty_until,
            lease_id,
        )

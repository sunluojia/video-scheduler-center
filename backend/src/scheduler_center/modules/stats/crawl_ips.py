from datetime import datetime
from hashlib import sha256
from ipaddress import ip_address, ip_network
from typing import Literal
from uuid import UUID

from sqlalchemy import String, Uuid, and_, case, cast, func, or_, select

from scheduler_center.core.common.clock import utcnow
from scheduler_center.core.config.settings import Settings
from scheduler_center.core.database.repository import Repository
from scheduler_center.core.web.schemas import Page
from scheduler_center.modules.batch.models import Batch, BatchStatus
from scheduler_center.modules.batch.tables import batches, creators
from scheduler_center.modules.crawl.models import (
    CrawlConclusion,
    CrawlerAttemptStatus,
    CrawlerTaskKind,
    CrawlerTaskStatus,
)
from scheduler_center.modules.crawl.tables import crawler_task_attempts, crawler_tasks
from scheduler_center.modules.proxy.crawl_policy import CrawlPolicy
from scheduler_center.modules.proxy.models import CrawlLinePolicy, ListingResult, Outcome, Use
from scheduler_center.modules.proxy.tables import proxies, proxy_leases, proxy_lines
from scheduler_center.modules.stats.schemas import (
    CrawlIpBatch,
    CrawlIpMetrics,
    CrawlIpRisk,
    CrawlIpRow,
    CrawlIpStats,
    CrawlIpSummary,
)

MIN_RATE_SECONDS = 60
SHARE_UNITS = 10_000
ACCOUNT_RESULTS = (
    CrawlConclusion.PRIVATE,
    CrawlConclusion.NOT_FOUND,
    CrawlConclusion.LOGIN_REQUIRED,
)

# 当前采集器优先给出结构化结果；词条补充旧租约和可识别的平台报错。
# 私密、注销或被封的达人属于账号结论。网络故障与未确认原因的 suspect 也不计入。
RISK_DETAILS = {
    "empty_body": r"空正文|\mempty[_ -]body\M",
    "challenge": r"挑战页|验证码|人机验证|\mcaptcha\M|\mchallenge[_ -]page\M|verify you are human",
    "throttled": (
        r"\m209002\M|限流|频率限制|too many requests|\mrate[_ -]?limit(?:ed|ing)?\M"
        r"|\m(?:HTTP|status(?:[ _]?code)?)[ :（(=]+429\M|[（(]429[）)]"
    ),
    "blocked": (
        r"(?:线路|出口|IP|请求|访问|平台).{0,12}(?:封禁|封锁|拦截|被拒绝)"
        r"|\m(?:ip|access|request)[ _-](?:is[ _-])?(?:blocked|banned|forbidden|denied)\M"
        r"|(?:作品列表|达人主页) HTTP 403\M"
        r"|^\s*(?:封禁|blocked|banned)\s*$"
    ),
    "other": r"风控|\mrisk[_ -]?control\M|\msecurity check\M",
}


class CrawlIpRepository(Repository):
    async def batch_page(self, offset: int, size: int) -> Page[CrawlIpBatch]:
        page, total = await self._page(
            select(Batch).order_by(batches.c.created_at.desc(), batches.c.id.desc()), offset, size
        )
        available = set()
        if page:
            recent = _latest_leases().where(
                crawler_tasks.c.batch_id.in_([item.id for item in page])
            )
            available = set(await self._session.scalars(recent))
        return Page(
            items=[_batch_view(batch, batch.id in available) for batch in page], total=total
        )

    async def stats(self, batch_id: UUID | None, settings: Settings) -> CrawlIpStats:
        if batch_id is None:
            batch = await self._default_batch()
        else:
            batch = await self.require(Batch, batch_id)
        rows = []
        if batch is not None:
            rows = (await self._session.execute(_by_line(batch.id))).mappings().all()
        first = min((row.first_lease_at for row in rows), default=None)
        last = max((row.last_lease_at for row in rows), default=None)
        elapsed = _elapsed(first, last)
        states, waits = {}, {}
        now = utcnow()
        if rows and settings.crawl_policy_enabled and settings.crawl_empty_backoff_steps:
            policy = CrawlPolicy(self._session, settings)
            keys = sorted(set(settings.crawl_policy_exits).intersection(row.line for row in rows))
            if keys:
                states = await policy.states.for_keys(policy.epoch, keys, now, lock=False)
                waits = await policy.probe_waits(states, now, lock=False)

        metrics = [
            _metrics(
                row.crawl_leases,
                row.collected_creators,
                (row.private, row.not_found, row.login_required),
                CrawlIpRisk(**{key: row[key] for key in RISK_DETAILS}),
                _elapsed(row.first_lease_at, row.last_lease_at),
                elapsed,
            )
            for row in rows
        ]
        creator_shares = _shares([item.collected_creators for item in metrics])
        risk_shares = _shares([item.risk_count for item in metrics])
        lines = []
        for row, item, creator_share, risk_share in zip(
            rows, metrics, creator_shares, risk_shares, strict=True
        ):
            line = _public_line(row.line)
            state, step, until = _backoff(
                states.get(row.line), waits.get(row.line), row.legacy_empty_until, now
            )
            lines.append(
                CrawlIpRow(
                    **item.model_dump(),
                    line=line,
                    proxy_name=_public_name(row.proxy_name) or line,
                    creator_share_pct=creator_share,
                    risk_share_pct=risk_share,
                    backoff_state=state,
                    backoff_step=step,
                    backoff_until=until,
                )
            )
        total = _metrics(
            sum(item.crawl_leases for item in metrics),
            sum(item.collected_creators for item in metrics),
            (
                sum(item.private for item in metrics),
                sum(item.not_found for item in metrics),
                sum(item.login_required for item in metrics),
            ),
            CrawlIpRisk(
                **{
                    key: sum(getattr(item.risk_breakdown, key) for item in metrics)
                    for key in RISK_DETAILS
                }
            ),
            elapsed,
            elapsed,
        )
        return CrawlIpStats(
            batch=_batch_view(batch, bool(lines)) if batch else None,
            data_status=(
                "available"
                if lines
                else "no_batch"
                if batch is None
                else "history_unavailable"
                if batch.finished_at
                else "no_leases"
            ),
            summary=CrawlIpSummary(
                **total.model_dump(),
                active_ips=len(lines),
                first_lease_at=first,
                last_lease_at=last,
            ),
            lines=lines,
        )

    async def _default_batch(self) -> Batch | None:
        recent = _latest_leases().subquery()
        stmt = (
            select(Batch)
            .outerjoin(recent, recent.c.batch_id == batches.c.id)
            .order_by(
                recent.c.last_lease_at.is_not(None).desc(),
                (batches.c.status == BatchStatus.RUNNING).desc(),
                recent.c.last_lease_at.desc().nulls_last(),
                batches.c.created_at.desc(),
                batches.c.id.desc(),
            )
            .limit(1)
        )
        return await self._session.scalar(stmt)


def _batch_view(batch: Batch, has_leases: bool) -> CrawlIpBatch:
    return CrawlIpBatch(id=batch.id, name=batch.name, status=batch.status, has_leases=has_leases)


def _crawl_leases():
    lease, task = proxy_leases.c, crawler_tasks.c
    return proxy_leases.join(
        crawler_tasks,
        and_(lease.task_id == task.id, lease.use == Use.CRAWL, task.kind == CrawlerTaskKind.CRAWL),
    )


def _latest_leases():
    batch_id = crawler_tasks.c.batch_id
    return (
        select(batch_id, func.max(proxy_leases.c.acquired_at).label("last_lease_at"))
        .select_from(_crawl_leases())
        .group_by(batch_id)
    )


def _risk_kind():
    lease = proxy_leases.c
    return case(
        (lease.released_at.is_(None), None),
        (lease.listing_result.in_((ListingResult.EMPTY_RESPONSE, "empty_body")), "empty_body"),
        (lease.outcome == Outcome.THROTTLED, "throttled"),
        (lease.outcome.in_((Outcome.NORMAL, "ok", Outcome.PROXY_DOWN, Outcome.AUTH_FAILED)), None),
        *((lease.detail.bool_op("~*")(pattern), kind) for kind, pattern in RISK_DETAILS.items()),
    )


def _evidence(batch_id: UUID):
    lease, task, attempt, proxy = (
        proxy_leases.c,
        crawler_tasks.c,
        crawler_task_attempts.c,
        proxies.c,
    )
    complete = and_(lease.listing_result == ListingResult.COMPLETE, lease.released_at.is_not(None))
    task_result = and_(
        task.status == CrawlerTaskStatus.SUCCEEDED,
        task.conclusion.in_((CrawlConclusion.OK, *ACCOUNT_RESULTS)),
        lease.outcome.in_((Outcome.NORMAL, "ok")),
        lease.listing_result.is_(None),
        lease.acquired_at >= task.published_at,
        lease.released_at <= task.finished_at,
    )
    attempt_result = attempt.status == CrawlerAttemptStatus.SUCCEEDED
    legacy_result = and_(task_result, lease.attempt_id.is_(None))
    creator_key = func.coalesce(
        func.nullif(func.lower(creators.c.handle), ""), cast(task.creator_id, String)
    )
    return (
        select(
            lease.id,
            lease.line,
            lease.acquired_at,
            lease.released_at,
            creator_key.label("creator_key"),
            proxy.id.label("proxy_id"),
            _risk_kind().label("risk"),
            case(
                (complete, CrawlConclusion.OK.value),
                (and_(task_result, or_(attempt_result, legacy_result)), task.conclusion),
            ).label("conclusion"),
            complete.label("complete"),
            legacy_result.label("legacy"),
            func.count().filter(legacy_result).over(partition_by=task.id).label("legacy_count"),
        )
        .select_from(
            _crawl_leases()
            .join(creators, creators.c.id == task.creator_id)
            .outerjoin(
                crawler_task_attempts,
                and_(
                    attempt.attempt_id == lease.attempt_id,
                    attempt.task_id == task.id,
                    attempt.run == task.run,
                ),
            )
            .outerjoin(
                proxies,
                and_(
                    proxy.id == lease.proxy_id,
                    proxy.line == lease.line,
                    proxy.config_revision.is_not_distinct_from(lease.proxy_revision),
                ),
            )
        )
        .where(task.batch_id == batch_id)
        .cte("crawl_lease_evidence")
    )


def _creator_results(evidence):
    row = evidence.c
    # 有完整列表反馈时优先采用它。没有 attempt_id 的旧回报仅在当轮可唯一匹配时归因。
    rank = case((row.complete, 0), (row.conclusion == CrawlConclusion.OK, 1), else_=2)
    ranked = (
        select(
            row.line,
            row.conclusion,
            func.row_number()
            .over(
                partition_by=row.creator_key,
                order_by=(rank, row.released_at, row.acquired_at, row.id),
            )
            .label("position"),
        )
        .where(row.conclusion.is_not(None), or_(~row.legacy, row.legacy_count == 1))
        .subquery()
    )
    return (
        select(
            ranked.c.line,
            func.count()
            .filter(ranked.c.conclusion == CrawlConclusion.OK)
            .label("collected_creators"),
            *(
                func.count().filter(ranked.c.conclusion == outcome).label(outcome.value)
                for outcome in ACCOUNT_RESULTS
            ),
        )
        .where(ranked.c.position == 1)
        .group_by(ranked.c.line)
        .subquery()
    )


def _by_line(batch_id: UUID):
    evidence = _evidence(batch_id)
    row = evidence.c
    results = _creator_results(evidence)
    line = proxy_lines.c
    lines = (
        select(
            row.line,
            func.count().label("crawl_leases"),
            func.min(row.acquired_at).label("first_lease_at"),
            func.max(row.acquired_at).label("last_lease_at"),
            case(
                (
                    and_(
                        func.count(row.proxy_id) == func.count(),
                        func.count(row.proxy_id.distinct()) == 1,
                    ),
                    cast(func.min(cast(row.proxy_id, String)), Uuid),
                ),
            ).label("proxy_id"),
            *(func.count().filter(row.risk == kind).label(kind) for kind in RISK_DETAILS),
        )
        .group_by(row.line)
        .subquery()
    )
    return (
        select(
            lines,
            _proxy_name().label("proxy_name"),
            line.cooldown_until.label("legacy_empty_until"),
            *(
                func.coalesce(results.c[key], 0).label(key)
                for key in ("collected_creators", *(outcome.value for outcome in ACCOUNT_RESULTS))
            ),
        )
        .select_from(
            lines.outerjoin(results, results.c.line == lines.c.line)
            .outerjoin(proxies, proxies.c.id == lines.c.proxy_id)
            .outerjoin(
                proxy_lines,
                and_(
                    line.use == Use.CRAWL,
                    line.key == lines.c.line,
                    line.reason == "多个任务的作品列表返回空正文",
                ),
            )
        )
        .order_by(lines.c.line)
    )


def _proxy_name():
    """在数据库内排除误贴到名称中的凭据，凭据列本身不进入查询结果。"""
    proxy = proxies.c
    basic_auth = func.replace(
        func.encode(func.convert_to(proxy.username + ":" + proxy.password, "UTF8"), "base64"),
        "\n",
        "",
    )
    sensitive = or_(
        *(
            and_(value != "", func.strpos(proxy.name, value) > 0)
            for value in (proxy.username, proxy.password, proxy.private_key, proxy.refresh_url)
        ),
        and_(proxy.username != "", proxy.password != "", func.strpos(proxy.name, basic_auth) > 0),
    )
    return case((sensitive, None), else_=proxy.name)


def _public_name(value: str | None) -> str | None:
    name = (value or "").strip()
    if name.casefold().startswith(("basic ", "bearer ")):
        return None
    return name if all(char.isalnum() or char in " _-.()（）·#" for char in name) else None


def _public_line(value: str) -> str:
    if "%" not in value:  # IPv6 zone identifiers can include private device names.
        parse_line = ip_network if "/" in value else ip_address
        try:
            parse_line(value)
            return value
        except ValueError:
            pass
    return f"线路 {sha256(value.encode()).hexdigest()[:12]}"


def _backoff(
    state: CrawlLinePolicy | None,
    wait: tuple[datetime, str] | None,
    legacy_until: datetime | None,
    now: datetime,
) -> tuple[Literal["normal", "cooling", "probing"], int, datetime | None]:
    probe_until = wait[0] if wait and wait[1] == "crawl_empty_cooldown" else None
    step = state.empty_step if state else 0
    policy_until = state.empty_until if state and step else None
    until = max(
        (value for value in (policy_until, probe_until, legacy_until) if value and value > now),
        default=None,
    )
    if until:
        return "cooling", step or 1, until
    return ("probing", step, None) if step else ("normal", 0, None)


def _elapsed(first: datetime | None, last: datetime | None) -> float:
    return (last - first).total_seconds() if first and last else 0.0


def _metrics(
    leases: int,
    collected: int,
    unavailable: tuple[int, int, int],
    risk: CrawlIpRisk,
    elapsed: float,
    batch_elapsed: float,
) -> CrawlIpMetrics:
    risk_count = sum(risk.model_dump().values())
    rate_hours = batch_elapsed / 3600 if batch_elapsed >= MIN_RATE_SECONDS else None
    private, not_found, login_required = unavailable
    return CrawlIpMetrics(
        crawl_leases=leases,
        collected_creators=collected,
        private=private,
        not_found=not_found,
        login_required=login_required,
        risk_count=risk_count,
        risk_rate_pct=round(100 * risk_count / leases, 2) if leases else 0.0,
        risk_breakdown=risk,
        active_hours=elapsed / 3600,
        creators_per_hour=collected / rate_hours if rate_hours is not None else None,
        leases_per_hour=leases / rate_hours if rate_hours is not None else None,
    )


def _shares(counts: list[int]) -> list[float]:
    """按最大余数分配百分比的百分之一，舍入后仍恰好合计 100%。"""
    total = sum(counts)
    if not total:
        return [0.0] * len(counts)
    portions = [divmod(count * SHARE_UNITS, total) for count in counts]
    missing = SHARE_UNITS - sum(units for units, _ in portions)
    extra = set(sorted(range(len(portions)), key=lambda index: -portions[index][1])[:missing])
    return [(units + (index in extra)) / 100 for index, (units, _) in enumerate(portions)]

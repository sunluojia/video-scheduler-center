import logging
from uuid import UUID

from fastapi import APIRouter

from scheduler_center.core.common.clock import utcnow
from scheduler_center.core.web.dependencies import ApiSettingsDep, DatabaseDep
from scheduler_center.modules.crawl.attempts import CrawlerAttempts
from scheduler_center.modules.crawl.models import CrawlerRunKind
from scheduler_center.modules.live.notifier import Live
from scheduler_center.modules.proxy.crawl_policy import CrawlRole
from scheduler_center.modules.proxy.dispatcher import Dispatcher, Grant
from scheduler_center.modules.proxy.messages import (
    LeaseGrant,
    LeaseReply,
    LeaseRequest,
    LeaseResult,
    Route,
)
from scheduler_center.modules.proxy.models import Outcome, Use

log = logging.getLogger(__name__)
router = APIRouter(prefix="/proxy-leases", tags=["爬取程序"])


@router.post("")
async def acquire(
    body: LeaseRequest, database: DatabaseDep, settings: ApiSettingsDep
) -> LeaseReply:
    """每次要访问外网前按用途申请一条线路。分不到时按 retry_after 秒后再来，期间不要开始。"""
    async with database.transaction() as session:
        attempts = CrawlerAttempts(session)
        role = None
        route_attempt = body.attempt_id
        if body.use == Use.CRAWL and settings.crawl_policy_applies(body.device):
            if body.task_id is None and body.attempt_id is None:
                role = CrawlRole.WARM
            else:
                kind = await attempts.lease_kind(body.task_id, body.attempt_id, body.device)
                if kind == CrawlerRunKind.CRAWL:
                    role = CrawlRole.LISTING
                elif kind is None:
                    route_attempt = None
                    log.warning("crawl_policy event=unclassified_lease device=%s", body.device)
        result = await Dispatcher(session, settings).acquire(
            body.device,
            body.use,
            body.task_id,
            set(body.avoid),
            utcnow(),
            crawl_role=role,
            warm_bootstrap=body.warm_bootstrap,
        )
        if isinstance(result, Grant):
            lease, route = result.lease, Route.of(result)
            lease.attempt_id = body.attempt_id
            if route_attempt is not None:
                await attempts.record_route(route_attempt, body.device, route.kind)
            grant = LeaseGrant(lease_id=lease.id, line=lease.line, probe=lease.probe, route=route)
            return LeaseReply(lease=grant)
    return LeaseReply(retry_after=result.seconds, reason=result.reason, reason_code=result.code)


@router.post("/{lease_id}/release", status_code=204)
async def release(
    lease_id: UUID, body: LeaseResult, database: DatabaseDep, settings: ApiSettingsDep
) -> None:
    """尝试结束时归还线路，报告这条线路的表现；重复归还忽略。"""
    async with database.transaction() as session:
        await Dispatcher(session, settings).release(
            lease_id,
            body.outcome,
            body.detail,
            body.retry_after,
            utcnow(),
            listing_result=body.listing_result,
        )
        if body.outcome != Outcome.NORMAL:
            await Live(session).proxies_changed()

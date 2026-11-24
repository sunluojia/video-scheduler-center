from uuid import UUID

from fastapi import APIRouter

from scheduler_center.core.common.clock import utcnow
from scheduler_center.core.web.dependencies import DatabaseDep
from scheduler_center.modules.crawl.attempts import CrawlerAttempts
from scheduler_center.modules.live.notifier import Live
from scheduler_center.modules.proxy.dispatcher import Dispatcher, Grant
from scheduler_center.modules.proxy.messages import (
    LeaseGrant,
    LeaseReply,
    LeaseRequest,
    LeaseResult,
    Route,
)
from scheduler_center.modules.proxy.models import Outcome

router = APIRouter(prefix="/proxy-leases", tags=["爬取程序"])


@router.post("")
async def acquire(body: LeaseRequest, database: DatabaseDep) -> LeaseReply:
    """每次要访问外网前按用途申请一条线路。分不到时按 retry_after 秒后再来，期间不要开始。"""
    async with database.transaction() as session:
        result = await Dispatcher(session).acquire(
            body.device, body.use, body.task_id, set(body.avoid), utcnow()
        )
        if isinstance(result, Grant):
            lease, route = result.lease, Route.of(result)
            if body.attempt_id is not None:
                lease.attempt_id = body.attempt_id
                await CrawlerAttempts(session).record_route(
                    body.attempt_id, body.device, route.kind
                )
            grant = LeaseGrant(lease_id=lease.id, line=lease.line, probe=lease.probe, route=route)
            return LeaseReply(lease=grant)
    return LeaseReply(retry_after=result.seconds, reason=result.reason)


@router.post("/{lease_id}/release", status_code=204)
async def release(lease_id: UUID, body: LeaseResult, database: DatabaseDep) -> None:
    """尝试结束时归还线路，报告这条线路的表现；重复归还忽略。"""
    async with database.transaction() as session:
        await Dispatcher(session).release(
            lease_id,
            body.outcome,
            body.detail,
            body.retry_after,
            utcnow(),
            listing_result=body.listing_result,
        )
        if body.outcome != Outcome.NORMAL:
            await Live(session).proxies_changed()

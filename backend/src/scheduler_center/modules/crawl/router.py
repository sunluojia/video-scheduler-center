import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Request

from scheduler_center.core.common.errors import Conflict, Invalid
from scheduler_center.core.web.dependencies import ApiSettingsDep, DatabaseDep
from scheduler_center.core.web.schemas import ErrorDetail
from scheduler_center.modules.crawl.attempts import CrawlerAttempts
from scheduler_center.modules.crawl.models import AttemptRef, CrawlerRunKind
from scheduler_center.modules.crawl.schemas import (
    ClaimedTask,
    ClaimReply,
    ClaimRequest,
    CrawlWait,
    DeferRequest,
)
from scheduler_center.modules.flow.service import Flow
from scheduler_center.modules.live.notifier import Live
from scheduler_center.modules.material.dependencies import StoreDep
from scheduler_center.modules.material.store import ContentMismatch
from scheduler_center.modules.proxy.dispatcher import Dispatcher

log = logging.getLogger(__name__)
router = APIRouter(tags=["爬取程序"])


@router.post(
    "/crawler-tasks/claim",
    responses={409: {"model": ErrorDetail, "description": "当前使用旧版 Redis 派单"}},
)
async def claim_tasks(
    body: ClaimRequest, database: DatabaseDep, settings: ApiSettingsDep
) -> ClaimReply:
    """按设备剩余槽位原子认领可执行任务，租期 90 秒。"""
    if settings.legacy_crawler_dispatch:
        raise Conflict("当前使用 Redis 爬虫派单")
    limits = {
        CrawlerRunKind.CRAWL: body.kinds.crawl_creator,
        CrawlerRunKind.DOWNLOAD: body.kinds.download_video,
    }
    async with database.transaction() as session:
        wait = None
        if body.kinds.crawl_creator and settings.crawl_policy_applies(body.executor_id):
            allowed, wait = await Dispatcher(session, settings).limit_crawl_claims(
                body.executor_id, body.kinds.crawl_creator
            )
            limits[CrawlerRunKind.CRAWL] = allowed
            if allowed < body.kinds.crawl_creator:
                log.info(
                    "crawl_policy event=claim_limited device=%s skipped=%s "
                    "reason=%s retry_after=%s",
                    body.executor_id,
                    body.kinds.crawl_creator - allowed,
                    wait.code if wait else "available_slots",
                    wait.seconds if wait else None,
                )
        claimed = await CrawlerAttempts(session).claim(
            body.executor_id, body.executor_session_id, limits
        )
        if claimed:
            await Live(session).attempts_changed()
        return ClaimReply(
            tasks=[
                ClaimedTask(
                    kind=run.kind,
                    task_id=run.task_id,
                    run=run.run,
                    attempt_id=attempt.attempt_id,
                    attempt_no=attempt.attempt_no,
                    lease_expires_at=attempt.lease_expires_at,
                    attempts=run.attempts,
                    avoid=run.avoid,
                    history=run.history,
                    body=run.payload,
                )
                for run, attempt in claimed
            ],
            crawl_wait=(
                CrawlWait(retry_after=wait.seconds, reason=wait.reason, reason_code=wait.code)
                if wait
                else None
            ),
        )


@router.post(
    "/crawler-tasks/attempts/{attempt_id}/defer",
    status_code=204,
    responses={409: {"model": ErrorDetail, "description": "认领已失效或不属于该执行器会话"}},
)
async def defer_task(attempt_id: UUID, body: DeferRequest, database: DatabaseDep) -> None:
    """结束当前认领，延迟后可由任意设备重新认领。"""
    async with database.transaction() as session:
        await CrawlerAttempts(session).defer(
            attempt_id,
            body.executor_id,
            body.executor_session_id,
            body.retry_after,
            body.reason,
            consume_attempt=body.consume_attempt,
            avoid_line=body.avoid_line,
        )
        await Live(session).attempts_changed()


@router.put(
    "/download-tasks/{task_id}/runs/{run}/video",
    status_code=204,
    responses={
        404: {"model": ErrorDetail, "description": "旧版上传请求的任务不存在"},
        409: {"model": ErrorDetail, "description": "认领、轮次或已保存的文件与请求不符"},
    },
)
async def upload(
    task_id: UUID,
    run: int,
    request: Request,
    database: DatabaseDep,
    store: StoreDep,
    content_length: Annotated[int, Header(gt=0)],
    sha256: Annotated[str, Header(alias="X-Content-SHA256", pattern="^[0-9a-f]{64}$")],
    attempt_id: Annotated[UUID | None, Header(alias="X-Attempt-ID")] = None,
    executor_id: Annotated[str | None, Header(alias="X-Executor-ID", max_length=128)] = None,
    executor_session_id: Annotated[UUID | None, Header(alias="X-Executor-Session-ID")] = None,
) -> None:
    """爬取程序上传下载好的视频，校验大小和 SHA-256 后登记并提交分析。

    同一认领重复上传相同内容直接成功；认领已失效返回 409，大小或摘要不一致返回 422。
    """
    attempt_ref = AttemptRef(attempt_id, executor_id, executor_session_id) if attempt_id else None
    async with database.transaction() as session:
        if await Flow(session).check_upload(task_id, run, sha256, attempt_ref):
            return
    try:
        received = await store.receive(request.stream(), content_length, sha256)
    except ContentMismatch as error:
        raise Invalid(str(error)) from error
    material_id = uuid4()
    path = store.keep(received, material_id)
    try:
        async with database.transaction() as session:
            kept = await Flow(session).upload(
                task_id, run, material_id, content_length, sha256, attempt_ref
            )
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if not kept:
        path.unlink(missing_ok=True)

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Request

from scheduler_center.core.common.errors import Invalid
from scheduler_center.core.web.dependencies import DatabaseDep
from scheduler_center.modules.flow.service import Flow
from scheduler_center.modules.material.dependencies import StoreDep
from scheduler_center.modules.material.store import ContentMismatch

router = APIRouter(tags=["爬取程序"])


@router.put("/download-tasks/{task_id}/runs/{run}/video", status_code=204)
async def upload(
    task_id: UUID,
    run: int,
    request: Request,
    database: DatabaseDep,
    store: StoreDep,
    content_length: Annotated[int, Header(gt=0)],
    sha256: Annotated[str, Header(alias="X-Content-SHA256", pattern="^[0-9a-f]{64}$")],
) -> None:
    """爬取程序上传下载好的视频，校验大小和 SHA-256 后登记并提交分析。

    相同内容重复上传直接成功；任务已取消或已有更新的轮次返回 409，大小或摘要不一致返回 422。
    """
    async with database.transaction() as session:
        if await Flow(session).check_upload(task_id, run, sha256):
            return
    try:
        received = await store.receive(request.stream(), content_length, sha256)
    except ContentMismatch as error:
        raise Invalid(str(error)) from error
    material_id = uuid4()
    path = store.keep(received, material_id)
    try:
        async with database.transaction() as session:
            kept = await Flow(session).upload(task_id, run, material_id, content_length, sha256)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if not kept:
        path.unlink(missing_ok=True)

"""账号池页面的接口：各设备的身份池，以及转给设备执行的刷新和退休。"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Request

from scheduler_center.core.common.clock import utcnow
from scheduler_center.core.common.errors import NotFound
from scheduler_center.core.web.dependencies import DatabaseDep
from scheduler_center.modules.executor.dependencies import GatewayDep
from scheduler_center.modules.executor.models import Executor, ExecutorKind
from scheduler_center.modules.executor.repository import ExecutorRepository
from scheduler_center.modules.identity.repository import IdentityPoolRepository
from scheduler_center.modules.identity.schemas import (
    IdentityAction,
    IdentityBoard,
    IdentityDevice,
    IdentityPoolSetting,
    IdentitySnapshot,
)

router = APIRouter(tags=["账号池"])

SHORT_FOR = timedelta(minutes=10)
"""可用身份少于目标超过这么久才提示，刚启动、刚退休几个时后台会自己补上。"""


@router.get("/identities")
async def board(request: Request, database: DatabaseDep) -> IdentityBoard:
    """报过身份池快照的爬取程序，按设备列出，附需要人处理的问题。"""
    now = utcnow()
    timeout = timedelta(seconds=request.app.state.settings.heartbeat_timeout_seconds)
    async with database.transaction() as session:
        executors = await ExecutorRepository(session).all()
        target = await IdentityPoolRepository(session).target()
    devices = [
        IdentityDevice(
            instance_id=item.instance_id,
            online=item.online(now, timeout),
            last_seen_at=item.last_seen_at,
            snapshot=IdentitySnapshot.model_validate(item.identities),
        )
        for item in executors
        if item.kind == ExecutorKind.CRAWLER and item.identities
    ]
    alerts = [alert for device in devices if (alert := _alert(device, now))]
    return IdentityBoard(target=target, devices=devices, alerts=alerts)


@router.put("/identity-pool")
async def set_pool(body: IdentityPoolSetting, database: DatabaseDep) -> IdentityPoolSetting:
    """改账号池要保持的可用数量；爬取程序下一次心跳拿到新的分摊数，自动补充。"""
    async with database.transaction() as session:
        await IdentityPoolRepository(session).set_target(body.target, utcnow())
    return body


@router.post("/identities/{instance_id}/{identity_id}/{action}", status_code=204)
async def act(
    instance_id: str,
    identity_id: str,
    action: IdentityAction,
    database: DatabaseDep,
    gateway: GatewayDep,
) -> None:
    """刷新或退休一个身份，转给那台设备执行；页面在它下一次心跳后更新。"""
    async with database.transaction() as session:
        executor = await ExecutorRepository(session).get(
            Executor, (ExecutorKind.CRAWLER, instance_id)
        )
    if executor is None:
        raise NotFound(f"没有设备 {instance_id}")
    await gateway.call_on(executor, "POST", f"/api/identities/{identity_id}/{action}")


def _alert(device: IdentityDevice, now: datetime) -> str | None:
    """可用身份少于目标超过 10 分钟：附上预热成功率，低的话多半是采集线路的问题。"""
    snapshot, since = device.snapshot, device.snapshot.short_since
    if not device.online or since is None or now - since < SHORT_FOR:
        return None
    warmed = snapshot.warm_total - snapshot.warm_failed
    text = (
        f"设备 {device.instance_id} 可用身份 {snapshot.ready}/{snapshot.target}，"
        f"已经 {int((now - since).total_seconds() // 60)} 分钟不够；"
        f"最近一小时预热 {snapshot.warm_total} 次，成功 {warmed} 次"
    )
    if snapshot.warm_failed * 2 > snapshot.warm_total:
        text += "，多半是采集线路的问题，可以换一批美国住宅代理"
    return text

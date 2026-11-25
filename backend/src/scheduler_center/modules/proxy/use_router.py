"""各用途的页面接口：用途卡片（规则、线路、设备）、设备例外和需要人处理的问题。"""

from collections.abc import Iterable
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy.ext.asyncio import AsyncSession

from scheduler_center.core.common.clock import utcnow
from scheduler_center.core.common.errors import Invalid, Rejected
from scheduler_center.core.web.dependencies import DatabaseDep
from scheduler_center.modules.executor.dependencies import GatewayDep
from scheduler_center.modules.executor.models import Executor, ExecutorKind
from scheduler_center.modules.executor.repository import ExecutorRepository
from scheduler_center.modules.live.notifier import Live
from scheduler_center.modules.proxy.dispatcher import Dispatcher
from scheduler_center.modules.proxy.messages import CheckItem
from scheduler_center.modules.proxy.models import (
    CLUSTER,
    MAX_COOLDOWN,
    SERVICE_NAMES,
    USE_NAMES,
    CheckResult,
    Line,
    ProxyFolder,
    ProxyProtocol,
    ProxyRule,
    ProxyStatus,
    RuleSource,
    RuleTarget,
    RuleTargetKind,
    Use,
    device_line,
)
from scheduler_center.modules.proxy.repository import (
    FolderRepository,
    LeaseRepository,
    LineRepository,
    ProxyRepository,
    RuleRepository,
)
from scheduler_center.modules.proxy.schemas import (
    ClusterRuleIn,
    DeviceRow,
    DeviceRuleIn,
    LineView,
    RuleIn,
    RuleView,
    UseView,
)
from scheduler_center.modules.proxy.tables import proxy_leases
from scheduler_center.modules.proxy.testing import run_tests

router = APIRouter(tags=["代理"])


@router.get("/proxy-uses")
async def list_uses(request: Request, database: DatabaseDep) -> list[UseView]:
    """每个用途一张卡片：集群规则、能用的代理数、线路状态，以及各台设备。"""
    now, timeout = utcnow(), _timeout(request)
    async with database.transaction() as session:
        executors = await ExecutorRepository(session).all()
        rules = RuleRepository(session)
        views = []
        for use in Use:
            cluster, own = await rules.cluster(use), await rules.devices(use)
            labels = await _target_labels(
                session,
                (target for rule in (cluster, *own.values()) for target in rule.targets or []),
            )
            lines = await _line_views(session, use, cluster.line_limit, now)
            in_use = await LeaseRepository(session).in_use(now, by=proxy_leases.c.device, use=use)
            devices = {
                item.instance_id: item
                for item in executors
                if item.kind == ExecutorKind(use.service)
            }
            rows = []
            for device in sorted(devices.keys() | own.keys()):
                executor, rule = devices.get(device), own.get(device)
                effective = rule or cluster
                key = (
                    device_line(device, effective.local_port)
                    if effective.source in (RuleSource.DIRECT, RuleSource.LOCAL_PORT)
                    else None
                )
                rows.append(
                    DeviceRow(
                        instance_id=device,
                        online=bool(executor and executor.online(now, timeout)),
                        last_seen_at=executor.last_seen_at if executor else None,
                        rule=rule and RuleView.of(rule, labels),
                        in_use=in_use.get(device, 0),
                        line=next((line for line in lines if line.key == key), None),
                    )
                )
            views.append(
                UseView(
                    key=use,
                    service=use.service,
                    service_name=SERVICE_NAMES[use.service],
                    name=USE_NAMES[use],
                    rule=RuleView.of(cluster, labels),
                    proxies=len(await ProxyRepository(session).serving(use)),
                    lines=lines,
                    devices=rows,
                )
            )
    return views


@router.put("/proxy-uses/{use}/rule")
async def set_cluster_rule(use: Use, body: ClusterRuleIn, database: DatabaseDep) -> RuleView:
    """这个用途的集群规则：直连还是走代理池、先用哪条、每条线路的同时数。"""
    async with database.transaction() as session:
        rules = RuleRepository(session)
        rule = await rules.cluster(use, lock=True)
        labels = await _apply(session, rule, body)
        rule.mode, rule.line_limit = body.mode, body.line_limit
        await rules.save(rule)
        await Live(session).proxies_changed()
    return RuleView.of(rule, labels)


@router.put("/proxy-uses/{use}/devices/{instance_id}")
async def set_device_rule(
    use: Use, instance_id: str, body: DeviceRuleIn, database: DatabaseDep
) -> RuleView:
    """给一台设备在这个用途上设例外，可以用这台设备的本机端口。"""
    if instance_id == CLUSTER:
        raise Invalid("设备标识无效")
    async with database.transaction() as session:
        rules = RuleRepository(session)
        rule = await rules.get(ProxyRule, (use, instance_id), lock=True)
        rule = rule or ProxyRule(use, instance_id)
        labels = await _apply(session, rule, body)
        if "line_limit" in body.model_fields_set:
            rule.device_line_limit = body.line_limit
        await rules.save(rule)
        await Live(session).proxies_changed()
    return RuleView.of(rule, labels)


@router.delete("/proxy-uses/{use}/devices/{instance_id}", status_code=204)
async def clear_device_rule(use: Use, instance_id: str, database: DatabaseDep) -> None:
    """清除例外，设备回到集群规则。"""
    async with database.transaction() as session:
        rules = RuleRepository(session)
        if instance_id != CLUSTER and (rule := await rules.get(ProxyRule, (use, instance_id))):
            await rules.delete(rule)
            await Live(session).proxies_changed()


@router.post("/proxy-uses/{use}/devices/{instance_id}/test")
async def test_local_port(
    use: Use, instance_id: str, request: Request, database: DatabaseDep, gateway: GatewayDep
) -> LineView:
    """请这台设备测试它在这个用途上设的本机端口，记下出口。"""
    async with database.transaction() as session:
        rule = await RuleRepository(session).get(ProxyRule, (use, instance_id))
        executor = await ExecutorRepository(session).get(
            Executor, (ExecutorKind(use.service), instance_id)
        )
    if not rule or rule.source != RuleSource.LOCAL_PORT:
        raise Invalid("这台设备没有设本机端口")
    if executor is None:
        raise Invalid("这台设备还没有注册")
    item = CheckItem(key="port", protocol=ProxyProtocol.AUTO, host=None, port=rule.local_port)
    echo_url = request.app.state.settings.proxy_echo_url
    replies = await run_tests(gateway, executor, echo_url, [item])
    if not replies:
        raise Rejected(f"{instance_id} 没有返回测试结果")
    reply, now = replies[0], utcnow()
    key = device_line(instance_id, rule.local_port)
    async with database.transaction() as session:
        line = (await LineRepository(session).lock(use, [key], instance_id, now))[key]
        line.tested_at = now
        line.exit_ip = reply.exit_ip if reply.result == CheckResult.OK else None
        line.reason = None if reply.result == CheckResult.OK else reply.error or reply.result
        limit = (await RuleRepository(session).cluster(use)).line_limit
        views = await _line_views(session, use, limit, now)
    return next(view for view in views if view.key == key)


@router.get("/proxy-alerts")
async def alerts(request: Request, database: DatabaseDep) -> list[str]:
    """需要人处理的问题：代理认证失败、没有设备能测试代理、设备在某个用途上没有可用的代理，
    以及设备的线路都冷却到了最长时间（试探一直失败，平台或网络可能出了问题）。"""
    now, timeout = utcnow(), _timeout(request)
    found = []
    async with database.transaction() as session:
        proxies = ProxyRepository(session)
        if count := await proxies.count_status(ProxyStatus.AUTH_FAILED):
            found.append(f"{count} 个代理认证失败，更新凭据后自动恢复")
        online = [
            item for item in await ExecutorRepository(session).all() if item.online(now, timeout)
        ]
        crawlers = [item for item in online if item.kind == ExecutorKind.CRAWLER]
        if not crawlers and await proxies.count_status(ProxyStatus.UNTESTED):
            found.append("没有在线的爬取程序，新添加的代理暂时无法测试")
        rules, dispatcher = RuleRepository(session), Dispatcher(session)
        lines = await LineRepository(session).all()
        for use in Use:
            for device in (item for item in online if item.kind == ExecutorKind(use.service)):
                rule = await rules.effective(use, device.instance_id)
                candidates = await dispatcher.candidates(use, device.instance_id, rule)
                name = f"{USE_NAMES[use]}：设备 {device.instance_id}"
                if not candidates:
                    found.append(f"{name} 没有可用的代理，在等待")
                elif all(
                    (line := lines.get((use, key))) and line.backoff >= MAX_COOLDOWN
                    for key in candidates
                ):
                    found.append(f"{name} 的线路试探一直失败，冷却已到最长 30 分钟")
    return found


async def _line_views(session: AsyncSession, use: Use, limit: int, now: datetime) -> list[LineView]:
    """这个用途的线路：池里带这个用途的可用代理按出口归并，加上设备自己的线路。"""
    names: dict[str, list[str]] = {}
    for proxy in await ProxyRepository(session).serving(use):
        names.setdefault(proxy.line, []).append(proxy.name or f"{proxy.host}:{proxy.port}")
    lines = {
        key: line
        for (owner, key), line in (await LineRepository(session).all()).items()
        if owner == use
    }
    leases = LeaseRepository(session)
    in_use, recent = await leases.in_use(now, use=use), await leases.recent(now, use=use)
    keys = sorted(names.keys() | {key for key, line in lines.items() if line.device})
    views = []
    for key in keys:
        line = lines.get(key) or Line(use, key)
        ok, failed = recent.get(key, (0, 0))
        views.append(
            LineView(
                key=key,
                kind="direct" if key.startswith("direct:") else "port" if line.device else "pool",
                device=line.device,
                exit_ip=line.exit_ip if line.device else key.split("/")[0],
                proxies=names.get(key, []),
                state=line.state(now),
                score=line.score,
                cooldown_until=line.resume_at(),
                reason=line.reason,
                in_use=in_use.get(key, 0),
                limit=limit,
                recent_ok=ok,
                recent_failed=failed,
                tested_at=line.tested_at,
            )
        )
    return views


async def _apply(
    session: AsyncSession, rule: ProxyRule, body: RuleIn
) -> dict[tuple[RuleTargetKind, str], str]:
    """只保留当前走法用得到的设置；轮询位置随规则重置。"""
    targets = None
    if body.source == RuleSource.POOL and body.targets is not None:
        targets = [target.model_dump(mode="json") for target in body.targets]
    labels = await _target_labels(session, targets or [])
    if targets and len(labels) != len(targets):
        raise Invalid("所选分组或代理已删除，请重新选择")
    if body.source == RuleSource.POOL and body.folder_id:
        await FolderRepository(session).require(ProxyFolder, body.folder_id)
    rule.source = body.source
    rule.folder_id = body.folder_id if body.source == RuleSource.POOL else None
    rule.targets = targets
    rule.local_port = body.local_port if body.source == RuleSource.LOCAL_PORT else None
    rule.cursor, rule.updated_at = None, utcnow()
    return labels


async def _target_labels(
    session: AsyncSession, targets: Iterable[RuleTarget]
) -> dict[tuple[RuleTargetKind, str], str]:
    ids = {"folder": set(), "proxy": set()}
    for target in targets:
        ids[target["kind"]].add(UUID(target["id"]))
    labels: dict[tuple[RuleTargetKind, str], str] = {}
    if ids["folder"]:
        for folder in await FolderRepository(session).all():
            if folder.id in ids["folder"]:
                labels["folder", str(folder.id)] = folder.name
    if ids["proxy"]:
        for proxy in await ProxyRepository(session).of_ids(list(ids["proxy"])):
            labels["proxy", str(proxy.id)] = proxy.name or f"{proxy.host}:{proxy.port}"
    return labels


def _timeout(request: Request) -> timedelta:
    return timedelta(seconds=request.app.state.settings.heartbeat_timeout_seconds)

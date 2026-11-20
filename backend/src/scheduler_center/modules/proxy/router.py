"""代理池的页面接口：代理和文件夹。"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy.ext.asyncio import AsyncSession

from scheduler_center.core.common.clock import utcnow
from scheduler_center.core.common.errors import Conflict, Invalid
from scheduler_center.core.web.dependencies import DatabaseDep, PagingDep
from scheduler_center.core.web.schemas import Page
from scheduler_center.modules.live.notifier import Live
from scheduler_center.modules.proxy.models import (
    LineState,
    Proxy,
    ProxyFolder,
    ProxyProtocol,
    ProxyStatus,
    Use,
    serves,
    subtree,
    tree_order,
)
from scheduler_center.modules.proxy.parsing import parse_lines
from scheduler_center.modules.proxy.repository import (
    FolderRepository,
    LeaseRepository,
    LineRepository,
    ProxyRepository,
)
from scheduler_center.modules.proxy.schemas import (
    Affected,
    FolderIn,
    FolderName,
    FolderPlace,
    Folders,
    FolderView,
    Ids,
    Imported,
    ImportText,
    Move,
    ParsedRow,
    ProxyAction,
    ProxyIn,
    ProxyPatch,
    ProxyView,
    Retag,
    UseState,
    tidy,
)
from scheduler_center.modules.proxy.tables import proxy_leases

router = APIRouter(tags=["代理"])
ADDRESS, CREDENTIALS = ("host", "port"), ("protocol", "username", "password", "private_key")


@router.get("/proxies")
async def list_proxies(
    database: DatabaseDep,
    paging: PagingDep,
    q: str | None = None,
    folder_id: UUID | None = None,
    unfiled: bool = False,
    use: Use | None = None,
    protocol: ProxyProtocol | None = None,
    status: ProxyStatus | None = None,
    enabled: bool | None = None,
    ip_version: Annotated[int | None, Query(ge=4, le=6)] = None,
    dynamic: bool | None = None,
    line: str | None = None,
) -> Page[ProxyView]:
    """选了文件夹时包括它的子文件夹；unfiled 只看没放进文件夹的。"""
    now = utcnow()
    async with database.transaction() as session:
        folders = folder_id and subtree(await FolderRepository(session).all(), folder_id)
        items, total = await ProxyRepository(session).page(
            paging.offset,
            paging.size,
            keyword=q,
            folders=folders or None,
            unfiled=unfiled,
            use=use,
            protocol=protocol,
            status=status,
            enabled=enabled,
            ip_version=ip_version,
            dynamic=dynamic,
            line=line,
        )
        views = await _views(session, items, now)
    return Page(items=views, total=total)


@router.post("/proxies", status_code=201)
async def add_proxy(body: ProxyIn, database: DatabaseDep) -> ProxyView:
    """添加一条代理，入库后自动测试。"""
    now = utcnow()
    proxy = Proxy(**_normalized(body.model_dump()), test_at=now)
    async with database.transaction() as session:
        await _folder_exists(session, proxy.folder_id)
        if not await ProxyRepository(session).add_new([proxy]):
            raise Conflict("已有主机、端口、用户名都相同的代理")
        await _live(session)
        return (await _views(session, [proxy], now))[0]


@router.post("/proxies/parse")
async def parse(body: ImportText, database: DatabaseDep) -> list[ParsedRow]:
    """识别粘贴或导入的文本，逐行预览；不返回密码。"""
    async with database.transaction() as session:
        seen = await ProxyRepository(session).existing()
    rows = []
    for line in parse_lines(body.text):
        if address := line.address:
            key = (address.host, address.port, address.username)
            rows.append(
                ParsedRow(
                    number=line.number,
                    host=address.host,
                    port=address.port,
                    protocol=address.protocol,
                    username=address.username,
                    has_password=bool(address.password),
                    duplicate=key in seen,
                )
            )
            seen.add(key)
        else:
            rows.append(ParsedRow(number=line.number, error=line.error))
    return rows


@router.post("/proxies/import")
async def import_proxies(body: ImportText, database: DatabaseDep) -> Imported:
    """按识别结果导入到选定的文件夹、带上选定的用途，自动测试；无法识别和重复的行跳过。"""
    now = utcnow()
    lines = parse_lines(body.text)
    proxies = [
        Proxy(
            line.address.host,
            line.address.port,
            line.address.protocol,
            line.address.username,
            line.address.password,
            folder_id=body.folder_id,
            uses=list(body.uses),
            test_at=now,
        )
        for line in lines
        if line.address
    ]
    async with database.transaction() as session:
        await _folder_exists(session, body.folder_id)
        created = await ProxyRepository(session).add_new(proxies)
        if created:
            await _live(session)
    return Imported(created=len(created), skipped=len(lines) - len(created))


@router.post("/proxies/move")
async def move_proxies(body: Move, database: DatabaseDep) -> Affected:
    """把选中的代理移到另一个文件夹，拖动和“移动到”都用它。"""
    async with database.transaction() as session:
        await _folder_exists(session, body.folder_id)
        count = await ProxyRepository(session).move(body.ids, body.folder_id)
        await _live(session)
    return Affected(count=count)


@router.post("/proxies/uses")
async def retag_proxies(body: Retag, database: DatabaseDep) -> Affected:
    """给选中的代理加上或去掉用途标签。"""
    async with database.transaction() as session:
        proxies = await ProxyRepository(session).of_ids(body.ids, lock=True)
        for proxy in proxies:
            kept = [tag for tag in proxy.uses if tag not in body.remove]
            proxy.uses = tidy([*kept, *body.add])
        await _live(session)
    return Affected(count=len(proxies))


@router.patch("/proxies/{proxy_id}")
async def edit_proxy(proxy_id: UUID, body: ProxyPatch, database: DatabaseDep) -> ProxyView:
    """改过地址或凭据的代理重新测试；地址变了，原来的出口作废。"""
    now = utcnow()
    changes = _normalized(body.model_dump(exclude_unset=True))
    async with database.transaction() as session:
        repo = ProxyRepository(session)
        proxy = await repo.require(Proxy, proxy_id, lock=True)
        changed = {key for key, value in changes.items() if getattr(proxy, key) != value}
        identity = [changes.get(key, getattr(proxy, key)) for key in ("host", "port", "username")]
        if changed & {"host", "port", "username"} and await repo.duplicate(*identity, proxy.id):
            raise Conflict("已有主机、端口、用户名都相同的代理")
        if "folder_id" in changed:
            await _folder_exists(session, changes["folder_id"])
        for key in changed:
            setattr(proxy, key, changes[key])
        if changed & {*ADDRESS, *CREDENTIALS}:
            proxy.changed(now, address=bool(changed & set(ADDRESS)))
        await _live(session)
        return (await _views(session, [proxy], now))[0]


@router.post("/proxies/{action}")
async def act(action: ProxyAction, body: Ids, database: DatabaseDep) -> Affected:
    """批量测试、测速、刷新、启用、停用或删除选中的代理。
    测速只对测试通过的代理生效；刷新只对填了刷新地址的动态代理生效。"""
    now = utcnow()
    async with database.transaction() as session:
        repo = ProxyRepository(session)
        proxies = await repo.of_ids(body.ids, lock=True)
        match action:
            case ProxyAction.TEST:
                for proxy in proxies:
                    proxy.request_test(now)
            case ProxyAction.SPEED:
                proxies = [proxy for proxy in proxies if proxy.status == ProxyStatus.AVAILABLE]
                for proxy in proxies:
                    proxy.speed_at = now
            case ProxyAction.REFRESH:
                proxies = [proxy for proxy in proxies if proxy.refresh_url]
                for proxy in proxies:
                    proxy.refresh(now)
            case ProxyAction.ENABLE | ProxyAction.DISABLE:
                for proxy in proxies:
                    proxy.enabled = action == ProxyAction.ENABLE
            case ProxyAction.DELETE:
                await repo.remove([proxy.id for proxy in proxies])
        await _live(session)
    return Affected(count=len(proxies))


@router.get("/proxy-folders")
async def list_folders(database: DatabaseDep) -> Folders:
    """文件夹按树的先后顺序排列，先父后子；带各自直接放着的代理数。"""
    async with database.transaction() as session:
        folders = tree_order(await FolderRepository(session).all())
        counts = await ProxyRepository(session).count_by_folder()
    views = [
        FolderView(
            id=folder.id,
            name=folder.name,
            parent_id=folder.parent_id,
            count=counts.get(folder.id, 0),
        )
        for folder in folders
    ]
    return Folders(folders=views, unfiled=counts.get(None, 0))


@router.post("/proxy-folders", status_code=201)
async def add_folder(body: FolderIn, database: DatabaseDep) -> FolderView:
    name = body.name.strip()
    async with database.transaction() as session:
        repo = FolderRepository(session)
        await _folder_exists(session, body.parent_id)
        siblings = await repo.siblings(body.parent_id)
        _unique(siblings, name)
        folder = ProxyFolder(name, body.parent_id, siblings[-1].position + 1 if siblings else 0)
        repo.add(folder)
        await _live(session)
    return FolderView(id=folder.id, name=folder.name, parent_id=folder.parent_id, count=0)


@router.patch("/proxy-folders/{folder_id}", status_code=204)
async def rename_folder(folder_id: UUID, body: FolderName, database: DatabaseDep) -> None:
    async with database.transaction() as session:
        repo = FolderRepository(session)
        folder = await repo.require(ProxyFolder, folder_id, lock=True)
        _unique(await repo.siblings(folder.parent_id), body.name.strip(), folder.id)
        folder.name = body.name.strip()
        await _live(session)


@router.post("/proxy-folders/{folder_id}/move", status_code=204)
async def move_folder(folder_id: UUID, body: FolderPlace, database: DatabaseDep) -> None:
    """拖动文件夹：放进另一个文件夹，或调整同级顺序。不能放进它自己下面。"""
    async with database.transaction() as session:
        repo = FolderRepository(session)
        folder = await repo.require(ProxyFolder, folder_id, lock=True)
        if body.parent_id in subtree(await repo.all(), folder.id):
            raise Invalid("不能把文件夹移到它自己下面")
        await _folder_exists(session, body.parent_id)
        siblings = [item for item in await repo.siblings(body.parent_id) if item.id != folder.id]
        _unique(siblings, folder.name)
        siblings.insert(min(body.position, len(siblings)), folder)
        folder.parent_id = body.parent_id
        for position, item in enumerate(siblings):
            item.position = position
        await _live(session)


@router.delete("/proxy-folders/{folder_id}", status_code=204)
async def delete_folder(folder_id: UUID, database: DatabaseDep) -> None:
    """只删文件夹本身：里面的代理和子文件夹移到上一级。"""
    async with database.transaction() as session:
        repo = FolderRepository(session)
        await repo.remove(await repo.require(ProxyFolder, folder_id, lock=True))
        await _live(session)


async def _views(session: AsyncSession, items: list[Proxy], now: datetime) -> list[ProxyView]:
    ids = [item.id for item in items]
    lines = await LineRepository(session).all()
    leases = LeaseRepository(session)
    in_use = await leases.in_use(now, by=proxy_leases.c.proxy_id, keys=ids)
    recent = await leases.recent(now, by=proxy_leases.c.proxy_id)
    views = []
    for item in items:
        states = []
        for use in Use:
            if serves(item.uses, use):
                line = lines.get((use, item.line)) if item.line else None
                states.append(
                    UseState(
                        use=use,
                        state=line.state(now) if line else LineState.OPEN,
                        cooldown_until=line and line.resume_at(),
                        reason=line and line.reason,
                    )
                )
        ok, failed = recent.get(item.id, (0, 0))
        views.append(
            ProxyView.of(
                item,
                now,
                states=states,
                in_use=in_use.get(item.id, 0),
                recent_ok=ok,
                recent_failed=failed,
            )
        )
    return views


def _normalized(values: dict) -> dict:
    """可选的文本留空即为没有。"""
    for key in ("private_key", "refresh_url"):
        if key in values:
            values[key] = (values[key] or "").strip() or None
    if "host" in values:
        values["host"] = values["host"].strip().strip("[]")
    return values


async def _folder_exists(session: AsyncSession, folder_id: UUID | None) -> None:
    if folder_id:
        await FolderRepository(session).require(ProxyFolder, folder_id)


def _unique(siblings: list[ProxyFolder], name: str, own: UUID | None = None) -> None:
    if any(item.name == name and item.id != own for item in siblings):
        raise Conflict(f"这一级已有名为 {name} 的文件夹")


async def _live(session: AsyncSession) -> None:
    await Live(session).proxies_changed()

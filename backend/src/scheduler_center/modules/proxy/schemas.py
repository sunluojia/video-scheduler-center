from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, Field, model_validator

from scheduler_center.modules.proxy.models import (
    LineState,
    PickMode,
    Proxy,
    ProxyProtocol,
    ProxyRule,
    ProxyStatus,
    RuleSource,
    RuleTargetKind,
    Service,
    Use,
)

Tag = Service | Use
"""用途标签：整个服务，或服务的某类任务。"""


def tidy(tags: list[Tag]) -> list[Tag]:
    """去重；选了整个服务时，不再单列它的任务。"""
    services = {tag for tag in tags if tag in Service}
    return list(
        dict.fromkeys(tag for tag in tags if tag in services or tag.split(".")[0] not in services)
    )


Tags = Annotated[list[Tag], AfterValidator(tidy)]


class ProxyIn(BaseModel):
    name: str = Field("", max_length=128)
    protocol: ProxyProtocol = ProxyProtocol.AUTO
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    username: str = Field("", max_length=255)
    password: str = Field("", max_length=1000)
    private_key: str | None = Field(None, max_length=20000)
    refresh_url: str | None = Field(None, max_length=2000)
    folder_id: UUID | None = None
    uses: Tags = Field(default_factory=list)
    bound_device: str | None = Field(None, min_length=1, max_length=128)


class ProxyPatch(BaseModel):
    """只改提交了的字段；页面上密码留空时不提交，表示不改。"""

    name: str | None = Field(None, max_length=128)
    protocol: ProxyProtocol | None = None
    host: str | None = Field(None, min_length=1, max_length=255)
    port: int | None = Field(None, ge=1, le=65535)
    username: str | None = Field(None, max_length=255)
    password: str | None = Field(None, max_length=1000)
    private_key: str | None = Field(None, max_length=20000)
    refresh_url: str | None = Field(None, max_length=2000)
    folder_id: UUID | None = None
    uses: Tags | None = None


class UseState(BaseModel):
    """代理所在线路在某个用途上的状态。"""

    use: Use
    state: LineState
    cooldown_until: datetime | None
    """冷却或限流结束、恢复使用的时间。"""
    reason: str | None


class ProxyView(BaseModel):
    id: UUID
    name: str
    protocol: ProxyProtocol
    host: str
    port: int
    username: str
    """脱敏后的用户名，只显示首尾字符。"""
    has_password: bool
    has_private_key: bool
    dynamic: bool
    """填了刷新地址；刷新地址本身可能带凭据，不返回。"""
    folder_id: UUID | None
    uses: list[Tag]
    states: list[UseState]
    """它能用于的每个用途上，所在线路的状态。"""
    enabled: bool
    status: ProxyStatus
    testing: bool
    """已排队等设备测试。"""
    error: str | None
    exit_ip: str | None
    ip_version: int | None
    line: str | None
    latency_ms: int | None
    tested_at: datetime | None
    tested_by: str | None
    bound_device: str | None = None
    speed_testing: bool
    """已排队等设备测速。"""
    speed_mbps: float | None
    parallel_mbps: float | None
    speed_tested_at: datetime | None
    speed_error: str | None
    in_use: int
    recent_ok: int
    recent_failed: int
    created_at: datetime

    @classmethod
    def of(cls, proxy: Proxy, now: datetime, **extra) -> Self:
        return cls(
            id=proxy.id,
            name=proxy.name,
            protocol=proxy.protocol,
            host=proxy.host,
            port=proxy.port,
            username=mask(proxy.username),
            has_password=bool(proxy.password),
            has_private_key=bool(proxy.private_key),
            dynamic=bool(proxy.refresh_url),
            folder_id=proxy.folder_id,
            uses=proxy.uses,
            enabled=proxy.enabled,
            status=proxy.status,
            testing=proxy.test_at is not None and proxy.test_at <= now,
            error=proxy.error,
            exit_ip=proxy.exit_ip,
            ip_version=proxy.ip_version,
            line=proxy.line,
            latency_ms=proxy.latency_ms,
            tested_at=proxy.tested_at,
            tested_by=proxy.tested_by,
            bound_device=proxy.bound_device,
            speed_testing=proxy.speed_at is not None,
            speed_mbps=proxy.speed_mbps,
            parallel_mbps=proxy.parallel_mbps,
            speed_tested_at=proxy.speed_tested_at,
            speed_error=proxy.speed_error,
            created_at=proxy.created_at,
            **extra,
        )


def mask(value: str) -> str:
    if len(value) <= 2:
        return "*" * len(value)
    return f"{value[0]}***{value[-1]}"


class ImportText(BaseModel):
    text: str = Field(max_length=500_000)
    folder_id: UUID | None = None
    """导入到这个文件夹。"""
    uses: Tags = Field(default_factory=list)


class ParsedRow(BaseModel):
    number: int
    """原文的行号。"""
    host: str | None = None
    port: int | None = None
    protocol: ProxyProtocol | None = None
    username: str | None = None
    has_password: bool = False
    error: str | None = None
    duplicate: bool = False
    """与已有代理或前面的行重复，导入时跳过。"""


class Imported(BaseModel):
    created: int
    skipped: int
    """无法识别或重复的行数。"""


class ProxyAction(StrEnum):
    TEST = "test"
    SPEED = "speed"
    REFRESH = "refresh"
    ENABLE = "enable"
    DISABLE = "disable"
    DELETE = "delete"


class Ids(BaseModel):
    ids: list[UUID] = Field(max_length=2000)


class Move(Ids):
    folder_id: UUID | None
    """为空是移出文件夹。"""


class Retag(Ids):
    """给选中的代理加上或去掉用途标签。"""

    add: list[Tag] = Field(default_factory=list)
    remove: list[Tag] = Field(default_factory=list)


class Affected(BaseModel):
    count: int


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    parent_id: UUID | None = None


class FolderName(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class FolderPlace(BaseModel):
    """拖动文件夹：放到哪个文件夹下、排第几（从 0 起）。"""

    parent_id: UUID | None
    position: int = Field(ge=0)


class FolderView(BaseModel):
    id: UUID
    name: str
    parent_id: UUID | None
    count: int
    """直接放在这个文件夹里的代理数。"""


class Folders(BaseModel):
    folders: list[FolderView]
    """按树的先后顺序，先父后子。"""
    unfiled: int
    """没放进文件夹的代理数。"""


class RuleTargetIn(BaseModel):
    kind: RuleTargetKind
    id: UUID
    priority: int = Field(1, ge=1, le=2_147_483_647)
    weight: int = Field(1, ge=1, le=2_147_483_647)


class RuleTargetView(RuleTargetIn):
    label: str | None


class RuleIn(BaseModel):
    source: RuleSource
    folder_id: UUID | None = None
    local_port: int | None = Field(None, ge=1, le=65535)
    targets: list[RuleTargetIn] | None = None

    @model_validator(mode="after")
    def complete(self) -> Self:
        if self.source == RuleSource.LOCAL_PORT and not self.local_port:
            raise ValueError("请填写本机端口")
        if self.source == RuleSource.POOL and self.targets is not None:
            if self.folder_id is not None:
                raise ValueError("请用 targets 设置所选分组和代理")
            if len({(target.kind, target.id) for target in self.targets}) != len(self.targets):
                raise ValueError("所选分组或代理重复")
        return self


class DeviceRuleIn(RuleIn):
    line_limit: int | None = Field(None, ge=1, le=40)


class ClusterRuleIn(RuleIn):
    mode: PickMode = PickMode.PRIORITY
    line_limit: int = Field(2, ge=1, le=40)
    """每条线路同时使用数。"""

    @model_validator(mode="after")
    def not_local(self) -> Self:
        if self.source == RuleSource.LOCAL_PORT:
            raise ValueError("本机端口只能给单台设备设置")
        return self


class RuleView(BaseModel):
    source: RuleSource
    folder_id: UUID | None
    local_port: int | None
    mode: PickMode
    line_limit: int
    device_line_limit: int | None = None
    targets: list[RuleTargetView] | None
    updated_at: datetime

    @classmethod
    def of(cls, rule: ProxyRule, labels: dict[tuple[RuleTargetKind, str], str]) -> Self:
        return cls(
            source=rule.source,
            folder_id=rule.folder_id,
            local_port=rule.local_port,
            mode=rule.mode,
            line_limit=rule.device_line_limit or rule.line_limit,
            device_line_limit=rule.device_line_limit,
            targets=(
                [
                    RuleTargetView(**target, label=labels.get((target["kind"], target["id"])))
                    for target in rule.targets
                ]
                if rule.targets is not None
                else None
            ),
            updated_at=rule.updated_at,
        )


class LineView(BaseModel):
    key: str
    kind: Literal["pool", "direct", "port"]
    device: str | None
    exit_ip: str | None
    proxies: list[str]
    """归并到这条线路的代理名称。"""
    state: LineState
    score: float
    cooldown_until: datetime | None
    """冷却或限流结束、恢复使用的时间。"""
    reason: str | None
    in_use: int
    limit: int
    recent_ok: int
    recent_failed: int
    tested_at: datetime | None


class DeviceRow(BaseModel):
    instance_id: str
    online: bool
    last_seen_at: datetime | None
    """为空表示有例外设置，但设备还没注册过。"""
    rule: RuleView | None
    """这台设备在这个用途上的例外；为空表示跟随集群。"""
    in_use: int
    line: LineView | None
    """直连或本机端口时，设备自己的线路。"""


class UseView(BaseModel):
    """一个用途：集群规则、线路和设备，页面顶部的用途卡片用它。"""

    key: Use
    service: Service
    service_name: str
    name: str
    rule: RuleView
    proxies: int
    """代理池里能用于这个用途的可用代理数。"""
    lines: list[LineView]
    devices: list[DeviceRow]

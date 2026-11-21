"""与爬取程序之间的约定：申请和归还线路，以及代为测试代理。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from scheduler_center.modules.proxy.dispatcher import Grant
from scheduler_center.modules.proxy.models import (
    CheckResult,
    ListingResult,
    Outcome,
    Proxy,
    ProxyProtocol,
    Use,
)


class LeaseRequest(BaseModel):
    device: str = Field(min_length=1, max_length=128)
    """申请的设备，即执行程序注册时的实例标识。"""
    use: Use
    """用途：获取信息、下载视频等，线路好坏和并发按用途分开算。"""
    task_id: UUID | None = None
    """为哪个任务申请，只用于记录。"""
    avoid: list[str] = Field(default_factory=list, max_length=50)
    """这次任务已经失败过的线路；没有别的线路时仍会分到它们。"""


class Route(BaseModel):
    """怎么连：经池里的代理、直连，或者设备本机的代理端口。"""

    kind: Literal["proxy", "direct", "local_port"]
    protocol: ProxyProtocol | None = None
    """auto 表示还没确定，设备依次试 SOCKS5 和 HTTP。"""
    host: str | None = None
    port: int | None = None
    username: str = ""
    password: str = ""
    private_key: str | None = None
    host_key: str | None = None
    """SSH 主机指纹，与记录不符时按认证失败报告。"""

    @classmethod
    def of(cls, grant: Grant) -> Route:
        if proxy := grant.proxy:
            return cls(
                kind="proxy",
                protocol=proxy.protocol,
                host=proxy.host,
                port=proxy.port,
                username=proxy.username,
                password=proxy.password,
                private_key=proxy.private_key,
                host_key=proxy.host_key,
            )
        if grant.local_port:
            return cls(kind="local_port", port=grant.local_port)
        return cls(kind="direct")


class LeaseGrant(BaseModel):
    lease_id: UUID
    line: str
    """线路标识；重试时放进 avoid 可以避开它。"""
    probe: bool
    """试探用的线路：它刚冷却到期，这次的表现决定它是否恢复。"""
    route: Route


class LeaseReply(BaseModel):
    """分到线路时 lease 有值；否则按 retry_after 秒后再来，reason 是原因。"""

    lease: LeaseGrant | None = None
    retry_after: int | None = None
    reason: str | None = None


class LeaseResult(BaseModel):
    outcome: Outcome
    detail: str | None = Field(None, max_length=500)
    """失败的步骤和原因，页面上显示。"""
    retry_after: int | None = Field(None, ge=1, le=2_147_483_647)
    """限流时网站给的 Retry-After 秒数。"""
    listing_result: ListingResult | None = None
    """采集端的作品列表读取结果。"""


class CheckItem(BaseModel):
    key: str
    protocol: ProxyProtocol
    host: str | None
    """为空表示设备本机。"""
    port: int
    username: str = ""
    password: str = ""
    private_key: str | None = None
    host_key: str | None = None
    refresh_url: str | None = None
    """动态代理：先请求这个地址刷新出口，再测试。"""

    @classmethod
    def of(cls, proxy: Proxy, *, refresh: bool) -> CheckItem:
        return cls(
            key=str(proxy.id),
            protocol=proxy.protocol,
            host=proxy.host,
            port=proxy.port,
            username=proxy.username,
            password=proxy.password,
            private_key=proxy.private_key,
            host_key=proxy.host_key,
            refresh_url=proxy.refresh_url if refresh else None,
        )


class CheckRequest(BaseModel):
    echo_url: str
    """经代理请求这个地址，响应正文是出口 IP。"""
    items: list[CheckItem]


class SpeedRequest(BaseModel):
    url: str
    """经代理下载这个地址测速，响应是固定大小的数据。"""
    size: int
    """测速文件的字节数：只认完整的 200 响应，收到的字节数要和它一致。"""
    streams: int
    """先单路下载一次，再这么多路同时下载。"""
    item: CheckItem


class SpeedReply(BaseModel):
    key: str
    single_mbps: float | None = None
    parallel_mbps: float | None = None
    error: str | None = None
    """没下完整时的原因。"""


class CheckReply(BaseModel):
    key: str
    result: CheckResult
    protocol: ProxyProtocol | None = None
    """实际握手成功的协议。"""
    exit_ip: str | None = None
    latency_ms: int | None = None
    host_key: str | None = None
    error: str | None = None

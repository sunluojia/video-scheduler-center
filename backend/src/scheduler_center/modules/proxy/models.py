"""代理：通用的代理池（文件夹、用途标签）、各用途的规则，以及按出口划分的线路和线路租约。

服务每次要访问外网前按用途申请一条线路，用完归还并报告线路表现；调度中心据此控制每条线路在
整个集群里同时使用的数量，避开出问题的线路，过一阵放一个申请去试探它是否恢复。
线路好坏按用途分开记：不同用途访问的网站不同，一个出口下载视频被封，照样可以用来获取信息。
"""

import ipaddress
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal, TypedDict
from uuid import UUID, uuid4

from scheduler_center.core.common.clock import utcnow

CLUSTER = "*"
"""集群规则的设备标识：没有单独配置的设备都用它。"""
LEASE_TTL = timedelta(seconds=60)
"""租约随爬取程序的心跳续期，超过这个时长没有续期即收回。"""
MIN_COOLDOWN, MAX_COOLDOWN = 60, 1800
"""冷却从 60 秒起，每次试探失败翻倍，最长 30 分钟。"""
SCORE_LIMIT = 3
"""错误分到这个值，线路进入冷却。"""


class Service(StrEnum):
    """会用代理的服务。"""

    CRAWLER = "crawler"


class Use(StrEnum):
    """用途：哪个服务的哪类任务。"""

    CRAWL = "crawler.crawl_creator"
    DOWNLOAD = "crawler.download_video"

    @property
    def service(self) -> Service:
        return Service(self.split(".")[0])


SERVICE_NAMES = {Service.CRAWLER: "爬取程序"}
USE_NAMES = {Use.CRAWL: "获取信息", Use.DOWNLOAD: "下载视频"}


def serves(tags: list[str], use: Use) -> bool:
    """代理的用途标签是否包含这个用途：标签可以是整个服务，也可以是具体任务。"""
    return use in tags or use.service in tags


class ProxyProtocol(StrEnum):
    AUTO = "auto"
    """没写协议：首次测试时依次试 SOCKS5 和 HTTP 握手，按成功的写回。"""
    HTTP = "http"
    HTTPS = "https"
    SOCKS5 = "socks5"
    SSH = "ssh"


class ProxyStatus(StrEnum):
    UNTESTED = "untested"
    AVAILABLE = "available"
    UNREACHABLE = "unreachable"
    """连不上代理或握手失败；按退避时间自动复测。"""
    AUTH_FAILED = "auth_failed"
    """认证失败；更新凭据或手动测试通过才恢复。"""
    REFRESHING = "refreshing"
    """动态代理等待排空或正在刷新出口。"""


class CheckResult(StrEnum):
    OK = "ok"
    UNREACHABLE = "unreachable"
    AUTH_FAILED = "auth_failed"
    ECHO_FAILED = "echo_failed"
    """代理可用，但回显服务没有返回出口；回显服务自己也可能出问题，不判代理坏。"""


class RuleSource(StrEnum):
    DIRECT = "direct"
    POOL = "pool"
    """代理池里带这个用途的代理，可限定分组和单个代理。"""
    LOCAL_PORT = "local_port"
    """设备本机的代理端口，只能在设备规则里使用。"""


class PickMode(StrEnum):
    PRIORITY = "priority"
    WEIGHTED = "weighted"
    ROTATE = "rotate"


class Outcome(StrEnum):
    """归还线路时报告的表现。"""

    NORMAL = "normal"
    SUSPECT = "suspect"
    PROXY_DOWN = "proxy_down"
    """代理连不上，爬取程序已复测过一次。"""
    AUTH_FAILED = "auth_failed"
    THROTTLED = "throttled"


def exit_line(ip: str) -> str:
    """出口 IP 所属的线路：IPv4 按地址；IPv6 按 /64 网段，CDN 通常按 /64 识别 IPv6 用户。"""
    address = ipaddress.ip_address(ip)
    if address.version == 4:
        return str(address)
    return str(ipaddress.ip_network(f"{address}/64", strict=False))


def _is_ip(value: str | None) -> bool:
    try:
        ipaddress.ip_address(value or "")
    except ValueError:
        return False
    return True


def device_line(device: str, local_port: int | None = None) -> str:
    """直连和本机端口各算设备自己的一条线路。"""
    return f"port:{device}:{local_port}" if local_port else f"direct:{device}"


def _jittered(seconds: float) -> timedelta:
    """等待时间加减 20% 的随机量，免得一批线路或代理同时到期。"""
    return timedelta(seconds=seconds * random.uniform(0.8, 1.2))


def _next_backoff(backoff: int) -> int:
    return min(max(backoff * 2, MIN_COOLDOWN), MAX_COOLDOWN)


@dataclass
class Proxy:
    """代理池里的一条远程代理。"""

    host: str
    port: int
    protocol: ProxyProtocol = ProxyProtocol.AUTO
    username: str = ""
    password: str = ""
    private_key: str | None = None
    """SSH 私钥；与密码二选一。"""
    refresh_url: str | None = None
    """动态代理的刷新地址；为空是静态代理。"""
    name: str = ""
    folder_id: UUID | None = None
    uses: list[str] = field(default_factory=list)
    """用途标签：服务或具体任务，见 serves。"""
    id: UUID = field(default_factory=uuid4)
    enabled: bool = True
    status: ProxyStatus = ProxyStatus.UNTESTED
    error: str | None = None
    exit_ip: str | None = None
    line: str | None = None
    latency_ms: int | None = None
    host_key: str | None = None
    """SSH 主机指纹，首次连接时记下；之后变化按认证失败处理。"""
    tested_at: datetime | None = None
    tested_by: str | None = None
    test_at: datetime | None = None
    """到这个时间由 Worker 交给在线设备测试；为空表示不需要测试。"""
    testing_until: datetime | None = None
    """Worker 领走测试的到期时间，也是领取凭证：写回结果时核对它，改过配置或被别的 Worker
    重新领走后，迟到的旧结果不算；Worker 中途退出时到期重新领。"""
    backoff: int = 0
    """连续不通时的复测间隔（秒）。"""
    speed_at: datetime | None = None
    """到这个时间由 Worker 交给在线设备测速；为空表示不需要测速。"""
    speed_mbps: float | None = None
    """单路下载速度。"""
    parallel_mbps: float | None = None
    """几路同时下载的合计速度。"""
    speed_tested_at: datetime | None = None
    speed_error: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    config_revision: UUID | None = None
    """改过连接配置或开始刷新出口时更新，用来识别旧租约。"""

    @property
    def usable(self) -> bool:
        return self.enabled and self.status == ProxyStatus.AVAILABLE and self.line is not None

    @property
    def ip_version(self) -> int | None:
        return ipaddress.ip_address(self.exit_ip).version if self.exit_ip else None

    def request_test(self, now: datetime) -> None:
        self.test_at = now

    def speed_tested(
        self, now: datetime, single: float | None, parallel: float | None, error: str | None
    ) -> None:
        """测速结果只记录，不改代理状态：测速地址自己也可能拒绝请求。"""
        self.speed_at, self.speed_tested_at = None, now
        self.speed_mbps, self.parallel_mbps, self.speed_error = single, parallel, error

    def changed(self, now: datetime, *, address: bool) -> None:
        """地址或凭据改过：重新测试，正在进行的测试和测速作废；地址变了，原来的出口和速度也作废。"""
        self.config_revision = uuid4()
        self.status, self.error, self.backoff = ProxyStatus.UNTESTED, None, 0
        self.testing_until = self.speed_at = None
        if address:
            self.exit_ip = self.line = None
            self.speed_mbps = self.parallel_mbps = self.speed_tested_at = self.speed_error = None
        self.request_test(now)

    def refresh(self, now: datetime) -> None:
        """动态代理刷新出口：先排空，Worker 等它的线路都归还后再刷新并测试。"""
        if not self.refresh_url:
            return
        if self.status != ProxyStatus.REFRESHING:
            self.status = ProxyStatus.REFRESHING
            self.config_revision = uuid4()
            self.testing_until = None
        self.request_test(now)

    def tested(
        self,
        result: CheckResult,
        now: datetime,
        device: str,
        *,
        protocol: ProxyProtocol | None = None,
        exit_ip: str | None = None,
        latency_ms: int | None = None,
        host_key: str | None = None,
        error: str | None = None,
    ) -> None:
        if result == CheckResult.OK and not _is_ip(
            exit_ip
        ):  # 回显服务返回了别的内容，只是没测出出口
            result, error = (
                CheckResult.ECHO_FAILED,
                f"回显内容不是 IP 地址：{(exit_ip or '')[:60]!r}",
            )
        self.tested_at, self.tested_by, self.test_at, self.testing_until = now, device, None, None
        self.error, self.latency_ms = error, latency_ms
        if protocol and protocol != ProxyProtocol.AUTO:
            self.protocol = protocol
        self.host_key = host_key or self.host_key
        match result:
            case CheckResult.OK:
                self.status, self.backoff = ProxyStatus.AVAILABLE, 0
                self.exit_ip, self.line = exit_ip, exit_ip and exit_line(exit_ip)
            case CheckResult.ECHO_FAILED:
                # 代理本身是通的；出口未知时还不能分配，稍后再测
                self.status = ProxyStatus.AVAILABLE if self.line else ProxyStatus.UNTESTED
                if not self.line:
                    self._retest_later(now)
            case CheckResult.AUTH_FAILED:
                self.auth_failed(error)
            case CheckResult.UNREACHABLE:
                self.unreachable(now, error)

    def unreachable(self, now: datetime, error: str | None) -> None:
        self.status, self.error = ProxyStatus.UNREACHABLE, error
        self._retest_later(now)

    def auth_failed(self, error: str | None) -> None:
        self.status, self.error, self.test_at = ProxyStatus.AUTH_FAILED, error, None

    def worked(self) -> None:
        self.backoff = 0

    def _retest_later(self, now: datetime) -> None:
        self.backoff = _next_backoff(self.backoff)
        self.test_at = now + _jittered(self.backoff)


@dataclass
class ProxyFolder:
    """代理池的文件夹，可以嵌套；优先级按文件夹的先后顺序。"""

    name: str
    parent_id: UUID | None = None
    position: int = 0
    id: UUID = field(default_factory=uuid4)


def tree_order(folders: list[ProxyFolder]) -> list[ProxyFolder]:
    """按树的先后顺序排列：先父后子，同级按位置。"""
    children: dict[UUID | None, list[ProxyFolder]] = defaultdict(list)
    for folder in sorted(folders, key=lambda item: item.position):
        children[folder.parent_id].append(folder)
    ordered: list[ProxyFolder] = []

    def walk(parent: UUID | None) -> None:
        for folder in children[parent]:
            ordered.append(folder)
            walk(folder.id)

    walk(None)
    return ordered


def subtree(folders: list[ProxyFolder], root: UUID) -> set[UUID]:
    """这个文件夹和它下面的所有文件夹。"""
    found = {root}
    for folder in tree_order(folders):
        if folder.parent_id in found:
            found.add(folder.id)
    return found


type RuleTargetKind = Literal["folder", "proxy"]


class RuleTarget(TypedDict):
    kind: RuleTargetKind
    id: str
    priority: int
    weight: int


@dataclass
class ProxyRule:
    """某个用途的集群规则，或某台设备在这个用途上的例外。设备没有例外时跟随集群规则。"""

    use: Use
    device: str
    source: RuleSource = RuleSource.DIRECT
    folder_id: UUID | None = None
    """旧版规则选的单个文件夹。"""
    local_port: int | None = None
    mode: PickMode = PickMode.PRIORITY
    """先用哪条和每条线路的同时数只在集群规则里设置。"""
    line_limit: int = 2
    cursor: str | None = None
    """轮询时上一次分配的线路。"""
    updated_at: datetime = field(default_factory=utcnow)
    targets: list[RuleTarget] | None = None
    """显式选择的分组和代理。None 时沿用 folder_id 或整个代理池，空列表是空范围。"""


class LineState(StrEnum):
    OPEN = "open"
    COOLING = "cooling"
    PROBING = "probing"
    """冷却到期，只放一个申请去试探。"""
    THROTTLED = "throttled"


@dataclass
class Line:
    """一个用途上的一条线路：池里的代理按出口归并；直连和本机端口是设备自己的线路。"""

    use: Use
    key: str
    device: str | None = None
    """设备自己的线路所属的设备；池里的线路为空。"""
    score: float = 0
    cooldown_until: datetime | None = None
    """失败冷却到这个时间。"""
    backoff: int = 0
    """失败冷却的时长（秒），不为零表示处在冷却和试探的循环里。"""
    probe_at: datetime | None = None
    """进入冷却或提前试探的时间；线路都在冷却时，最多每 60 秒提前试探一次。"""
    throttled_until: datetime | None = None
    """平台限流到这个时间：期间普通申请和提前试探都不放，旧租约报正常也不提前解除。"""
    reason: str | None = None
    exit_ip: str | None = None
    """本机端口测到的出口。"""
    tested_at: datetime | None = None
    updated_at: datetime = field(default_factory=utcnow)

    def state(self, now: datetime) -> LineState:
        if self.throttled_until and self.throttled_until > now:
            return LineState.THROTTLED
        if self.cooldown_until and self.cooldown_until > now:
            return LineState.COOLING
        return LineState.PROBING if self.backoff else LineState.OPEN

    def resume_at(self) -> datetime | None:
        """限流和冷却都结束的时间。"""
        times = [time for time in (self.throttled_until, self.cooldown_until) if time]
        return max(times, default=None)

    def report(
        self, outcome: Outcome, now: datetime, *, probe: bool, detail: str | None = None
    ) -> None:
        """一次使用的表现。正常减 0.5 分，可疑加 1 分，到 3 分冷却；代理不通直接冷却。"""
        self.updated_at = now
        if outcome == Outcome.NORMAL:
            self.score = max(self.score - 0.5, 0)
            if probe:
                self.score, self.backoff, self.cooldown_until = 0, 0, None
                if self.state(now) != LineState.THROTTLED:
                    self.reason = None
            return
        self.reason = detail or self.reason
        if probe:
            self._cool(now, _next_backoff(self.backoff))
        elif self.state(now) == LineState.OPEN:
            self.score += 1
            if outcome != Outcome.SUSPECT or self.score >= SCORE_LIMIT:
                self._cool(now, MIN_COOLDOWN)

    def throttle(self, now: datetime, seconds: int, detail: str | None = None) -> None:
        """平台限流：按 Retry-After 等待，期间一个申请都不放。限流单独记，不碰失败冷却：
        到期后回到原来的状态，原来在冷却和试探循环里的，照旧只放一个申请去试探。"""
        until = now + timedelta(seconds=seconds)
        self.throttled_until = max(self.throttled_until or until, until)
        self.reason, self.updated_at = detail or "被限流", now

    def _cool(self, now: datetime, seconds: int) -> None:
        """进入冷却；提前试探也要从这时起至少隔 60 秒。"""
        self.score, self.backoff, self.probe_at = 0, seconds, now
        self.cooldown_until = now + _jittered(seconds)


@dataclass
class Lease:
    """一次使用线路的租约：申请时发出，归还时附上表现。"""

    device: str
    use: Use
    line: str
    proxy_id: UUID | None
    task_id: UUID | None = None
    probe: bool = False
    """试探用的租约：成功则线路恢复，失败则冷却时间翻倍。"""
    id: UUID = field(default_factory=uuid4)
    acquired_at: datetime = field(default_factory=utcnow)
    expires_at: datetime = field(default_factory=lambda: utcnow() + LEASE_TTL)
    released_at: datetime | None = None
    outcome: Outcome | None = None
    detail: str | None = None
    proxy_revision: UUID | None = None

    def renew(self, now: datetime) -> None:
        self.expires_at = now + LEASE_TTL

    def release(self, outcome: Outcome, detail: str | None, now: datetime) -> None:
        self.outcome, self.detail, self.released_at = outcome, detail, now

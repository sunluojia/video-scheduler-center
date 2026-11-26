from pathlib import Path
from typing import Annotated, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """进程运行配置，读取 SC_ 前缀的环境变量或 .env；每个进程只读取自己需要的部分。"""

    model_config = SettingsConfigDict(env_prefix="SC_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://scheduler:scheduler@localhost:5432/scheduler_center"
    redis_url: str = "redis://localhost:6379/0"
    queue_namespace: str = "dramatiq"
    """与爬取程序、分析服务使用同一个 Redis 和命名空间。"""
    legacy_crawler_dispatch: bool = False
    """使用旧版 Redis 爬虫派单；切换时 API、Worker、事件接收进程应使用相同配置。"""
    crawl_policy_enabled: bool = False
    crawl_policy_devices: tuple[Annotated[str, Field(min_length=1, max_length=128)], ...] = ()
    crawl_policy_exits: tuple[Annotated[str, Field(min_length=1, max_length=160)], ...] = ()
    crawl_policy_epoch: str = Field(default="", max_length=128)
    crawl_pacing_min_seconds: int = Field(default=45, ge=0)
    crawl_empty_backoff_steps: tuple[Annotated[int, Field(gt=0)], ...] = (60, 180, 360, 600)
    crawl_empty_window_seconds: int = Field(default=300, gt=0)
    crawl_empty_tasks: int = Field(default=3, gt=0)
    crawl_empty_warm_guard: bool = True
    crawl_warm_bootstrap_min_seconds: int = Field(default=90, gt=0)
    material_root: Path = Path("data/materials")
    """爬取程序上传的原视频，API 与 Worker 共享。"""
    heartbeat_timeout_seconds: float = 60
    """执行程序超过这个时长没有心跳视为离线。"""
    proxy_echo_url: str = "https://api64.ipify.org"
    """测试代理时经代理请求这个地址，响应正文是出口 IP；这个地址能返回 IPv6。"""
    proxy_speed_url: str = "https://speed.cloudflare.com/__down?bytes=10000000"
    """测速时经代理下载这个地址，响应是 10 MB 数据；Cloudflare 测速页用的就是这个接口。"""
    proxy_speed_size: int = 10_000_000
    """测速文件的字节数，换测速地址时一起改；收到的字节数对不上就算测速失败。"""
    log_level: str = "INFO"

    @model_validator(mode="after")
    def validate_crawl_policy(self) -> Self:
        if not self.crawl_policy_enabled:
            return self
        if tuple(sorted(set(self.crawl_empty_backoff_steps))) != self.crawl_empty_backoff_steps:
            raise ValueError("SC_CRAWL_EMPTY_BACKOFF_STEPS must be strictly increasing")
        if not (self.crawl_pacing_min_seconds or self.crawl_empty_backoff_steps):
            return self
        if self.legacy_crawler_dispatch:
            raise ValueError("SC_CRAWL_POLICY_ENABLED requires PostgreSQL task claims")
        scopes = (*self.crawl_policy_devices, *self.crawl_policy_exits)
        if (
            not self.crawl_policy_epoch.strip()
            or not self.crawl_policy_devices
            or not self.crawl_policy_exits
            or any(not value.strip() or "*" in value for value in scopes)
        ):
            raise ValueError("Crawl policy requires an epoch and explicit device and exit lists")
        return self

    def crawl_policy_applies(self, device: str) -> bool:
        return (
            self.crawl_policy_enabled
            and bool(self.crawl_pacing_min_seconds or self.crawl_empty_backoff_steps)
            and device in self.crawl_policy_devices
        )


class WorkerSettings(Settings):
    service_token: SecretStr = Field(min_length=16)
    """请在线的爬取程序测试代理时使用。"""
    poll_interval_seconds: float = 1
    reconcile_after_minutes: float = 30
    """已发布的任务超过这个时长仍未受理，重新投递。"""
    cleanup_enabled: bool = True
    cleanup_interval_minutes: float = 10


class ApiSettings(Settings):
    api_host: str = "127.0.0.1"
    """默认只监听本机；部署时改为 0.0.0.0，供执行程序经局域网访问。"""
    api_port: int = 8080
    service_token: SecretStr = Field(min_length=16)
    """与爬取程序、分析服务互相调用的服务凭据。"""
    static_root: Path = Path("../frontend/dist")
    """前端构建产物；目录不存在时只提供接口。"""

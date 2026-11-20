from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from scheduler_center.core.common.clock import utcnow

CRAWL_SLOTS, DOWNLOAD_SLOTS, WARM_SLOTS = 2, 4, 1
"""爬取程序默认同时采集 2 个达人、下载 4 条视频、预热 1 个身份。"""


class ExecutorKind(StrEnum):
    CRAWLER = "crawler"
    ANALYSIS = "analysis"

    @property
    def label(self) -> str:
        return {"crawler": "爬取程序", "analysis": "分析服务"}[self]


@dataclass
class Executor:
    """注册到调度中心的执行程序实例，注册请求兼作心跳。"""

    kind: ExecutorKind
    instance_id: str
    url: str
    version: str
    registered_at: datetime = field(default_factory=utcnow)
    last_seen_at: datetime | None = field(default_factory=utcnow)
    withdrawn_at: datetime | None = None
    identities: dict | None = None
    """爬取程序最近一次心跳带来的身份池快照，账号池页面显示。"""
    crawl_slots: int = CRAWL_SLOTS
    download_slots: int = DOWNLOAD_SLOTS
    warm_slots: int = WARM_SLOTS
    """爬取程序的并发，执行程序页设置，随心跳回复下发；分析服务不用。"""

    def online(self, now: datetime, timeout: timedelta) -> bool:
        return (
            self.withdrawn_at is None
            and self.last_seen_at is not None
            and now - self.last_seen_at < timeout
        )

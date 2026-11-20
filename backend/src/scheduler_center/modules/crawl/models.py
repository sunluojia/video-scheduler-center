from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from scheduler_center.core.common.clock import utcnow


class CrawlerTaskKind(StrEnum):
    CRAWL = "crawl"
    """爬取任务：锁定达人的最新作品，每位达人一个。"""
    DOWNLOAD = "download"
    """下载任务：下载一条视频并上传，每条视频一个。"""


class CrawlerTaskStatus(StrEnum):
    PUBLISHED = "published"
    ACCEPTED = "accepted"
    SUCCEEDED = "succeeded"
    """爬取任务得出结论，或下载任务的视频已上传。"""
    FAILED = "failed"
    CANCELLED = "cancelled"


class CrawlConclusion(StrEnum):
    OK = "ok"
    PRIVATE = "private"
    NOT_FOUND = "not_found"
    FAILED = "failed"


@dataclass
class CrawlerTask:
    """发给爬取程序的任务。重新爬取、重新下载时沿用任务 ID，轮次加一。"""

    kind: CrawlerTaskKind
    batch_id: UUID
    creator_id: UUID
    message: dict
    """最近一次发布的消息，对账时原样重发。"""
    work_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)
    run: int = 1
    status: CrawlerTaskStatus = CrawlerTaskStatus.PUBLISHED
    conclusion: CrawlConclusion | None = None
    error: str | None = None
    published_at: datetime = field(default_factory=utcnow)
    accepted_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.status in (CrawlerTaskStatus.PUBLISHED, CrawlerTaskStatus.ACCEPTED)

    def republish(self, now: datetime) -> None:
        """新一轮：爬取程序丢弃上一轮的进度。消息由调用方按新轮次生成。"""
        self.run += 1
        self.status, self.published_at = CrawlerTaskStatus.PUBLISHED, now
        self.conclusion = self.error = self.accepted_at = self.finished_at = None

    def accept(self, now: datetime) -> None:
        if self.status == CrawlerTaskStatus.PUBLISHED:
            self.status, self.accepted_at = CrawlerTaskStatus.ACCEPTED, now

    def finish(self, status: CrawlerTaskStatus, now: datetime, error: str | None = None) -> None:
        self.status, self.error, self.finished_at = status, error, now

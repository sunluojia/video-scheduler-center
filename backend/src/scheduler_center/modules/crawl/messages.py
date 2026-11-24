"""与爬取程序约定的任务和事件，见设计文档第 4 节。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter, field_validator

from scheduler_center.core.messaging.outbox import Route
from scheduler_center.modules.batch.models import MAX_WORKS, WorkKind
from scheduler_center.modules.crawl.models import CrawlConclusion

CRAWL_CREATOR = Route("crawler.tasks", "crawl_creator")
DOWNLOAD_VIDEO = Route("crawler.tasks", "download_video")
CRAWLER_EVENTS = Route("crawler.events", "crawler_event")


class CreatorRef(BaseModel):
    handle: str
    oecuid: str | None


class CrawlCreator(BaseModel):
    task_id: UUID
    run: int
    batch_id: UUID
    creator: CreatorRef
    cutoff: datetime
    """只取此前发布的作品。"""
    max_works: int = MAX_WORKS


class WorkRef(BaseModel):
    work_id: str
    url: str


class DownloadVideo(BaseModel):
    task_id: UUID
    run: int
    batch_id: UUID
    creator: CreatorRef
    work: WorkRef


class CrawledWork(BaseModel):
    position: int = Field(ge=1, le=MAX_WORKS)
    work_id: str = Field(min_length=1, max_length=64)
    url: str = Field(min_length=1)
    type: WorkKind
    published_at: datetime | None = None
    description: str = ""
    topics: list[str] = []


class CrawledProfile(BaseModel):
    """爬取程序核实过的达人资料；读不到的字段为空。"""

    handle: str = Field(min_length=1, max_length=64)
    url: str = Field(min_length=1)
    nickname: str | None = None
    platform_user_id: str | None = Field(None, max_length=64)


class EventBase(BaseModel):
    event_id: UUID
    task_id: UUID
    run: int = Field(ge=1)
    occurred_at: datetime
    executor_id: str | None = Field(default=None, max_length=128)
    executor_session_id: UUID | None = None
    attempt_id: UUID | None = None


class Accepted(EventBase):
    type: Literal["accepted"]


class Crawled(EventBase):
    type: Literal["crawled"]
    conclusion: CrawlConclusion
    reason: str | None = None
    profile: CrawledProfile | None = None
    works: list[CrawledWork] = Field(default=[], max_length=MAX_WORKS)

    @field_validator("works")
    @classmethod
    def _positions(cls, works: list[CrawledWork]) -> list[CrawledWork]:
        works = sorted(works, key=lambda work: work.position)
        if [work.position for work in works] != list(range(1, len(works) + 1)):
            raise ValueError("作品位置须从 1 开始连续编号")
        return works


class DownloadFailed(EventBase):
    type: Literal["download_failed"]
    reason: str


type CrawlerEvent = Annotated[Accepted | Crawled | DownloadFailed, Field(discriminator="type")]
crawler_event = TypeAdapter(CrawlerEvent)


class Uploaded(EventBase):
    """视频上传成功。上传走 HTTP，不经队列，调度中心收下视频时自己记一条，留作经过和统计。"""

    type: Literal["uploaded"] = "uploaded"
    size: int

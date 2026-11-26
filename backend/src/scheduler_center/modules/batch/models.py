from calendar import monthrange
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from scheduler_center.core.common.clock import BEIJING, utcnow
from scheduler_center.core.common.errors import Conflict

MAX_WORKS = 5
"""每位达人锁定的最新作品数。"""


class BatchStatus(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class Batch:
    name: str
    source: str
    """来源：上传的文件名，或“粘贴链接”。"""
    id: UUID = field(default_factory=uuid4)
    status: BatchStatus = BatchStatus.DRAFT
    created_at: datetime = field(default_factory=utcnow)
    started_at: datetime | None = None
    """开始时间，也是爬取的截止时间：只取此前发布的作品。"""
    finished_at: datetime | None = None

    def start(self, now: datetime) -> None:
        if self.status != BatchStatus.DRAFT:
            raise Conflict("只有草稿批次可以开始")
        self.status = BatchStatus.RUNNING
        self.started_at = now

    def pause(self) -> None:
        if self.status != BatchStatus.RUNNING:
            raise Conflict("只有进行中的批次可以暂停")
        self.status = BatchStatus.PAUSED

    def resume(self) -> None:
        if self.status != BatchStatus.PAUSED:
            raise Conflict("只有已暂停的批次可以继续")
        self.status = BatchStatus.RUNNING

    def cancel(self, now: datetime) -> None:
        if self.status in (BatchStatus.COMPLETED, BatchStatus.CANCELLED):
            raise Conflict("批次已经结束")
        self.status = BatchStatus.CANCELLED
        self.finished_at = now

    def complete(self, now: datetime) -> None:
        self.status = BatchStatus.COMPLETED
        self.finished_at = now

    def reopen(self) -> None:
        """对已开始的批次重新爬取、下载或分析：已完成的批次回到进行中。"""
        if self.status in (BatchStatus.DRAFT, BatchStatus.CANCELLED):
            raise Conflict("批次未开始或已取消")
        if self.status == BatchStatus.COMPLETED:
            self.status = BatchStatus.RUNNING
            self.finished_at = None

    @property
    def deletable(self) -> bool:
        return self.status in (BatchStatus.DRAFT, BatchStatus.COMPLETED, BatchStatus.CANCELLED)


class CreatorStatus(StrEnum):
    PENDING = "pending"
    CRAWLING = "crawling"
    ANALYZING = "analyzing"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"
    FAILED = "failed"
    """私密账号或找不到此达人。"""
    RECRAWL = "recrawl"
    """需要登录、爬取失败或视频全部失败，需要重新抓取。"""
    INVALID = "invalid"
    """名单中的达人名称无法识别为账号。"""


class CreatorProgress(StrEnum):
    """批次工作台上每位达人当前所处的阶段。"""

    PENDING = CreatorStatus.PENDING
    CRAWLING = CreatorStatus.CRAWLING
    DOWNLOADING = "downloading"
    AUDIO_ANALYSIS = "audio_analysis"
    VIDEO_ANALYSIS = "video_analysis"
    SUMMARIZING = CreatorStatus.SUMMARIZING
    COMPLETED = CreatorStatus.COMPLETED
    FAILED = CreatorStatus.FAILED
    RECRAWL = CreatorStatus.RECRAWL
    INVALID = CreatorStatus.INVALID


CREATOR_FINAL = frozenset(
    {CreatorStatus.COMPLETED, CreatorStatus.FAILED, CreatorStatus.RECRAWL, CreatorStatus.INVALID}
)


@dataclass
class Creator:
    batch_id: UUID
    position: int
    """名单中的顺序，从 1 开始；导出时按它排列。"""
    row: int | None
    """原表格的行号；粘贴链接时为空。"""
    name: str
    """名单中的达人名称原文。"""
    handle: str | None
    oecuid: str | None = None
    id: UUID = field(default_factory=uuid4)
    status: CreatorStatus = CreatorStatus.PENDING
    note: str | None = None
    """结论或原因，例如私密账号、爬取失败的原因。"""
    labels: list[str] = field(default_factory=list)
    """由作品清单得出的标签：图文作者、无作品。"""
    tags: list[str] | None = None
    """AI 汇总标签。"""
    profile: dict | None = None
    """爬取程序最近一次回传的平台资料：账号、主页链接、昵称、平台用户 ID。"""
    updated_at: datetime = field(default_factory=utcnow)
    """最近活动时间，即导出的“采集/识别的最新时间”。"""

    def work_inactivity_tag(
        self, works: Sequence[Work], crawl_finished_at: datetime | None = None
    ) -> str | None:
        """用最近采集时间和最新作品的发布时间计算完整的北京自然月数。"""
        latest_at = max(
            (work.published_at for work in works if work.published_at is not None), default=None
        )
        if latest_at is None:
            return None

        latest_at = latest_at.astimezone(BEIJING)
        ref_at = (crawl_finished_at or self.updated_at).astimezone(BEIJING)
        months = (ref_at.year - latest_at.year) * 12 + ref_at.month - latest_at.month
        if months <= 0:
            return None

        # 保留发布时间的时分秒，目标月没有对应日期时取该月最后一天。
        last_day = monthrange(ref_at.year, ref_at.month)[1]
        anniversary = latest_at.replace(
            year=ref_at.year, month=ref_at.month, day=min(latest_at.day, last_day)
        )
        if ref_at < anniversary:
            months -= 1

        for minimum, tag in (
            (12, "一年以上未更新"),
            (6, "半年内未更新"),
            (3, "3个月内未更新"),
            (1, "1个月内未更新"),
        ):
            if months >= minimum:
                return tag
        return None

    def __post_init__(self) -> None:
        if self.handle is None:
            self.status = CreatorStatus.INVALID
            self.note = "名单中的达人名称无法识别为 TikTok 账号"

    def crawl(self, now: datetime) -> None:
        """开始（重新）爬取：清掉上一轮的结论。"""
        self.settle(CreatorStatus.CRAWLING, now)
        self.labels, self.tags = [], None

    def crawled(self, works: list[Work], now: datetime) -> None:
        """作品清单锁定：有视频就开始分析，没有视频直接得出结论。"""
        if not works:
            self.labels = ["无作品"]
        elif any(work.kind == WorkKind.PHOTO for work in works):
            self.labels = ["图文作者"]
        has_video = any(work.kind == WorkKind.VIDEO for work in works)
        self.settle(CreatorStatus.ANALYZING if has_video else CreatorStatus.COMPLETED, now)

    def complete(self, tags: list[str], now: datetime) -> None:
        self.tags = tags
        self.settle(CreatorStatus.COMPLETED, now)

    def settle(self, status: CreatorStatus, now: datetime, note: str | None = None) -> None:
        self.status, self.note, self.updated_at = status, note, now

    @property
    def recrawlable(self) -> bool:
        return self.status in (CreatorStatus.COMPLETED, CreatorStatus.FAILED, CreatorStatus.RECRAWL)


class WorkKind(StrEnum):
    VIDEO = "video"
    PHOTO = "photo"


class WorkStatus(StrEnum):
    SKIPPED = "skipped"
    """图文作品，不下载。"""
    DOWNLOADING = "downloading"
    ANALYZING = "analyzing"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


WORK_FINAL = frozenset(
    {WorkStatus.SKIPPED, WorkStatus.COMPLETED, WorkStatus.FAILED, WorkStatus.ABANDONED}
)


@dataclass
class Work:
    """爬取锁定的一条作品。视频的状态由下载任务和分析任务推导。"""

    batch_id: UUID
    creator_id: UUID
    position: int
    """1～5，按发布时间从新到旧。"""
    platform_id: str
    url: str
    kind: WorkKind
    published_at: datetime | None = None
    description: str = ""
    topics: list[str] = field(default_factory=list)
    id: UUID = field(default_factory=uuid4)
    status: WorkStatus = WorkStatus.DOWNLOADING
    error: str | None = None
    tags: list[str] | None = None
    """识别标签。"""
    material_id: UUID | None = None
    updated_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if self.kind == WorkKind.PHOTO:
            self.status = WorkStatus.SKIPPED

    def settle(self, status: WorkStatus, now: datetime, error: str | None = None) -> None:
        self.status, self.error, self.updated_at = status, error, now

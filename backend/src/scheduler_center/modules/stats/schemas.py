from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from scheduler_center.modules.batch.models import BatchStatus, CreatorProgress, WorkStatus
from scheduler_center.modules.crawl.models import CrawlConclusion


class Bucket(StrEnum):
    HOUR = "hour"
    DAY = "day"


class Counts(BaseModel):
    """一段时间里各环节做完了多少。重新爬取、重新下载、补跑都会再算一次。"""

    profile_ok: int = 0
    """读到主页：得出账号结论或拿到达人资料。"""
    profile_failed: int = 0
    """主页没读成就放弃了。"""
    listing_ok: int = 0
    """作品列表读完，锁定了作品。"""
    listing_failed: int = 0
    """读到主页，但作品列表没读成。"""
    unreachable: int = 0
    """私密账号、需要登录、找不到此达人。"""
    videos_found: int = 0
    """锁定的视频作品（不含图文）。"""
    downloaded: int = 0
    """上传到调度中心的视频。"""
    downloaded_bytes: int = 0
    download_failed: int = 0
    analyzed: int = 0
    """识别完成的视频，含直接复用已有结果的。"""
    reused: int = 0
    """直接复用了相同视频已有结果的。"""
    analysis_failed: int = 0
    """分析失败或结果不明（待核查）的视频。"""
    summarized: int = 0
    summary_failed: int = 0


class TimelineRow(Counts):
    at: datetime
    """时段开始：北京时间的整点或零点。"""


class StageRow(BaseModel):
    """分析服务的一个阶段；听音按音频段计数。"""

    stage: str
    succeeded: int
    failed: int
    """失败或结果不明。"""
    retried: int
    """出错后等待重试的次数。"""
    skipped: int


type DurationStage = Literal["crawl", "download", "analysis", "summary"]


class DurationRow(BaseModel):
    """这段时间里做完的任务用了多久（秒）。"""

    stage: str
    """crawl 采集、download 下载、analysis 视频分析、summary 汇总，其余是分析服务的阶段。"""
    count: int
    average: float
    median: float
    p90: float


class FailureRow(BaseModel):
    stage: DurationStage
    reason: str
    """多次尝试的只取最后一次的原因。"""
    count: int


class Stats(BaseModel):
    since: datetime
    until: datetime
    bucket: Bucket
    batch_id: UUID | None
    totals: Counts
    conclusions: dict[CrawlConclusion, int]
    """爬取程序回传的结论次数。"""
    timeline: list[TimelineRow]
    """每个时段一行，没有数据的时段也有。"""
    stages: list[StageRow]
    durations: list[DurationRow]
    failures: list[FailureRow]
    """各环节最常见的失败原因，每个环节最多 8 条。"""
    creators: dict[CreatorProgress, int]
    """统计范围内的批次（或指定批次）当前各阶段的达人数。"""
    videos: dict[WorkStatus, int]
    """同上，当前各状态的视频数。"""


class CrawlIpBatch(BaseModel):
    id: UUID
    name: str
    status: BatchStatus
    has_leases: bool


class CrawlIpRisk(BaseModel):
    """一次租约最多归入一类风控。"""

    empty_body: int
    challenge: int
    throttled: int
    blocked: int
    other: int


class CrawlIpMetrics(BaseModel):
    crawl_leases: int
    collected_creators: int
    """按账号去重的成功采集人数，包含列表完整但没有符合条件作品的达人。"""
    private: int
    not_found: int
    login_required: int
    risk_count: int
    risk_rate_pct: float
    risk_breakdown: CrawlIpRisk
    active_hours: float
    """本行首末租约发放时间之差。"""
    creators_per_hour: float | None
    """用全批次可见租约的首末跨度折算，不足 60 秒时为空。"""
    leases_per_hour: float | None


class CrawlIpSummary(CrawlIpMetrics):
    active_ips: int
    first_lease_at: datetime | None
    last_lease_at: datetime | None


class CrawlIpRow(CrawlIpMetrics):
    line: str
    proxy_name: str
    creator_share_pct: float
    risk_share_pct: float
    backoff_state: Literal["normal", "cooling", "probing"]
    backoff_step: int
    backoff_until: datetime | None


class CrawlIpStats(BaseModel):
    batch: CrawlIpBatch | None
    data_status: Literal["available", "no_batch", "no_leases", "history_unavailable"]
    summary: CrawlIpSummary
    lines: list[CrawlIpRow]

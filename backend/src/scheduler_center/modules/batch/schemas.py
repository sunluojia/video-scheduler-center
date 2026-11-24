from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from scheduler_center.core.web.schemas import View
from scheduler_center.modules.analysis.models import JobKind, JobStatus, StageStatus
from scheduler_center.modules.batch.models import (
    BatchStatus,
    CreatorProgress,
    CreatorStatus,
    WorkKind,
    WorkStatus,
)
from scheduler_center.modules.crawl.messages import CrawledProfile
from scheduler_center.modules.crawl.models import (
    CrawlConclusion,
    CrawlerAttemptStatus,
    CrawlerTaskKind,
    CrawlerTaskStatus,
)
from scheduler_center.modules.flow.history import Source


class BatchView(View):
    id: UUID
    name: str
    source: str
    status: BatchStatus
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class BatchRow(BatchView):
    creators: dict[CreatorStatus, int]
    """每种状态的达人数。"""
    creator_progress: dict[CreatorProgress, int]
    """工作台上各阶段的达人数。"""
    videos: dict[WorkStatus, int]
    """每种状态的视频数。"""


class BatchAction(BaseModel):
    batch: BatchView
    warnings: list[str]
    """没能送达执行程序的通知。"""


class Affected(BaseModel):
    count: int


class CreatorView(View):
    id: UUID
    batch_id: UUID
    position: int
    row: int | None
    name: str
    handle: str | None
    oecuid: str | None
    status: CreatorStatus
    note: str | None
    labels: list[str]
    tags: list[str] | None
    profile: CrawledProfile | None
    """爬取程序核实过的平台资料。"""
    updated_at: datetime


class WorkView(View):
    id: UUID
    batch_id: UUID
    creator_id: UUID
    position: int
    platform_id: str
    url: str
    kind: WorkKind
    published_at: datetime | None
    description: str
    topics: list[str]
    status: WorkStatus
    error: str | None
    tags: list[str] | None
    updated_at: datetime


class CreatorRow(CreatorView):
    progress: CreatorProgress
    works: list[WorkView]
    """锁定的作品，按位置排列。"""


class WorkRow(BaseModel):
    work: WorkView
    creator: CreatorView
    batch: BatchView


class CrawlerTaskView(View):
    id: UUID
    kind: CrawlerTaskKind
    run: int
    status: CrawlerTaskStatus
    conclusion: CrawlConclusion | None
    error: str | None
    published_at: datetime
    accepted_at: datetime | None
    finished_at: datetime | None
    current_attempt_id: UUID | None
    last_started_executor_id: str | None
    result_executor_id: str | None


class JobView(View):
    id: UUID
    kind: JobKind
    run: int
    status: JobStatus
    result: dict | None
    error: str | None
    reused_from: str | None
    artifacts_until: datetime | None
    submitted_at: datetime
    accepted_at: datetime | None
    finished_at: datetime | None
    last_executor_id: str | None
    result_executor_id: str | None


class StageView(View):
    stage: str
    segment: int | None
    status: StageStatus
    attempt: int
    model: str | None
    prompt_version: int | None
    result: dict | None
    error: str | None
    retry_at: datetime | None
    started_at: datetime | None
    occurred_at: datetime
    last_executor_id: str | None
    result_executor_id: str | None


class CrawlerAttemptView(View):
    attempt_id: UUID
    task_id: UUID
    run: int
    attempt_no: int
    executor_id: str
    executor_session_id: UUID
    status: CrawlerAttemptStatus
    claimed_at: datetime
    lease_expires_at: datetime
    ended_at: datetime | None
    error: str | None
    route_kind: str | None


class AnalysisAttemptView(View):
    attempt_id: UUID
    job_id: UUID
    run: int
    retry_generation: int
    stage: str
    segment: int | None
    attempt_no: int
    executor_id: str | None
    executor_session_id: UUID | None
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    error: str | None
    model: str | None


class EventView(View):
    source: Source
    task_id: UUID
    payload: dict[str, Any]
    received_at: datetime
    executor_id: str | None
    executor_session_id: UUID | None
    attempt_id: UUID | None


class CreatorDetail(BaseModel):
    creator: CreatorView
    batch: BatchView
    crawl: CrawlerTaskView | None
    works: list[WorkView]
    summary: JobView | None
    crawl_attempts: list[CrawlerAttemptView]
    summary_attempts: list[AnalysisAttemptView]
    history: list[EventView]
    """爬取任务的事件。"""


class WorkDetail(BaseModel):
    work: WorkView
    creator: CreatorView
    download: CrawlerTaskView | None
    analysis: JobView | None
    download_attempts: list[CrawlerAttemptView]
    analysis_attempts: list[AnalysisAttemptView]
    stages: list[StageView]
    """分析任务当前轮次的各阶段。"""
    history: list[EventView]
    """下载任务和分析任务的事件。"""

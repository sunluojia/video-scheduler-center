"""与分析服务约定的任务和事件，见分析服务设计文档第 3 节。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter

from scheduler_center.core.messaging.outbox import Route
from scheduler_center.modules.analysis.models import JobKind, JobStatus, StageStatus

ANALYZE_VIDEO = Route("analysis.tasks", "analyze_video")
SUMMARIZE_CREATOR = Route("analysis.tasks", "summarize_creator")
ANALYSIS_EVENTS = Route("analysis.events", "analysis_event")


class MaterialRef(BaseModel):
    id: UUID
    size: int
    sha256: str


class JobMessage(BaseModel):
    job_id: UUID
    run: int
    batch_id: UUID
    creator_id: UUID
    ai_batch_mode: bool | None = None


class AnalyzeVideo(JobMessage):
    material: MaterialRef
    description: str
    topics: list[str]
    allow_reuse: bool
    """第一轮允许复用相同视频的已有结果；重新分析时不复用。"""


class SourceVideo(BaseModel):
    job_id: UUID
    run: int
    recognition: dict


class SummarizeCreator(JobMessage):
    videos: list[SourceVideo]


class AnalysisEventBase(BaseModel):
    event_id: UUID
    job_id: UUID
    run: int
    occurred_at: datetime
    executor_id: str | None = Field(default=None, max_length=128)
    executor_session_id: UUID | None = None
    attempt_id: UUID | None = None
    retry_generation: int | None = Field(default=None, ge=0)


class JobEvent(AnalysisEventBase):
    type: Literal["job"]
    kind: JobKind
    status: JobStatus
    result: dict | None = None
    error: str | None = None
    reused_from: str | None = None
    artifacts_until: datetime | None = None


class StageEvent(AnalysisEventBase):
    type: Literal["stage"]
    stage: str = Field(max_length=64)
    segment: int | None = None
    attempt: int = 0
    status: StageStatus
    model: str | None = None
    prompt_version: int | None = None
    result: dict | None = None
    error: str | None = None
    retry_at: datetime | None = None


type AnalysisEvent = Annotated[JobEvent | StageEvent, Field(discriminator="type")]
analysis_event = TypeAdapter(AnalysisEvent)


class JobState(BaseModel):
    """分析服务控制接口返回的任务状态。"""

    job_id: UUID
    run: int
    status: JobStatus
    error: str | None = None
    occurred_at: datetime
    """分析服务取得这个状态的时间，与任务事件按同一时钟比较先后。"""

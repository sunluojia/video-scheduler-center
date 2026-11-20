from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from scheduler_center.core.common.clock import utcnow


class JobKind(StrEnum):
    VIDEO = "video"
    SUMMARY = "summary"


class JobStatus(StrEnum):
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    """模型请求已发出、结果不明，需要人选择补跑或放弃。"""
    ABANDONED = "abandoned"
    CANCELLED = "cancelled"


JOB_ENDED = frozenset({JobStatus.ABANDONED, JobStatus.CANCELLED})
"""调度中心这边已经结束，不再采纳分析服务的事件。"""


@dataclass
class AnalysisJob:
    """发给分析服务的任务。补跑沿用轮次；重新分析、重新汇总时轮次加一。"""

    kind: JobKind
    batch_id: UUID
    creator_id: UUID
    message: dict
    """最近一次发布的消息，对账时原样重发。"""
    work_id: UUID | None = None
    sources: list[dict] | None = None
    """汇总任务用到的视频任务 [{job_id, run}]，视频结果变化后需要重新汇总。"""
    id: UUID = field(default_factory=uuid4)
    run: int = 1
    status: JobStatus = JobStatus.SUBMITTED
    result: dict | None = None
    error: str | None = None
    reused_from: str | None = None
    artifacts_until: datetime | None = None
    """抽帧图片和音频段可以从分析服务获取到此时。"""
    submitted_at: datetime = field(default_factory=utcnow)
    accepted_at: datetime | None = None
    finished_at: datetime | None = None
    event_at: datetime | None = None
    """最近一次采纳的任务事件发生的时间（分析服务的时钟）；更早发生的是乱序到达的，不再采纳。"""

    def resubmit(self, now: datetime, sources: list[dict] | None = None) -> None:
        """新一轮：消息由调用方按新轮次生成。"""
        self.run += 1
        self.sources = sources
        self.status, self.submitted_at = JobStatus.SUBMITTED, now
        self.result = self.error = self.reused_from = self.artifacts_until = None
        self.accepted_at = self.finished_at = self.event_at = None

    def follow(
        self,
        status: JobStatus,
        occurred_at: datetime,
        *,
        result: dict | None = None,
        error: str | None = None,
        reused_from: str | None = None,
        artifacts_until: datetime | None = None,
    ) -> bool:
        """采纳分析服务回报的任务状态：只前进不后退；返回是否采纳。

        只和分析服务自己的事件时间比较，两边的时钟不必一致。
        """
        if self.status in JOB_ENDED or (self.event_at and occurred_at < self.event_at):
            return False
        if status == JobStatus.ACCEPTED:
            if self.status != JobStatus.SUBMITTED:
                return False
            self.status, self.accepted_at = status, occurred_at
            return True
        self.status, self.event_at = status, occurred_at
        self.result, self.error, self.reused_from = result, error, reused_from
        self.artifacts_until = artifacts_until
        self.finished_at = None if status == JobStatus.PROCESSING else occurred_at
        return True

    def processing(self) -> None:
        """某个阶段开始执行。"""
        if self.status in (JobStatus.SUBMITTED, JobStatus.ACCEPTED):
            self.status = JobStatus.PROCESSING

    def end(self, status: JobStatus, now: datetime) -> None:
        """放弃或取消。"""
        self.status, self.finished_at = status, now


class StageStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    RETRYING = "retrying"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    SKIPPED = "skipped"


@dataclass
class AnalysisStage:
    """一个阶段（听音按段）在某一轮的最新状态；完整经过保存在事件历史中。"""

    job_id: UUID
    run: int
    stage: str
    segment: int | None
    status: StageStatus
    occurred_at: datetime
    id: UUID = field(default_factory=uuid4)
    attempt: int = 0
    model: str | None = None
    prompt_version: int | None = None
    result: dict | None = None
    error: str | None = None
    retry_at: datetime | None = None
    started_at: datetime | None = None

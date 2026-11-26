from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from scheduler_center.modules.crawl.messages import CrawlCreator, DownloadVideo
from scheduler_center.modules.crawl.models import CrawlerRunKind


class ExecutorSession(BaseModel):
    executor_id: str = Field(min_length=1, max_length=128)
    executor_session_id: UUID


class ClaimKinds(BaseModel):
    crawl_creator: int = Field(default=0, ge=0, le=500)
    download_video: int = Field(default=0, ge=0, le=500)


class ClaimRequest(ExecutorSession):
    kinds: ClaimKinds


class ClaimedTask(BaseModel):
    kind: CrawlerRunKind
    task_id: UUID
    run: int
    attempt_id: UUID
    attempt_no: int
    lease_expires_at: datetime
    attempts: int
    avoid: list[str]
    history: list[str]
    body: CrawlCreator | DownloadVideo


class CrawlWait(BaseModel):
    retry_after: int
    reason: str
    reason_code: str | None = None


class ClaimReply(BaseModel):
    tasks: list[ClaimedTask]
    crawl_wait: CrawlWait | None = Field(default=None, exclude_if=lambda wait: wait is None)


class DeferRequest(ExecutorSession):
    retry_after: int = Field(ge=0, le=2_147_483_647)
    reason: str
    consume_attempt: bool = False
    avoid_line: str | None = None

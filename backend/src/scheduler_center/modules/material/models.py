from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from scheduler_center.core.common.clock import utcnow


@dataclass
class Material:
    """爬取程序上传的一条原视频，供分析服务下载。"""

    work_id: UUID
    size: int
    sha256: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utcnow)
    discarded_at: datetime | None = None
    source_attempt_id: UUID | None = None
    source_executor_id: str | None = None
    """不再需要的时间；文件由 Worker 随后删除。"""

    def discard(self, now: datetime) -> None:
        if self.discarded_at is None:
            self.discarded_at = now

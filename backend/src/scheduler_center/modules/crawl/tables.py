from sqlalchemy import (
    Column,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from scheduler_center.core.database.mapping import (
    enum_column,
    mapper_registry,
    metadata,
    reference,
    timestamp,
)
from scheduler_center.modules.crawl.models import (
    CrawlConclusion,
    CrawlerAttemptStatus,
    CrawlerRunKind,
    CrawlerRunStatus,
    CrawlerTask,
    CrawlerTaskAttempt,
    CrawlerTaskKind,
    CrawlerTaskRun,
    CrawlerTaskStatus,
)

crawler_tasks = Table(
    "crawler_tasks",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("kind", enum_column(CrawlerTaskKind), nullable=False),
    Column("batch_id", reference("batches.id"), nullable=False, index=True),
    Column("creator_id", reference("creators.id"), nullable=False, index=True),
    Column("work_id", reference("works.id"), unique=True),
    Column("message", JSONB, nullable=False),
    Column("run", Integer, nullable=False),
    Column("status", enum_column(CrawlerTaskStatus), nullable=False),
    Column("conclusion", enum_column(CrawlConclusion)),
    Column("error", Text),
    Column("published_at", timestamp(), nullable=False),
    Column("accepted_at", timestamp()),
    Column("finished_at", timestamp()),
    Column("current_attempt_id", Uuid),
    Column("last_started_executor_id", String(128)),
    Column("result_executor_id", String(128)),
    Index(
        "ix_crawler_tasks_unaccepted",
        "published_at",
        postgresql_where=text("status = 'published'"),
    ),
)

crawler_task_runs = Table(
    "crawler_task_runs",
    metadata,
    # 重爬删除旧作品与下载任务，批次内仍可查询这些轮次和认领记录。
    Column("task_id", Uuid, primary_key=True),
    Column("run", Integer, primary_key=True),
    Column("batch_id", reference("batches.id"), nullable=False, index=True),
    Column("creator_id", Uuid, nullable=False, index=True),
    Column("work_id", String(64)),
    Column("kind", enum_column(CrawlerRunKind), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("status", enum_column(CrawlerRunStatus), nullable=False),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("avoid", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("history", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("next_eligible_at", timestamp(), nullable=False, server_default=func.now()),
    Column("current_attempt_id", Uuid),
    Column("created_at", timestamp(), nullable=False, server_default=func.now()),
    Column("updated_at", timestamp(), nullable=False, server_default=func.now()),
    Index(
        "ix_crawler_task_runs_eligible",
        "kind",
        "next_eligible_at",
        "created_at",
        "task_id",
        postgresql_where=text("status = 'queued'"),
    ),
)

crawler_task_attempts = Table(
    "crawler_task_attempts",
    metadata,
    Column("attempt_id", Uuid, primary_key=True),
    Column("task_id", Uuid, nullable=False),
    Column("run", Integer, nullable=False),
    Column("attempt_no", Integer, nullable=False),
    Column("executor_id", String(128), nullable=False),
    Column("executor_session_id", Uuid, nullable=False),
    Column("status", enum_column(CrawlerAttemptStatus), nullable=False),
    Column("claimed_at", timestamp(), nullable=False),
    Column("lease_expires_at", timestamp(), nullable=False),
    Column("ended_at", timestamp()),
    Column("error", Text),
    Column("route_kind", String(32)),
    ForeignKeyConstraint(
        ["task_id", "run"],
        ["crawler_task_runs.task_id", "crawler_task_runs.run"],
        ondelete="CASCADE",
        deferrable=True,
        initially="DEFERRED",
    ),
    UniqueConstraint("task_id", "run", "attempt_no"),
    Index(
        "uq_crawler_task_attempts_active",
        "task_id",
        "run",
        unique=True,
        postgresql_where=text("status IN ('leased', 'running')"),
    ),
    Index("ix_crawler_task_attempts_expiry", "status", "lease_expires_at"),
    Index("ix_crawler_task_attempts_executor_claimed", "executor_id", text("claimed_at DESC")),
    Index("ix_crawler_task_attempts_task_run", "task_id", "run"),
)

mapper_registry.map_imperatively(CrawlerTask, crawler_tasks)
mapper_registry.map_imperatively(CrawlerTaskRun, crawler_task_runs)
mapper_registry.map_imperatively(CrawlerTaskAttempt, crawler_task_attempts)

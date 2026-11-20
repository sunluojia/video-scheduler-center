from sqlalchemy import Column, Index, Integer, Table, Text, Uuid, text
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
    CrawlerTask,
    CrawlerTaskKind,
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
    Index(
        "ix_crawler_tasks_unaccepted",
        "published_at",
        postgresql_where=text("status = 'published'"),
    ),
)

mapper_registry.map_imperatively(CrawlerTask, crawler_tasks)

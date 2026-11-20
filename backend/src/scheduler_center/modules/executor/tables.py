from sqlalchemy import Column, SmallInteger, String, Table, Text
from sqlalchemy.dialects.postgresql import JSONB

from scheduler_center.core.database.mapping import enum_column, mapper_registry, metadata, timestamp
from scheduler_center.modules.executor.models import (
    CRAWL_SLOTS,
    DOWNLOAD_SLOTS,
    WARM_SLOTS,
    Executor,
    ExecutorKind,
)

executors = Table(
    "executors",
    metadata,
    Column("kind", enum_column(ExecutorKind), primary_key=True),
    Column("instance_id", String(128), primary_key=True),
    Column("url", Text, nullable=False),
    Column("version", String(64), nullable=False),
    Column("registered_at", timestamp(), nullable=False),
    Column("last_seen_at", timestamp()),
    Column("withdrawn_at", timestamp()),
    Column("identities", JSONB),
    Column("crawl_slots", SmallInteger, nullable=False, server_default=str(CRAWL_SLOTS)),
    Column("download_slots", SmallInteger, nullable=False, server_default=str(DOWNLOAD_SLOTS)),
    Column("warm_slots", SmallInteger, nullable=False, server_default=str(WARM_SLOTS)),
)

mapper_registry.map_imperatively(Executor, executors)

from sqlalchemy import (
    Column,
    Integer,
    LargeBinary,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB

from scheduler_center.core.database.mapping import (
    enum_column,
    mapper_registry,
    metadata,
    reference,
    timestamp,
)
from scheduler_center.modules.batch.models import (
    Batch,
    BatchStatus,
    Creator,
    CreatorStatus,
    Work,
    WorkKind,
    WorkStatus,
)

batches = Table(
    "batches",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("name", String(128), nullable=False),
    Column("source", String(255), nullable=False),
    Column("status", enum_column(BatchStatus), nullable=False, index=True),
    Column("created_at", timestamp(), nullable=False, index=True),
    Column("started_at", timestamp()),
    Column("finished_at", timestamp()),
)

creators = Table(
    "creators",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("batch_id", reference("batches.id"), nullable=False),
    Column("position", Integer, nullable=False),
    Column("row", Integer),
    Column("name", Text, nullable=False),
    Column("handle", String(64)),
    Column("oecuid", String(64)),
    Column("status", enum_column(CreatorStatus), nullable=False, index=True),
    Column("note", Text),
    Column("labels", JSONB, nullable=False),
    Column("tags", JSONB),
    Column("profile", JSONB),
    Column("updated_at", timestamp(), nullable=False),
    UniqueConstraint("batch_id", "position"),
)

qr_codes = Table(
    "qr_codes",
    metadata,
    Column("creator_id", reference("creators.id"), primary_key=True),
    Column("content", LargeBinary, nullable=False),
    Column("media_type", String(32), nullable=False),
)
"""名单中的二维码图片，导出时原样放回；单独存放，查询达人时不加载。"""

works = Table(
    "works",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("batch_id", reference("batches.id"), nullable=False, index=True),
    Column("creator_id", reference("creators.id"), nullable=False, index=True),
    Column("position", Integer, nullable=False),
    Column("platform_id", String(64), nullable=False),
    Column("url", Text, nullable=False),
    Column("kind", enum_column(WorkKind), nullable=False),
    Column("published_at", timestamp()),
    Column("description", Text, nullable=False),
    Column("topics", JSONB, nullable=False),
    Column("status", enum_column(WorkStatus), nullable=False, index=True),
    Column("error", Text),
    Column("tags", JSONB),
    Column("material_id", Uuid),
    Column("updated_at", timestamp(), nullable=False),
    UniqueConstraint("creator_id", "position"),
)

mapper_registry.map_imperatively(Batch, batches)
mapper_registry.map_imperatively(Creator, creators)
mapper_registry.map_imperatively(Work, works)

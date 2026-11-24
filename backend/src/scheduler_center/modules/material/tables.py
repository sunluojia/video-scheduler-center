from sqlalchemy import BigInteger, Column, String, Table, Uuid

from scheduler_center.core.database.mapping import mapper_registry, metadata, reference, timestamp
from scheduler_center.modules.material.models import Material

materials = Table(
    "materials",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("work_id", reference("works.id"), nullable=False, index=True),
    Column("size", BigInteger, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("created_at", timestamp(), nullable=False),
    Column("discarded_at", timestamp()),
    Column("source_attempt_id", Uuid),
    Column("source_executor_id", String(128)),
)

mapper_registry.map_imperatively(Material, materials)

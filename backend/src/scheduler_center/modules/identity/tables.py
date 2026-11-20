from sqlalchemy import CheckConstraint, Column, Integer, SmallInteger, Table

from scheduler_center.core.database.mapping import metadata, timestamp

identity_pool = Table(
    "identity_pool",
    metadata,
    Column("id", SmallInteger, primary_key=True),
    Column("target", Integer, nullable=False),
    Column("updated_at", timestamp(), nullable=False),
    CheckConstraint("id = 1", name="identity_pool_single_row"),
)
"""账号池的设置，只有一行。"""

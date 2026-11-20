"""表结构公共部分。业务模型是普通数据类，通过命令式映射落库，模型本身不依赖 ORM。"""

from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, MetaData
from sqlalchemy.orm import registry

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)
mapper_registry = registry(metadata=metadata)


def enum_column(kind: type[StrEnum]) -> Enum:
    return Enum(
        kind,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
    )


def timestamp() -> DateTime:
    return DateTime(timezone=True)


def reference(column: str) -> ForeignKey:
    """模型之间不建立 ORM 关系，同一事务内的写入顺序不固定，因此外键在提交时检查。

    上级记录删除时一并删除：删除批次即清掉它的全部数据。
    """
    return ForeignKey(column, ondelete="CASCADE", deferrable=True, initially="DEFERRED")

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from scheduler_center.core.database.mapping import (
    enum_column,
    mapper_registry,
    metadata,
    timestamp,
)
from scheduler_center.modules.proxy.models import (
    Lease,
    Line,
    ListingResult,
    Outcome,
    PickMode,
    Proxy,
    ProxyFolder,
    ProxyProtocol,
    ProxyRule,
    ProxyStatus,
    RuleSource,
    Use,
)


def folder_key() -> ForeignKey:
    """文件夹删除前，里面的代理和子文件夹先移到上一级；写入顺序不固定，外键在提交时检查。"""
    return ForeignKey("proxy_folders.id", deferrable=True, initially="DEFERRED")


proxy_folders = Table(
    "proxy_folders",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("name", String(64), nullable=False),
    Column("parent_id", Uuid, folder_key(), index=True),
    Column("position", Integer, nullable=False),
)

proxies = Table(
    "proxies",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("name", String(128), nullable=False),
    Column("protocol", enum_column(ProxyProtocol), nullable=False),
    Column("host", String(255), nullable=False),
    Column("port", Integer, nullable=False),
    Column("username", String(255), nullable=False),
    Column("password", Text, nullable=False),
    Column("private_key", Text),
    Column("refresh_url", Text),
    Column("folder_id", Uuid, folder_key(), index=True),
    Column("uses", JSONB, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("status", enum_column(ProxyStatus), nullable=False),
    Column("error", Text),
    Column("exit_ip", String(64)),
    Column("line", String(160), index=True),
    Column("latency_ms", Integer),
    Column("host_key", Text),
    Column("tested_at", timestamp()),
    Column("tested_by", String(128)),
    Column("test_at", timestamp(), index=True),
    Column("testing_until", timestamp()),
    Column("backoff", Integer, nullable=False),
    Column("speed_at", timestamp()),
    Column("speed_mbps", Float),
    Column("parallel_mbps", Float),
    Column("speed_tested_at", timestamp()),
    Column("speed_error", Text),
    Column("created_at", timestamp(), nullable=False),
    Column("config_revision", Uuid),
    UniqueConstraint("host", "port", "username"),
)

proxy_rules = Table(
    "proxy_rules",
    metadata,
    Column("use", enum_column(Use), primary_key=True),
    Column("device", String(128), primary_key=True),
    Column("source", enum_column(RuleSource), nullable=False),
    Column("folder_id", Uuid, folder_key()),
    Column("local_port", Integer),
    Column("mode", enum_column(PickMode), nullable=False),
    Column("line_limit", Integer, nullable=False),
    Column("cursor", String(160)),
    Column("updated_at", timestamp(), nullable=False),
    Column("targets", JSONB(none_as_null=True)),
)

proxy_lines = Table(
    "proxy_lines",
    metadata,
    Column("use", enum_column(Use), primary_key=True),
    Column("key", String(160), primary_key=True),
    Column("device", String(128)),
    Column("score", Float, nullable=False),
    Column("cooldown_until", timestamp()),
    Column("backoff", Integer, nullable=False),
    Column("probe_at", timestamp()),
    Column("throttled_until", timestamp()),
    Column("reason", Text),
    Column("exit_ip", String(64)),
    Column("tested_at", timestamp()),
    Column("updated_at", timestamp(), nullable=False),
)

proxy_leases = Table(
    "proxy_leases",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("device", String(128), nullable=False),
    Column("use", enum_column(Use), nullable=False),
    Column("line", String(160), nullable=False),
    Column("proxy_id", Uuid),
    Column("task_id", Uuid),
    Column("probe", Boolean, nullable=False),
    Column("acquired_at", timestamp(), nullable=False, index=True),
    Column("expires_at", timestamp(), nullable=False),
    Column("released_at", timestamp()),
    Column("outcome", enum_column(Outcome)),
    Column("detail", Text),
    Column("proxy_revision", Uuid),
    Column("listing_result", enum_column(ListingResult)),
    Index("ix_proxy_leases_open", "use", "line", postgresql_where=text("released_at IS NULL")),
    Index(
        "ix_proxy_leases_listing_result",
        "use",
        "line",
        "released_at",
        postgresql_where=text("listing_result IN ('complete', 'empty_response')"),
    ),
)
"""租约同时是线路表现的记录，保留 24 小时；代理删除后记录照常保留。"""

mapper_registry.map_imperatively(Proxy, proxies)
mapper_registry.map_imperatively(ProxyFolder, proxy_folders)
mapper_registry.map_imperatively(ProxyRule, proxy_rules)
mapper_registry.map_imperatively(Line, proxy_lines)
mapper_registry.map_imperatively(Lease, proxy_leases)

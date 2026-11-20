"""代理：代理池（文件夹、用途标签）、各用途的规则、线路和线路租约。

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proxy_folders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["proxy_folders.id"],
            name=op.f("fk_proxy_folders_parent_id_proxy_folders"),
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proxy_folders")),
    )
    op.create_index(
        op.f("ix_proxy_folders_parent_id"), "proxy_folders", ["parent_id"], unique=False
    )
    op.create_table(
        "proxy_leases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device", sa.String(length=128), nullable=False),
        sa.Column(
            "use",
            sa.Enum(
                "crawler.crawl_creator",
                "crawler.download_video",
                name="use",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("line", sa.String(length=160), nullable=False),
        sa.Column("proxy_id", sa.Uuid(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("probe", sa.Boolean(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "outcome",
            sa.Enum(
                "normal",
                "suspect",
                "proxy_down",
                "auth_failed",
                "throttled",
                name="outcome",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proxy_leases")),
    )
    op.create_index(
        op.f("ix_proxy_leases_acquired_at"), "proxy_leases", ["acquired_at"], unique=False
    )
    op.create_index(
        "ix_proxy_leases_open",
        "proxy_leases",
        ["use", "line"],
        unique=False,
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.create_table(
        "proxy_lines",
        sa.Column(
            "use",
            sa.Enum(
                "crawler.crawl_creator",
                "crawler.download_video",
                name="use",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("device", sa.String(length=128), nullable=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("backoff", sa.Integer(), nullable=False),
        sa.Column("probe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("exit_ip", sa.String(length=64), nullable=True),
        sa.Column("tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("use", "key", name=op.f("pk_proxy_lines")),
    )
    op.create_table(
        "proxies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "protocol",
            sa.Enum(
                "auto",
                "http",
                "https",
                "socks5",
                "ssh",
                name="proxyprotocol",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("password", sa.Text(), nullable=False),
        sa.Column("private_key", sa.Text(), nullable=True),
        sa.Column("refresh_url", sa.Text(), nullable=True),
        sa.Column("folder_id", sa.Uuid(), nullable=True),
        sa.Column("uses", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "untested",
                "available",
                "unreachable",
                "auth_failed",
                "refreshing",
                name="proxystatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("exit_ip", sa.String(length=64), nullable=True),
        sa.Column("line", sa.String(length=160), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("host_key", sa.Text(), nullable=True),
        sa.Column("tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tested_by", sa.String(length=128), nullable=True),
        sa.Column("test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("backoff", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["folder_id"],
            ["proxy_folders.id"],
            name=op.f("fk_proxies_folder_id_proxy_folders"),
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proxies")),
        sa.UniqueConstraint("host", "port", "username", name=op.f("uq_proxies_host")),
    )
    op.create_index(op.f("ix_proxies_folder_id"), "proxies", ["folder_id"], unique=False)
    op.create_index(op.f("ix_proxies_line"), "proxies", ["line"], unique=False)
    op.create_index(op.f("ix_proxies_test_at"), "proxies", ["test_at"], unique=False)
    op.create_table(
        "proxy_rules",
        sa.Column(
            "use",
            sa.Enum(
                "crawler.crawl_creator",
                "crawler.download_video",
                name="use",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("device", sa.String(length=128), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "direct", "pool", "local_port", name="rulesource", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("folder_id", sa.Uuid(), nullable=True),
        sa.Column("local_port", sa.Integer(), nullable=True),
        sa.Column(
            "mode",
            sa.Enum("priority", "rotate", name="pickmode", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("line_limit", sa.Integer(), nullable=False),
        sa.Column("cursor", sa.String(length=160), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["folder_id"],
            ["proxy_folders.id"],
            name=op.f("fk_proxy_rules_folder_id_proxy_folders"),
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("use", "device", name=op.f("pk_proxy_rules")),
    )


def downgrade() -> None:
    op.drop_table("proxy_rules")
    op.drop_index(op.f("ix_proxies_test_at"), table_name="proxies")
    op.drop_index(op.f("ix_proxies_line"), table_name="proxies")
    op.drop_index(op.f("ix_proxies_folder_id"), table_name="proxies")
    op.drop_table("proxies")
    op.drop_table("proxy_lines")
    op.drop_index(
        "ix_proxy_leases_open",
        table_name="proxy_leases",
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.drop_index(op.f("ix_proxy_leases_acquired_at"), table_name="proxy_leases")
    op.drop_table("proxy_leases")
    op.drop_index(op.f("ix_proxy_folders_parent_id"), table_name="proxy_folders")
    op.drop_table("proxy_folders")

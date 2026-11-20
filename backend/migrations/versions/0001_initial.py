"""初始表结构。

Revision ID: 0001
Revises:
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "running",
                "paused",
                "completed",
                "cancelled",
                name="batchstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_batches")),
    )
    op.create_index(op.f("ix_batches_created_at"), "batches", ["created_at"], unique=False)
    op.create_index(op.f("ix_batches_status"), "batches", ["status"], unique=False)
    op.create_table(
        "executors",
        sa.Column(
            "kind",
            sa.Enum("crawler", "analysis", name="executorkind", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("instance_id", sa.String(length=128), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("kind", "instance_id", name=op.f("pk_executors")),
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("queue", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox")),
    )
    op.create_index(
        "ix_outbox_pending",
        "outbox",
        ["id"],
        unique=False,
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.create_table(
        "creators",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("row", sa.Integer(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("handle", sa.String(length=64), nullable=True),
        sa.Column("oecuid", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "crawling",
                "analyzing",
                "summarizing",
                "completed",
                "failed",
                "recrawl",
                "invalid",
                name="creatorstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("labels", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["batches.id"],
            name=op.f("fk_creators_batch_id_batches"),
            ondelete="CASCADE",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_creators")),
        sa.UniqueConstraint("batch_id", "position", name=op.f("uq_creators_batch_id")),
    )
    op.create_index(op.f("ix_creators_status"), "creators", ["status"], unique=False)
    op.create_table(
        "events",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source",
            sa.Enum("crawler", "analysis", name="source", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("run", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["batches.id"],
            name=op.f("fk_events_batch_id_batches"),
            ondelete="CASCADE",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_events")),
        sa.UniqueConstraint("source", "event_id", name=op.f("uq_events_source")),
    )
    op.create_index(op.f("ix_events_batch_id"), "events", ["batch_id"], unique=False)
    op.create_index(op.f("ix_events_task_id"), "events", ["task_id"], unique=False)
    op.create_table(
        "qr_codes",
        sa.Column("creator_id", sa.Uuid(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("media_type", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["creator_id"],
            ["creators.id"],
            name=op.f("fk_qr_codes_creator_id_creators"),
            ondelete="CASCADE",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("creator_id", name=op.f("pk_qr_codes")),
    )
    op.create_table(
        "works",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("platform_id", sa.String(length=64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("video", "photo", name="workkind", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("topics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "skipped",
                "downloading",
                "analyzing",
                "needs_review",
                "completed",
                "failed",
                "abandoned",
                name="workstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("material_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["batches.id"],
            name=op.f("fk_works_batch_id_batches"),
            ondelete="CASCADE",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["creator_id"],
            ["creators.id"],
            name=op.f("fk_works_creator_id_creators"),
            ondelete="CASCADE",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_works")),
        sa.UniqueConstraint("creator_id", "position", name=op.f("uq_works_creator_id")),
    )
    op.create_index(op.f("ix_works_batch_id"), "works", ["batch_id"], unique=False)
    op.create_index(op.f("ix_works_creator_id"), "works", ["creator_id"], unique=False)
    op.create_index(op.f("ix_works_status"), "works", ["status"], unique=False)
    op.create_table(
        "analysis_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("video", "summary", name="jobkind", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=False),
        sa.Column("work_id", sa.Uuid(), nullable=True),
        sa.Column("message", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("run", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "submitted",
                "accepted",
                "processing",
                "completed",
                "failed",
                "needs_review",
                "abandoned",
                "cancelled",
                name="jobstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("reused_from", sa.String(length=128), nullable=True),
        sa.Column("artifacts_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["batches.id"],
            name=op.f("fk_analysis_jobs_batch_id_batches"),
            ondelete="CASCADE",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["creator_id"],
            ["creators.id"],
            name=op.f("fk_analysis_jobs_creator_id_creators"),
            ondelete="CASCADE",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["work_id"],
            ["works.id"],
            name=op.f("fk_analysis_jobs_work_id_works"),
            ondelete="CASCADE",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_jobs")),
        sa.UniqueConstraint("work_id", name=op.f("uq_analysis_jobs_work_id")),
    )
    op.create_index(op.f("ix_analysis_jobs_batch_id"), "analysis_jobs", ["batch_id"], unique=False)
    op.create_index(
        op.f("ix_analysis_jobs_creator_id"), "analysis_jobs", ["creator_id"], unique=False
    )
    op.create_index(
        "ix_analysis_jobs_unaccepted",
        "analysis_jobs",
        ["submitted_at"],
        unique=False,
        postgresql_where=sa.text("status = 'submitted'"),
    )
    op.create_table(
        "crawler_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("crawl", "download", name="crawlertaskkind", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=False),
        sa.Column("work_id", sa.Uuid(), nullable=True),
        sa.Column("message", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("run", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "published",
                "accepted",
                "succeeded",
                "failed",
                "cancelled",
                name="crawlertaskstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "conclusion",
            sa.Enum(
                "ok",
                "private",
                "not_found",
                "failed",
                name="crawlconclusion",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["batches.id"],
            name=op.f("fk_crawler_tasks_batch_id_batches"),
            ondelete="CASCADE",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["creator_id"],
            ["creators.id"],
            name=op.f("fk_crawler_tasks_creator_id_creators"),
            ondelete="CASCADE",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["work_id"],
            ["works.id"],
            name=op.f("fk_crawler_tasks_work_id_works"),
            ondelete="CASCADE",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawler_tasks")),
        sa.UniqueConstraint("work_id", name=op.f("uq_crawler_tasks_work_id")),
    )
    op.create_index(op.f("ix_crawler_tasks_batch_id"), "crawler_tasks", ["batch_id"], unique=False)
    op.create_index(
        op.f("ix_crawler_tasks_creator_id"), "crawler_tasks", ["creator_id"], unique=False
    )
    op.create_index(
        "ix_crawler_tasks_unaccepted",
        "crawler_tasks",
        ["published_at"],
        unique=False,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_table(
        "materials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("work_id", sa.Uuid(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["work_id"],
            ["works.id"],
            name=op.f("fk_materials_work_id_works"),
            ondelete="CASCADE",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_materials")),
    )
    op.create_index(op.f("ix_materials_work_id"), "materials", ["work_id"], unique=False)
    op.create_table(
        "analysis_stages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("run", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("segment", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "started",
                "succeeded",
                "retrying",
                "failed",
                "needs_review",
                "skipped",
                name="stagestatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("prompt_version", sa.Integer(), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["analysis_jobs.id"],
            name=op.f("fk_analysis_stages_job_id_analysis_jobs"),
            ondelete="CASCADE",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_stages")),
        sa.UniqueConstraint(
            "job_id",
            "run",
            "stage",
            "segment",
            name=op.f("uq_analysis_stages_job_id"),
            postgresql_nulls_not_distinct=True,
        ),
    )


def downgrade() -> None:
    for table in (
        "analysis_stages",
        "materials",
        "crawler_tasks",
        "analysis_jobs",
        "works",
        "qr_codes",
        "events",
        "creators",
        "outbox",
        "executors",
        "batches",
    ):
        op.drop_table(table)

"""PostgreSQL 爬虫认领与执行器归属

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

TIME = sa.DateTime(timezone=True)
ACTIVE_ATTEMPT = sa.text("status IN ('leased', 'running')")


def upgrade() -> None:
    op.create_table(
        "crawler_task_runs",
        sa.Column("task_id", sa.Uuid(), primary_key=True),
        sa.Column("run", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=False),
        sa.Column("work_id", sa.String(64)),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avoid", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("history", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("next_eligible_at", TIME, nullable=False, server_default=sa.func.now()),
        sa.Column("current_attempt_id", sa.Uuid()),
        sa.Column("created_at", TIME, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TIME, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["batches.id"], ondelete="CASCADE", deferrable=True, initially="DEFERRED"
        ),
    )
    op.create_index("ix_crawler_task_runs_batch_id", "crawler_task_runs", ["batch_id"])
    op.create_index("ix_crawler_task_runs_creator_id", "crawler_task_runs", ["creator_id"])
    op.create_index(
        "ix_crawler_task_runs_eligible",
        "crawler_task_runs",
        ["kind", "next_eligible_at", "created_at", "task_id"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_table(
        "crawler_task_attempts",
        sa.Column("attempt_id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("run", sa.Integer(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("executor_id", sa.String(128), nullable=False),
        sa.Column("executor_session_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("claimed_at", TIME, nullable=False),
        sa.Column("lease_expires_at", TIME, nullable=False),
        sa.Column("ended_at", TIME),
        sa.Column("error", sa.Text()),
        sa.Column("route_kind", sa.String(32)),
        sa.ForeignKeyConstraint(
            ["task_id", "run"],
            ["crawler_task_runs.task_id", "crawler_task_runs.run"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint("task_id", "run", "attempt_no"),
    )
    op.create_index(
        "uq_crawler_task_attempts_active",
        "crawler_task_attempts",
        ["task_id", "run"],
        unique=True,
        postgresql_where=ACTIVE_ATTEMPT,
    )
    op.create_index(
        "ix_crawler_task_attempts_expiry", "crawler_task_attempts", ["status", "lease_expires_at"]
    )
    op.create_index(
        "ix_crawler_task_attempts_executor_claimed",
        "crawler_task_attempts",
        ["executor_id", sa.text("claimed_at DESC")],
    )
    op.create_index(
        "ix_crawler_task_attempts_task_run", "crawler_task_attempts", ["task_id", "run"]
    )
    op.create_table(
        "analysis_stage_attempts",
        sa.Column("attempt_id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("run", sa.Integer(), nullable=False),
        sa.Column("retry_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("segment", sa.Integer()),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("executor_id", sa.String(128)),
        sa.Column("executor_session_id", sa.Uuid()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", TIME),
        sa.Column("ended_at", TIME),
        sa.Column("error", sa.Text()),
        sa.Column("model", sa.String(128)),
        sa.Column("batch_id", sa.Uuid()),
        sa.Column("creator_id", sa.Uuid()),
        sa.Column("work_id", sa.String(64)),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["batches.id"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    op.create_index("ix_analysis_stage_attempts_batch_id", "analysis_stage_attempts", ["batch_id"])
    op.create_index(
        "ix_analysis_stage_attempts_job_run", "analysis_stage_attempts", ["job_id", "run"]
    )
    op.create_index(
        "ix_analysis_stage_attempts_executor_ended",
        "analysis_stage_attempts",
        ["executor_id", "ended_at"],
    )
    for table, columns in attribution_columns().items():
        for column in columns:
            op.add_column(table, column)
    op.add_column("analysis_jobs", sa.Column("last_executor_at", TIME))
    op.alter_column("analysis_stages", "stage", type_=sa.String(64), existing_type=sa.String(32))

    # 已发布和已受理的数据都保有原始协议消息。下载作品使用平台作品 ID。
    op.execute(
        """
        INSERT INTO crawler_task_runs
            (task_id, run, batch_id, creator_id, work_id, kind, payload, status, created_at)
        SELECT t.id, t.run, t.batch_id, t.creator_id,
            coalesce(w.platform_id, t.message #>> '{work,work_id}'),
            CASE t.kind WHEN 'crawl' THEN 'crawl_creator' ELSE 'download_video' END,
            t.message, 'queued', t.published_at
        FROM crawler_tasks t
        LEFT JOIN works w ON w.id = t.work_id
        WHERE t.status IN ('published', 'accepted')
        """
    )


def attribution_columns() -> dict[str, list[sa.Column]]:
    return {
        "crawler_tasks": [
            sa.Column("current_attempt_id", sa.Uuid()),
            sa.Column("last_started_executor_id", sa.String(128)),
            sa.Column("result_executor_id", sa.String(128)),
        ],
        "materials": [
            sa.Column("source_attempt_id", sa.Uuid()),
            sa.Column("source_executor_id", sa.String(128)),
        ],
        "proxy_leases": [sa.Column("attempt_id", sa.Uuid())],
        "events": [
            sa.Column("executor_id", sa.String(128)),
            sa.Column("executor_session_id", sa.Uuid()),
            sa.Column("attempt_id", sa.Uuid()),
        ],
        **{
            table: [
                sa.Column("last_executor_id", sa.String(128)),
                sa.Column("result_executor_id", sa.String(128)),
                sa.Column("retry_generation", sa.Integer()),
            ]
            for table in ("analysis_jobs", "analysis_stages")
        },
    }


def downgrade() -> None:
    op.alter_column("analysis_stages", "stage", type_=sa.String(32), existing_type=sa.String(64))
    for table, columns in attribution_columns().items():
        for column in columns:
            op.drop_column(table, column.name)
    op.drop_column("analysis_jobs", "last_executor_at")
    op.drop_table("analysis_stage_attempts")
    op.drop_table("crawler_task_attempts")
    op.drop_table("crawler_task_runs")

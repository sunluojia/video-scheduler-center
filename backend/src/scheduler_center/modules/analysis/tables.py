from sqlalchemy import Column, Index, Integer, String, Table, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB

from scheduler_center.core.database.mapping import (
    enum_column,
    mapper_registry,
    metadata,
    reference,
    timestamp,
)
from scheduler_center.modules.analysis.models import (
    AnalysisJob,
    AnalysisStage,
    JobKind,
    JobStatus,
    StageStatus,
)

analysis_jobs = Table(
    "analysis_jobs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("kind", enum_column(JobKind), nullable=False),
    Column("batch_id", reference("batches.id"), nullable=False, index=True),
    Column("creator_id", reference("creators.id"), nullable=False, index=True),
    Column("work_id", reference("works.id"), unique=True),
    Column("message", JSONB, nullable=False),
    Column("sources", JSONB),
    Column("run", Integer, nullable=False),
    Column("status", enum_column(JobStatus), nullable=False),
    Column("result", JSONB),
    Column("error", Text),
    Column("reused_from", String(128)),
    Column("artifacts_until", timestamp()),
    Column("submitted_at", timestamp(), nullable=False),
    Column("accepted_at", timestamp()),
    Column("finished_at", timestamp()),
    Column("event_at", timestamp()),
    Index(
        "ix_analysis_jobs_unaccepted",
        "submitted_at",
        postgresql_where=text("status = 'submitted'"),
    ),
)

analysis_stages = Table(
    "analysis_stages",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("job_id", reference("analysis_jobs.id"), nullable=False),
    Column("run", Integer, nullable=False),
    Column("stage", String(32), nullable=False),
    Column("segment", Integer),
    Column("status", enum_column(StageStatus), nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("model", String(255)),
    Column("prompt_version", Integer),
    Column("result", JSONB),
    Column("error", Text),
    Column("retry_at", timestamp()),
    Column("started_at", timestamp()),
    Column("occurred_at", timestamp(), nullable=False),
    UniqueConstraint("job_id", "run", "stage", "segment", postgresql_nulls_not_distinct=True),
)

mapper_registry.map_imperatively(AnalysisJob, analysis_jobs)
mapper_registry.map_imperatively(AnalysisStage, analysis_stages)

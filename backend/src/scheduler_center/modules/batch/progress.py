"""从作品状态和当前分析轮次推导工作台上的达人进度。"""

from sqlalchemy import String, and_, case, cast, func, or_, select

from scheduler_center.modules.analysis.models import StageStatus
from scheduler_center.modules.analysis.tables import analysis_jobs, analysis_stages
from scheduler_center.modules.batch.models import (
    CreatorProgress,
    CreatorStatus,
    WorkKind,
    WorkStatus,
)
from scheduler_center.modules.batch.tables import creators, works


def creator_progress():
    """每个达人只计入最早还没结束的阶段，完成或失败的作品不阻挡其他作品。"""
    video = (works.c.creator_id == creators.c.id, works.c.kind == WorkKind.VIDEO)
    downloading = (
        select(1)
        .where(*video, works.c.status == WorkStatus.DOWNLOADING)
        .correlate(creators)
        .exists()
    )
    pending_audio = (
        select(1)
        .select_from(works.outerjoin(analysis_jobs, analysis_jobs.c.work_id == works.c.id))
        .where(
            *video,
            works.c.status.in_((WorkStatus.ANALYZING, WorkStatus.NEEDS_REVIEW)),
            ~_audio_finished(),
        )
        .correlate(creators)
        .exists()
    )
    return case(
        (creators.c.status != CreatorStatus.ANALYZING, cast(creators.c.status, String)),
        (downloading, CreatorProgress.DOWNLOADING.value),
        (pending_audio, CreatorProgress.AUDIO_ANALYSIS.value),
        else_=CreatorProgress.VIDEO_ANALYSIS.value,
    )


def _audio_finished():
    stage = analysis_stages.c
    current_run = (stage.job_id == analysis_jobs.c.id, stage.run == analysis_jobs.c.run)
    listened_segments = (
        select(func.count())
        .select_from(analysis_stages)
        .where(
            *current_run,
            stage.stage == "listen",
            stage.segment.is_not(None),
            stage.status == StageStatus.SUCCEEDED,
        )
        .correlate(analysis_jobs)
        .scalar_subquery()
    )
    segment_count = stage.result["segment_count"]
    return (
        select(1)
        .where(
            *current_run,
            or_(
                stage.stage == "recognize",
                and_(
                    stage.stage == "fetch",
                    stage.status == StageStatus.SUCCEEDED,
                    stage.result["has_audio"].astext == "false",
                ),
                and_(
                    stage.stage == "extract_audio",
                    or_(
                        stage.status == StageStatus.SKIPPED,
                        and_(
                            stage.status == StageStatus.SUCCEEDED,
                            func.jsonb_typeof(segment_count) == "number",
                            func.to_jsonb(listened_segments) >= segment_count,
                        ),
                    ),
                ),
            ),
        )
        .correlate(analysis_jobs)
        .exists()
    )

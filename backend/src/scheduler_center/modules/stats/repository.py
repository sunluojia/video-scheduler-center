"""数据看板的统计：一段时间里各环节做完了多少、用了多久、为什么失败。

计数来自事件：爬取程序回传的结论和下载失败、调度中心收下视频时记的“已上传”、分析服务回传的结果。
事件一直保留，重新爬取、重新下载、补跑都再算一次，反映实际做了多少。
用时来自任务的发布和结束时间；当前分布来自达人和视频的状态。
"""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import BigInteger, ColumnElement, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONPATH

from scheduler_center.core.common.clock import BEIJING
from scheduler_center.core.database.repository import Repository
from scheduler_center.modules.analysis.models import JobKind, JobStatus, StageStatus
from scheduler_center.modules.analysis.tables import analysis_jobs, analysis_stages
from scheduler_center.modules.batch.models import BatchStatus, CreatorProgress, WorkKind
from scheduler_center.modules.batch.progress import creator_progress
from scheduler_center.modules.batch.tables import batches, creators, works
from scheduler_center.modules.crawl.models import (
    CrawlConclusion,
    CrawlerTaskKind,
    CrawlerTaskStatus,
)
from scheduler_center.modules.crawl.tables import crawler_tasks
from scheduler_center.modules.flow.history import Source, events
from scheduler_center.modules.flow.service import UNREACHABLE
from scheduler_center.modules.stats.schemas import (
    Bucket,
    Counts,
    DurationRow,
    FailureRow,
    StageRow,
    Stats,
    TimelineRow,
)

TIME_ZONE = "Asia/Shanghai"
"""按北京时间的整点、零点分时段，和导出表的时间一致。"""
LAST_ATTEMPT = r"^.*第 \d+ 次："
"""爬取程序把每次尝试的原因用“第 N 次：”连起来回传，失败原因按最后一次归类。"""
REASON_LENGTH = 120
TOP_REASONS = 8
UNSETTLED = (JobStatus.FAILED, JobStatus.NEEDS_REVIEW)


class StatsRepository(Repository):
    async def stats(
        self, since: datetime, until: datetime, bucket: Bucket, batch_id: UUID | None
    ) -> Stats:
        timeline = await self._timeline(since, until, bucket, batch_id)
        totals = Counts(
            **{name: sum(getattr(row, name) for row in timeline) for name in Counts.model_fields}
        )
        scope = _scope(since, until, batch_id)
        return Stats(
            since=since,
            until=until,
            bucket=bucket,
            batch_id=batch_id,
            totals=totals,
            conclusions=await self._conclusions(since, until, batch_id),
            timeline=timeline,
            stages=await self._stages(since, until, batch_id),
            durations=await self._durations(since, until, batch_id),
            failures=await self._failures(since, until, batch_id),
            creators=await self._creators(scope),
            videos=await self._videos(scope),
        )

    async def _timeline(
        self, since: datetime, until: datetime, bucket: Bucket, batch_id: UUID | None
    ) -> list[TimelineRow]:
        """每个时段各环节的完成数；没有数据的时段补零，图表的横轴连续。"""
        event, payload = events.c, events.c.payload
        crawled = _crawler(event_type="crawled")
        conclusion = payload["conclusion"].astext
        failed = conclusion == CrawlConclusion.FAILED
        has_profile = func.jsonb_typeof(payload["profile"]) == "object"
        status, kind = payload["status"].astext, payload["kind"].astext
        video = and_(_analysis("job"), kind == JobKind.VIDEO)
        summary = and_(_analysis("job"), kind == JobKind.SUMMARY)
        completed = status == JobStatus.COMPLETED
        counts: dict[str, ColumnElement[bool]] = {
            "profile_ok": and_(crawled, or_(~failed, has_profile)),
            "profile_failed": and_(crawled, failed, ~has_profile),
            "listing_ok": and_(crawled, conclusion == CrawlConclusion.OK),
            "listing_failed": and_(crawled, failed, has_profile),
            "unreachable": and_(crawled, conclusion.in_(list(UNREACHABLE))),
            "downloaded": _crawler(event_type="uploaded"),
            "download_failed": _crawler(event_type="download_failed"),
            "analyzed": and_(video, completed),
            "reused": and_(video, completed, payload["reused_from"].astext.is_not(None)),
            "analysis_failed": and_(video, status.in_(UNSETTLED)),
            "summarized": and_(summary, completed),
            "summary_failed": and_(summary, status.in_(UNSETTLED)),
        }
        found_videos = func.jsonb_array_length(
            func.jsonb_path_query_array(
                payload["works"], cast('$[*] ? (@.type == "video")', JSONPATH)
            )
        )
        at = func.date_trunc(bucket.value, event.occurred_at, TIME_ZONE).label("at")
        stmt = (
            select(
                at,
                *(func.count().filter(condition).label(name) for name, condition in counts.items()),
                _sum(found_videos, crawled, conclusion == CrawlConclusion.OK).label("videos_found"),
                _sum(
                    payload["size"].astext.cast(BigInteger), _crawler(event_type="uploaded")
                ).label("downloaded_bytes"),
            )
            .where(*_during(event.occurred_at, since, until), *_of_batch(event.batch_id, batch_id))
            .group_by(at)
        )
        found = {row.at: row for row in (await self._session.execute(stmt)).mappings()}
        return [
            TimelineRow.model_validate({**(found.get(start) or {}), "at": start})
            for start in _buckets(since, until, bucket)
        ]

    async def _conclusions(
        self, since: datetime, until: datetime, batch_id: UUID | None
    ) -> dict[CrawlConclusion, int]:
        conclusion = events.c.payload["conclusion"].astext
        stmt = (
            select(conclusion, func.count())
            .where(
                _crawler(event_type="crawled"),
                *_during(events.c.occurred_at, since, until),
                *_of_batch(events.c.batch_id, batch_id),
            )
            .group_by(conclusion)
        )
        return {CrawlConclusion(name): count for name, count in await self._session.execute(stmt)}

    async def _stages(
        self, since: datetime, until: datetime, batch_id: UUID | None
    ) -> list[StageRow]:
        """分析服务各阶段的结果，按阶段事件统计。"""
        payload = events.c.payload
        stage, status = payload["stage"].astext, payload["status"].astext
        stmt = (
            select(
                stage.label("stage"),
                func.count().filter(status == StageStatus.SUCCEEDED).label("succeeded"),
                func.count()
                .filter(status.in_([StageStatus.FAILED, StageStatus.NEEDS_REVIEW]))
                .label("failed"),
                func.count().filter(status == StageStatus.RETRYING).label("retried"),
                func.count().filter(status == StageStatus.SKIPPED).label("skipped"),
            )
            .where(
                _analysis("stage"),
                *_during(events.c.occurred_at, since, until),
                *_of_batch(events.c.batch_id, batch_id),
            )
            .group_by(stage)
        )
        rows = [
            StageRow.model_validate(row) for row in (await self._session.execute(stmt)).mappings()
        ]
        return sorted(rows, key=lambda row: STAGE_ORDER.get(row.stage, len(STAGE_ORDER)))

    async def _durations(
        self, since: datetime, until: datetime, batch_id: UUID | None
    ) -> list[DurationRow]:
        """在这段时间里结束的任务和分析阶段各用了多久。"""
        task, job, stage = crawler_tasks.c, analysis_jobs.c, analysis_stages.c
        crawl = (
            "crawl",
            task.finished_at - task.published_at,
            task.finished_at,
            task.batch_id,
            [task.kind == CrawlerTaskKind.CRAWL, task.status != CrawlerTaskStatus.CANCELLED],
        )
        download = (
            "download",
            task.finished_at - task.published_at,
            task.finished_at,
            task.batch_id,
            [task.kind == CrawlerTaskKind.DOWNLOAD, task.status == CrawlerTaskStatus.SUCCEEDED],
        )
        jobs = [
            (
                name,
                job.finished_at - job.submitted_at,
                job.finished_at,
                job.batch_id,
                [job.kind == kind, job.status == JobStatus.COMPLETED],
            )
            for name, kind in (("analysis", JobKind.VIDEO), ("summary", JobKind.SUMMARY))
        ]
        rows = []
        for name, spent, ended, owner, conditions in [crawl, download, *jobs]:
            stmt = select(*_spread(spent)).where(
                *conditions, *_during(ended, since, until), *_of_batch(owner, batch_id)
            )
            row = (await self._session.execute(stmt)).mappings().one()
            if row["count"]:
                rows.append(DurationRow(stage=name, **row))
        # 分析服务的各阶段：从开始到完成
        stmt = (
            select(stage.stage, *_spread(stage.occurred_at - stage.started_at))
            .select_from(analysis_stages.join(analysis_jobs, job.id == stage.job_id))
            .where(
                stage.status == StageStatus.SUCCEEDED,
                stage.started_at.is_not(None),
                *_during(stage.occurred_at, since, until),
                *_of_batch(job.batch_id, batch_id),
            )
            .group_by(stage.stage)
        )
        found = [
            DurationRow.model_validate(row)
            for row in (await self._session.execute(stmt)).mappings()
        ]
        return rows + sorted(found, key=lambda row: STAGE_ORDER.get(row.stage, len(STAGE_ORDER)))

    async def _failures(
        self, since: datetime, until: datetime, batch_id: UUID | None
    ) -> list[FailureRow]:
        payload = events.c.payload
        reason = func.left(
            func.regexp_replace(
                func.coalesce(payload["reason"].astext, payload["error"].astext, "未说明原因"),
                LAST_ATTEMPT,
                "",
            ),
            REASON_LENGTH,
        )
        kind, status = payload["kind"].astext, payload["status"].astext
        cases = [
            (
                "crawl",
                and_(_crawler(event_type="crawled"), payload["conclusion"].astext == "failed"),
            ),
            ("download", _crawler(event_type="download_failed")),
            ("analysis", and_(_analysis("job"), kind == JobKind.VIDEO, status.in_(UNSETTLED))),
            ("summary", and_(_analysis("job"), kind == JobKind.SUMMARY, status.in_(UNSETTLED))),
        ]
        rows = []
        for name, condition in cases:
            stmt = (
                select(reason.label("reason"), func.count().label("count"))
                .where(
                    condition,
                    *_during(events.c.occurred_at, since, until),
                    *_of_batch(events.c.batch_id, batch_id),
                )
                .group_by(reason)
                .order_by(func.count().desc(), reason)
                .limit(TOP_REASONS)
            )
            rows += [
                FailureRow(stage=name, reason=row.reason, count=row.count)
                for row in await self._session.execute(stmt)
            ]
        return rows

    async def _creators(self, scope: ColumnElement[bool]) -> dict[CreatorProgress, int]:
        progress = creator_progress().label("progress")
        stmt = (
            select(progress, func.count())
            .select_from(creators.join(batches, batches.c.id == creators.c.batch_id))
            .where(scope)
            .group_by("progress")
        )
        return {CreatorProgress(name): count for name, count in await self._session.execute(stmt)}

    async def _videos(self, scope: ColumnElement[bool]) -> dict:
        stmt = (
            select(works.c.status, func.count())
            .select_from(works.join(batches, batches.c.id == works.c.batch_id))
            .where(scope, works.c.kind == WorkKind.VIDEO)
            .group_by(works.c.status)
        )
        return dict((await self._session.execute(stmt)).all())


STAGE_ORDER = {
    name: index
    for index, name in enumerate(
        [
            "crawl",
            "download",
            "analysis",
            "summary",
            "fetch",
            "extract_frames",
            "extract_audio",
            "listen",
            "recognize",
        ]
    )
}


def _scope(since: datetime, until: datetime, batch_id: UUID | None) -> ColumnElement[bool]:
    """当前分布看哪些批次：指定的批次，或这段时间里进行过的批次。"""
    if batch_id is not None:
        return batches.c.id == batch_id
    return and_(
        batches.c.status != BatchStatus.DRAFT,
        batches.c.started_at < until,
        or_(batches.c.finished_at.is_(None), batches.c.finished_at >= since),
    )


def _crawler(*, event_type: str) -> ColumnElement[bool]:
    return and_(events.c.source == Source.CRAWLER, events.c.type == event_type)


def _analysis(event_type: str) -> ColumnElement[bool]:
    return and_(events.c.source == Source.ANALYSIS, events.c.type == event_type)


def _during(column, since: datetime, until: datetime) -> list[ColumnElement[bool]]:
    return [column >= since, column < until]


def _of_batch(column, batch_id: UUID | None) -> list[ColumnElement[bool]]:
    return [] if batch_id is None else [column == batch_id]


def _sum(value, *conditions):
    return func.coalesce(func.sum(value).filter(*conditions), 0)


def _spread(spent):
    """一组用时（interval）的个数、平均、中位数和 90 分位，单位秒。"""
    seconds = func.extract("epoch", spent)
    return (
        func.count().label("count"),
        func.coalesce(func.avg(seconds), 0).label("average"),
        func.coalesce(func.percentile_cont(0.5).within_group(seconds), 0).label("median"),
        func.coalesce(func.percentile_cont(0.9).within_group(seconds), 0).label("p90"),
    )


def _buckets(since: datetime, until: datetime, bucket: Bucket) -> list[datetime]:
    """从起点所在的时段到终点，每个时段的开始时间（北京时间）。"""
    start = since.astimezone(BEIJING).replace(minute=0, second=0, microsecond=0)
    step = timedelta(hours=1)
    if bucket == Bucket.DAY:
        start, step = start.replace(hour=0), timedelta(days=1)
    starts = []
    while start < until:
        starts.append(start)
        start += step
    return starts

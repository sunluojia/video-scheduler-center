"""业务推进：把爬取程序和分析服务回传的结果变成达人、作品的状态和结论，并发布后续任务。

同一位达人的变化都先锁住达人这一行，事件、上传和人工操作因此按达人串行。
人工操作可能让已完成的批次重新进行，先锁批次再锁达人；事件处理只锁达人。
"""

import logging
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from scheduler_center.core.common.clock import utcnow
from scheduler_center.core.common.errors import Conflict, NotFound
from scheduler_center.core.messaging.outbox import Outbox, Route
from scheduler_center.modules.analysis.messages import (
    ANALYZE_VIDEO,
    SUMMARIZE_CREATOR,
    AnalysisEvent,
    AnalyzeVideo,
    JobEvent,
    JobState,
    MaterialRef,
    SourceVideo,
    StageEvent,
    SummarizeCreator,
)
from scheduler_center.modules.analysis.models import (
    AnalysisJob,
    AnalysisStage,
    JobKind,
    JobStatus,
    StageStatus,
)
from scheduler_center.modules.analysis.repository import AnalysisJobRepository
from scheduler_center.modules.batch.models import (
    WORK_FINAL,
    Batch,
    BatchStatus,
    Creator,
    CreatorStatus,
    Work,
    WorkKind,
    WorkStatus,
)
from scheduler_center.modules.batch.repository import (
    BatchRepository,
    CreatorRepository,
    WorkRepository,
)
from scheduler_center.modules.crawl.messages import (
    CRAWL_CREATOR,
    DOWNLOAD_VIDEO,
    Accepted,
    CrawlCreator,
    Crawled,
    CrawlerEvent,
    CreatorRef,
    DownloadFailed,
    DownloadVideo,
    Uploaded,
    WorkRef,
)
from scheduler_center.modules.crawl.models import (
    CrawlConclusion,
    CrawlerTask,
    CrawlerTaskKind,
    CrawlerTaskStatus,
)
from scheduler_center.modules.crawl.repository import CrawlerTaskRepository
from scheduler_center.modules.flow.history import EventLog, Source
from scheduler_center.modules.live.notifier import Live
from scheduler_center.modules.material.models import Material
from scheduler_center.modules.material.repository import MaterialRepository

log = logging.getLogger(__name__)

WORK_STATUS = {
    JobStatus.SUBMITTED: WorkStatus.ANALYZING,
    JobStatus.ACCEPTED: WorkStatus.ANALYZING,
    JobStatus.PROCESSING: WorkStatus.ANALYZING,
    JobStatus.COMPLETED: WorkStatus.COMPLETED,
    JobStatus.FAILED: WorkStatus.FAILED,
    JobStatus.NEEDS_REVIEW: WorkStatus.NEEDS_REVIEW,
    JobStatus.ABANDONED: WorkStatus.ABANDONED,
}
"""分析任务的状态对应的视频状态；取消的任务不再改变视频。"""

UNREACHABLE = {
    CrawlConclusion.PRIVATE: "私密账号",
    CrawlConclusion.LOGIN_REQUIRED: "需要登录",
    CrawlConclusion.NOT_FOUND: "找不到此达人",
}
REDO = (WorkStatus.COMPLETED, WorkStatus.FAILED, WorkStatus.NEEDS_REVIEW, WorkStatus.ABANDONED)
"""可以重新下载或重新分析的视频：已经得出结果，或等待人工处理。"""


class Flow:
    def __init__(self, session: AsyncSession) -> None:
        self._batches = BatchRepository(session)
        self._creators = CreatorRepository(session)
        self._works = WorkRepository(session)
        self._tasks = CrawlerTaskRepository(session)
        self._jobs = AnalysisJobRepository(session)
        self._materials = MaterialRepository(session)
        self._outbox = Outbox(session)
        self._events = EventLog(session)
        self._live = Live(session)

    # 批次

    async def start(self, batch_id: UUID) -> Batch:
        """每位可识别的达人发布一个爬取任务。"""
        now = utcnow()
        batch = await self._batches.require(Batch, batch_id, lock=True)
        batch.start(now)
        for creator in await self._creators.of_batch(batch.id):
            if creator.handle is not None:
                await self._crawl(batch, creator, None, now)
        await self._live.changed(batch.id)
        return batch

    async def pause(self, batch_id: UUID) -> Batch:
        """暂停采集：爬取程序不再开始这个批次新的获取作品，已锁定作品的下载、识别和汇总照常。

        这里只记录状态，已发布的爬取任务由爬取程序暂缓；继续之前批次不会结束。
        """
        batch = await self._batches.require(Batch, batch_id, lock=True)
        batch.pause()
        await self._live.changed(batch.id)
        return batch

    async def resume(self, batch_id: UUID) -> Batch:
        batch = await self._batches.require(Batch, batch_id, lock=True)
        batch.resume()
        await self._live.changed(batch.id)
        return batch

    async def cancel(self, batch_id: UUID) -> Batch:
        now = utcnow()
        batch = await self._batches.require(Batch, batch_id, lock=True)
        batch.cancel(now)
        await self._creators.of_batch(batch.id, lock=True)  # 等正在处理的事件结束
        await self._tasks.cancel_batch(batch.id, now)
        await self._jobs.cancel_batch(batch.id, now)
        await self._outbox.withdraw(batch.id)
        await self._materials.discard_for_batch(batch.id, now)
        await self._live.changed(batch.id)
        return batch

    async def delete(self, batch_id: UUID) -> None:
        """删除批次及其全部数据；原视频文件由 Worker 随后清理。"""
        batch = await self._batches.require(Batch, batch_id, lock=True)
        if not batch.deletable:
            raise Conflict("进行中或已暂停的批次需要先取消")
        await self._batches.delete(batch)
        await self._live.changed(batch_id)

    async def complete_if_done(self, batch_id: UUID) -> None:
        """全部达人都得出结论后，批次完成。"""
        batch = await self._batches.require(Batch, batch_id, lock=True)
        if batch.status == BatchStatus.RUNNING and not await self._creators.unfinished(batch.id):
            batch.complete(utcnow())
            await self._live.changed(batch.id)

    async def recrawl_batch(self, batch_id: UUID) -> int:
        """重新爬取批次中需要重新抓取的达人。"""
        now = utcnow()
        batch = await self._batches.require(Batch, batch_id, lock=True)
        batch.reopen()
        creators = await self._creators.of_batch(batch.id, status=CreatorStatus.RECRAWL, lock=True)
        for creator in creators:
            await self._recrawl(batch, creator, now)
        await self._live.changed(batch.id)
        return len(creators)

    # 爬取程序

    async def on_crawler_event(self, event: CrawlerEvent) -> None:
        task = await self._tasks.get(CrawlerTask, event.task_id)
        if task is None:
            log.warning("爬取程序的事件没有对应的任务，忽略：%s", event.task_id)
            return
        creator = await self._creators.require(Creator, task.creator_id, lock=True)
        if not await self._events.record(Source.CRAWLER, task.id, task.batch_id, event):
            return
        task = await self._tasks.require(CrawlerTask, task.id, lock=True)
        if event.run != task.run or not task.active:
            return  # 旧轮次或已结束的任务，只记入历史
        now = utcnow()
        match event:
            case Accepted():
                task.accept(now)
            case Crawled():
                await self._crawled(task, creator, event, now)
            case DownloadFailed():
                task.finish(CrawlerTaskStatus.FAILED, now, event.reason)
                work = await self._works.require(Work, task.work_id)
                work.settle(WorkStatus.FAILED, now, f"下载失败：{event.reason}")
                await self._settle(creator, now)
        await self._live.changed(creator.batch_id, creator.id)

    async def _crawled(self, task: CrawlerTask, creator: Creator, event: Crawled, now: datetime):
        failed = event.conclusion == CrawlConclusion.FAILED
        task.conclusion = event.conclusion
        task.finish(CrawlerTaskStatus.FAILED if failed else CrawlerTaskStatus.SUCCEEDED, now)
        task.error = event.reason
        if event.profile:
            creator.profile = event.profile.model_dump()
        if failed:
            creator.settle(CreatorStatus.RECRAWL, now, f"爬取失败：{event.reason or '未说明原因'}")
        elif event.conclusion in UNREACHABLE:
            creator.settle(CreatorStatus.FAILED, now, UNREACHABLE[event.conclusion])
        else:
            works = [
                Work(
                    batch_id=creator.batch_id,
                    creator_id=creator.id,
                    position=item.position,
                    platform_id=item.work_id,
                    url=item.url,
                    kind=item.type,
                    published_at=item.published_at,
                    description=item.description,
                    topics=item.topics,
                )
                for item in event.works
            ]
            for work in works:
                self._works.add(work)
                if work.kind == WorkKind.VIDEO:
                    await self._download(creator, work, None, now)
            creator.crawled(works, now)

    async def check_upload(self, task_id: UUID, run: int, sha256: str) -> bool:
        """接收视频之前检查；返回 True 表示这一轮已经上传过相同内容。"""
        task = await self._download_task(task_id)
        return await self._uploaded_before(task, run, sha256)

    async def upload(
        self, task_id: UUID, run: int, material_id: UUID, size: int, sha256: str
    ) -> bool:
        """登记上传的视频并提交分析；返回 False 表示相同内容已经上传过，文件不再需要。"""
        task = await self._download_task(task_id)
        creator = await self._creators.require(Creator, task.creator_id, lock=True)
        task = await self._tasks.require(CrawlerTask, task.id, lock=True)
        if await self._uploaded_before(task, run, sha256):
            return False
        now = utcnow()
        work = await self._works.require(Work, task.work_id)
        material = Material(work.id, size, sha256, material_id)
        self._materials.add(material)
        uploaded = Uploaded(
            event_id=material.id, task_id=task.id, run=run, occurred_at=now, size=size
        )
        await self._events.record(Source.CRAWLER, task.id, task.batch_id, uploaded)
        task.finish(CrawlerTaskStatus.SUCCEEDED, now)
        await self._analyze(creator, work, material, now)
        await self._settle(creator, now)
        await self._live.changed(creator.batch_id, creator.id)
        return True

    async def _download_task(self, task_id: UUID) -> CrawlerTask:
        task = await self._tasks.get(CrawlerTask, task_id)
        if task is None or task.kind != CrawlerTaskKind.DOWNLOAD:
            raise NotFound(f"下载任务 {task_id} 不存在")
        return task

    async def _uploaded_before(self, task: CrawlerTask, run: int, sha256: str) -> bool:
        if run != task.run or task.status == CrawlerTaskStatus.CANCELLED:
            raise Conflict("下载任务已取消，或已有更新的轮次")
        if task.status == CrawlerTaskStatus.FAILED:
            raise Conflict("下载任务已经报告失败")
        if task.status != CrawlerTaskStatus.SUCCEEDED:
            return False
        work = await self._works.require(Work, task.work_id)
        material = work.material_id and await self._materials.get(Material, work.material_id)
        if material and material.sha256 == sha256:
            return True
        raise Conflict("这一轮已经上传过其他内容")

    # 分析服务

    async def on_analysis_event(self, event: AnalysisEvent) -> None:
        job = await self._jobs.get(AnalysisJob, event.job_id)
        if job is None:
            log.warning("分析服务的事件没有对应的任务，忽略：%s", event.job_id)
            return
        creator = await self._creators.require(Creator, job.creator_id, lock=True)
        if not await self._events.record(Source.ANALYSIS, job.id, job.batch_id, event):
            return
        job = await self._jobs.require(AnalysisJob, job.id, lock=True)
        if event.run != job.run:
            return
        now = utcnow()
        match event:
            case StageEvent():
                await self._stage(job, event)
                job.processing()
            case JobEvent():
                adopted = job.follow(
                    event.status,
                    event.occurred_at,
                    result=event.result,
                    error=event.error,
                    reused_from=event.reused_from,
                    artifacts_until=event.artifacts_until,
                )
                if adopted:  # 没采纳的旧事件不改动作品，以免覆盖之后的重新下载结果
                    await self._job_changed(creator, job, now)
        await self._live.changed(creator.batch_id, creator.id)

    async def _stage(self, job: AnalysisJob, event: StageEvent) -> None:
        stage = await self._jobs.stage(job.id, event.run, event.stage, event.segment)
        if stage is None:
            stage = AnalysisStage(
                job.id, event.run, event.stage, event.segment, event.status, event.occurred_at
            )
            self._jobs.add(stage)
        elif event.occurred_at < stage.occurred_at:
            return
        if event.status == StageStatus.STARTED:
            stage.started_at = event.occurred_at
        stage.status, stage.occurred_at, stage.attempt = (
            event.status,
            event.occurred_at,
            event.attempt,
        )
        stage.model, stage.prompt_version = event.model, event.prompt_version
        stage.result, stage.error, stage.retry_at = event.result, event.error, event.retry_at

    async def _job_changed(self, creator: Creator, job: AnalysisJob, now: datetime) -> None:
        if job.kind == JobKind.VIDEO:
            work = await self._works.require(Work, job.work_id)
            status = WORK_STATUS.get(job.status)
            if status is not None and work.status != WorkStatus.DOWNLOADING:
                work.settle(status, now, _job_error(job))
                completed = job.status == JobStatus.COMPLETED
                work.tags = job.result["recognition"]["tags"] if completed else None
            if work.status in (WorkStatus.COMPLETED, WorkStatus.ABANDONED):
                await self._discard_material(work, now)
        await self._settle(creator, now)

    async def retried(self, batch_id: UUID, states: list[JobState]) -> None:
        """分析服务补跑后返回的任务状态；只采纳确实重新开始的任务，其余以事件为准。

        回执可能晚于补跑之后的事件到达，和事件一样按分析服务的时间先后采纳。
        """
        now = utcnow()
        batch = await self._batches.require(Batch, batch_id, lock=True)
        batch.reopen()
        reopened = {state.job_id: state for state in states if state.status == JobStatus.PROCESSING}
        jobs = await self._jobs.of_ids(list(reopened))
        for job in sorted(jobs, key=lambda item: item.creator_id):  # 按达人 ID 顺序加锁
            creator = await self._creators.require(Creator, job.creator_id, lock=True)
            job = await self._jobs.require(AnalysisJob, job.id, lock=True)
            state = reopened[job.id]
            if state.run == job.run and job.follow(JobStatus.PROCESSING, state.occurred_at):
                await self._job_changed(creator, job, now)
        await self._live.changed(batch.id)

    # 人工操作

    async def recrawl(self, creator_id: UUID) -> Creator:
        batch, creator = await self._lock_creator(creator_id)
        if not creator.recrawlable:
            raise Conflict("达人正在处理中，不能重新爬取")
        await self._recrawl(batch, creator, utcnow())
        await self._live.changed(batch.id, creator.id)
        return creator

    async def resummarize(self, creator_id: UUID) -> Creator:
        _, creator = await self._lock_creator(creator_id)
        summary = await self._jobs.summary(creator.id)
        if summary is None or creator.status not in (
            CreatorStatus.SUMMARIZING,
            CreatorStatus.COMPLETED,
        ):
            raise Conflict("达人的视频还没有可以汇总的结果")
        await self._settle(creator, utcnow(), resummarize=True)
        await self._live.changed(creator.batch_id, creator.id)
        return creator

    async def redownload(self, work_id: UUID) -> Work:
        creator, work = await self._lock_work(work_id)
        now = utcnow()
        await self._discard_material(work, now)
        await self._download(creator, work, await self._tasks.download_task(work.id), now)
        await self._settle(creator, now)
        await self._live.changed(creator.batch_id, creator.id)
        return work

    async def reanalyze(self, work_id: UUID) -> Work:
        """原视频还在就直接重新分析，已清理的先重新下载。"""
        creator, work = await self._lock_work(work_id)
        material = work.material_id and await self._materials.get(Material, work.material_id)
        if not material or material.discarded_at is not None:
            return await self.redownload(work_id)
        now = utcnow()
        await self._analyze(creator, work, material, now)
        await self._settle(creator, now)
        await self._live.changed(creator.batch_id, creator.id)
        return work

    async def abandon(self, work_id: UUID) -> Work:
        creator, work = await self._lock_work(work_id)
        job = await self._jobs.video_job(work.id)
        if job is None or job.status != JobStatus.NEEDS_REVIEW:
            raise Conflict("只有待核查的视频可以放弃")
        now = utcnow()
        job.end(JobStatus.ABANDONED, now)
        await self._job_changed(creator, job, now)
        await self._live.changed(creator.batch_id, creator.id)
        return work

    async def retry_target(self, work_id: UUID) -> AnalysisJob:
        """补跑之前检查：只有分析失败或待核查的视频可以补跑，下载失败的需要重新下载。"""
        work = await self._works.require(Work, work_id)
        batch = await self._batches.require(Batch, work.batch_id)
        batch.reopen()
        job = await self._jobs.video_job(work.id)
        if (
            work.status not in (WorkStatus.FAILED, WorkStatus.NEEDS_REVIEW)
            or job is None
            or job.status not in (JobStatus.FAILED, JobStatus.NEEDS_REVIEW)
        ):
            raise Conflict("只有分析失败或待核查的视频可以补跑")
        return job

    async def _lock_creator(self, creator_id: UUID) -> tuple[Batch, Creator]:
        creator = await self._creators.require(Creator, creator_id)
        batch = await self._batches.require(Batch, creator.batch_id, lock=True)
        batch.reopen()
        return batch, await self._creators.require(Creator, creator_id, lock=True)

    async def _lock_work(self, work_id: UUID) -> tuple[Creator, Work]:
        work = await self._works.require(Work, work_id)
        _, creator = await self._lock_creator(work.creator_id)
        work = await self._works.require(Work, work_id, lock=True)
        if work.status not in REDO:
            raise Conflict("视频正在下载或分析中")
        return creator, work

    # 发布任务与得出结论

    async def _recrawl(self, batch: Batch, creator: Creator, now: datetime) -> None:
        """重新爬取：上一轮的作品连同下载、分析记录一起删除，以新锁定的作品为准。"""
        await self._works.remove_of_creator(creator.id)
        await self._crawl(batch, creator, await self._tasks.crawl_task(creator.id), now)

    async def _crawl(
        self, batch: Batch, creator: Creator, task: CrawlerTask | None, now: datetime
    ) -> None:
        if task is None:
            task = CrawlerTask(CrawlerTaskKind.CRAWL, batch.id, creator.id, {})
            self._tasks.add(task)
        else:
            task.republish(now)
        message = CrawlCreator(
            task_id=task.id,
            run=task.run,
            batch_id=batch.id,
            creator=_creator_ref(creator),
            cutoff=batch.started_at,
        )
        await self._publish(task, CRAWL_CREATOR, message)
        creator.crawl(now)

    async def _download(
        self, creator: Creator, work: Work, task: CrawlerTask | None, now: datetime
    ) -> None:
        if task is None:
            task = CrawlerTask(CrawlerTaskKind.DOWNLOAD, creator.batch_id, creator.id, {}, work.id)
            self._tasks.add(task)
        else:
            task.republish(now)
        message = DownloadVideo(
            task_id=task.id,
            run=task.run,
            batch_id=creator.batch_id,
            creator=_creator_ref(creator),
            work=WorkRef(work_id=work.platform_id, url=work.url),
        )
        await self._publish(task, DOWNLOAD_VIDEO, message)
        work.settle(WorkStatus.DOWNLOADING, now)

    async def _analyze(self, creator: Creator, work: Work, material: Material, now: datetime):
        if work.material_id != material.id:
            await self._discard_material(work, now)
            work.material_id = material.id
        job = await self._jobs.video_job(work.id)
        if job is None:
            job = AnalysisJob(JobKind.VIDEO, creator.batch_id, creator.id, {}, work.id)
            self._jobs.add(job)
        else:
            job.resubmit(now)
        message = AnalyzeVideo(
            job_id=job.id,
            run=job.run,
            batch_id=creator.batch_id,
            creator_id=creator.id,
            material=MaterialRef(id=material.id, size=material.size, sha256=material.sha256),
            description=work.description,
            topics=work.topics,
            allow_reuse=job.run == 1,
        )
        await self._publish(job, ANALYZE_VIDEO, message)
        work.tags = None
        work.settle(WorkStatus.ANALYZING, now)

    async def _settle(self, creator: Creator, now: datetime, *, resummarize: bool = False):
        """按视频的结果推进达人：都有结果后汇总；全部失败需要重新抓取。

        汇总用到的视频结果有变化时（补跑、重新分析），重新汇总。
        """
        videos = [w for w in await self._works.of_creator(creator.id) if w.kind == WorkKind.VIDEO]
        if not videos:
            return
        if any(work.status not in WORK_FINAL for work in videos):
            if creator.status != CreatorStatus.ANALYZING:
                creator.settle(CreatorStatus.ANALYZING, now)
            return
        completed = [work for work in videos if work.status == WorkStatus.COMPLETED]
        if not completed:
            creator.settle(CreatorStatus.RECRAWL, now, "视频全部失败，需要重新抓取")
            return
        jobs = await self._jobs.of_works([work.id for work in completed])
        sources = [
            {"job_id": str(jobs[work.id].id), "run": jobs[work.id].run} for work in completed
        ]
        summary = await self._jobs.summary(creator.id)
        if summary is not None and summary.sources == sources and not resummarize:
            _follow_summary(creator, summary, now)
            return
        if summary is None:
            summary = AnalysisJob(
                JobKind.SUMMARY, creator.batch_id, creator.id, {}, sources=sources
            )
            self._jobs.add(summary)
        else:
            summary.resubmit(now, sources)
        message = SummarizeCreator(
            job_id=summary.id,
            run=summary.run,
            batch_id=creator.batch_id,
            creator_id=creator.id,
            videos=[
                SourceVideo(
                    job_id=jobs[work.id].id,
                    run=jobs[work.id].run,
                    recognition=jobs[work.id].result["recognition"],
                )
                for work in completed
            ],
        )
        await self._publish(summary, SUMMARIZE_CREATOR, message)
        creator.settle(CreatorStatus.SUMMARIZING, now)

    async def _publish(
        self, target: CrawlerTask | AnalysisJob, route: Route, message: BaseModel
    ) -> None:
        target.message = message.model_dump(mode="json")
        await self._outbox.put(route, target.message)

    async def _discard_material(self, work: Work, now: datetime) -> None:
        material = work.material_id and await self._materials.get(Material, work.material_id)
        if material:
            material.discard(now)


def _follow_summary(creator: Creator, summary: AnalysisJob, now: datetime) -> None:
    match summary.status:
        case JobStatus.COMPLETED if creator.status != CreatorStatus.COMPLETED:
            creator.complete(summary.result["tags"], now)
        case JobStatus.FAILED | JobStatus.NEEDS_REVIEW:
            creator.settle(CreatorStatus.SUMMARIZING, now, f"汇总失败：{summary.error}")
        case JobStatus.SUBMITTED | JobStatus.ACCEPTED | JobStatus.PROCESSING:
            if creator.status != CreatorStatus.SUMMARIZING:
                creator.settle(CreatorStatus.SUMMARIZING, now)


def _job_error(job: AnalysisJob) -> str | None:
    match job.status:
        case JobStatus.FAILED:
            return f"分析失败：{job.error}"
        case JobStatus.NEEDS_REVIEW | JobStatus.ABANDONED:
            return f"结果不明：{job.error}"
    return None


def _creator_ref(creator: Creator) -> CreatorRef:
    return CreatorRef(handle=creator.handle, oecuid=creator.oecuid)

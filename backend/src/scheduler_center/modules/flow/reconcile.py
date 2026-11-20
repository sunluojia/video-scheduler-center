"""对账：已发布却长时间没有受理的任务可能在队列中丢失，原样重发；执行程序按任务 ID 和轮次去重。"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from scheduler_center.core.common.clock import utcnow
from scheduler_center.core.messaging.outbox import Outbox
from scheduler_center.modules.analysis.messages import ANALYZE_VIDEO, SUMMARIZE_CREATOR
from scheduler_center.modules.analysis.models import JobKind
from scheduler_center.modules.analysis.repository import AnalysisJobRepository
from scheduler_center.modules.crawl.messages import CRAWL_CREATOR, DOWNLOAD_VIDEO
from scheduler_center.modules.crawl.models import CrawlerTaskKind
from scheduler_center.modules.crawl.repository import CrawlerTaskRepository

BATCH = 500


async def republish_crawler_tasks(session: AsyncSession, before: datetime) -> int:
    outbox, now = Outbox(session), utcnow()
    tasks = await CrawlerTaskRepository(session).unaccepted(before, BATCH)
    for task in tasks:
        route = CRAWL_CREATOR if task.kind == CrawlerTaskKind.CRAWL else DOWNLOAD_VIDEO
        await outbox.put(route, task.message)
        task.published_at = now
    return len(tasks)


async def republish_analysis_jobs(session: AsyncSession, before: datetime) -> int:
    outbox, now = Outbox(session), utcnow()
    jobs = await AnalysisJobRepository(session).unaccepted(before, BATCH)
    for job in jobs:
        route = ANALYZE_VIDEO if job.kind == JobKind.VIDEO else SUMMARIZE_CREATOR
        await outbox.put(route, job.message)
        job.submitted_at = now
    return len(jobs)

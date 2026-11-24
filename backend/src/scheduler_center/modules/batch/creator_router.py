from uuid import UUID

from fastapi import APIRouter, Response

from scheduler_center.core.common.errors import NotFound
from scheduler_center.core.web.dependencies import DatabaseDep, LegacyCrawlerDispatchDep
from scheduler_center.modules.analysis.attempts import AnalysisAttempts
from scheduler_center.modules.analysis.repository import AnalysisJobRepository
from scheduler_center.modules.batch.models import Batch, Creator
from scheduler_center.modules.batch.repository import (
    BatchRepository,
    CreatorRepository,
    WorkRepository,
)
from scheduler_center.modules.batch.schemas import CreatorDetail, CreatorView
from scheduler_center.modules.crawl.attempts import CrawlerAttempts
from scheduler_center.modules.crawl.repository import CrawlerTaskRepository
from scheduler_center.modules.flow.history import EventLog
from scheduler_center.modules.flow.service import Flow

router = APIRouter(prefix="/creators", tags=["达人"])


@router.get("/{creator_id}")
async def get_creator(creator_id: UUID, database: DatabaseDep) -> CreatorDetail:
    async with database.transaction() as session:
        creator = await CreatorRepository(session).require(Creator, creator_id)
        crawl = await CrawlerTaskRepository(session).crawl_task(creator.id)
        summary = await AnalysisJobRepository(session).summary(creator.id)
        return CreatorDetail(
            creator=creator,
            batch=await BatchRepository(session).require(Batch, creator.batch_id),
            crawl=crawl,
            works=await WorkRepository(session).of_creator(creator.id),
            summary=summary,
            crawl_attempts=await CrawlerAttempts(session).of_tasks([crawl.id]) if crawl else [],
            summary_attempts=await AnalysisAttempts(session).of_jobs([summary.id])
            if summary
            else [],
            history=await EventLog(session).of_tasks([crawl.id]) if crawl else [],
        )


@router.get("/{creator_id}/qrcode")
async def qrcode(creator_id: UUID, database: DatabaseDep) -> Response:
    async with database.transaction() as session:
        image = await CreatorRepository(session).qr(creator_id)
    if image is None:
        raise NotFound("没有二维码图片")
    content, media_type = image
    return Response(content, media_type=media_type)


@router.post("/{creator_id}/recrawl")
async def recrawl(
    creator_id: UUID, database: DatabaseDep, legacy_crawler_dispatch: LegacyCrawlerDispatchDep
) -> CreatorView:
    async with database.transaction() as session:
        flow = Flow(session, legacy_crawler_dispatch=legacy_crawler_dispatch)
        return CreatorView.model_validate(await flow.recrawl(creator_id))


@router.post("/{creator_id}/resummarize")
async def resummarize(creator_id: UUID, database: DatabaseDep) -> CreatorView:
    async with database.transaction() as session:
        return CreatorView.model_validate(await Flow(session).resummarize(creator_id))

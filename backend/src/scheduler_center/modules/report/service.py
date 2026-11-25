from collections import defaultdict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from scheduler_center.modules.batch.models import Work
from scheduler_center.modules.batch.repository import CreatorRepository, WorkRepository
from scheduler_center.modules.crawl.repository import CrawlerTaskRepository
from scheduler_center.modules.report.exporter import CreatorResult


async def load_creator_results(session: AsyncSession, batch_id: UUID) -> list[CreatorResult]:
    creators = CreatorRepository(session)
    members = await creators.of_batch(batch_id)
    works = await WorkRepository(session).of_batch(batch_id)
    qr_codes = await creators.qr_of_batch(batch_id)
    crawl_finished_at = await CrawlerTaskRepository(session).latest_crawl_finished_at(batch_id)
    works_of: dict[UUID, list[Work]] = defaultdict(list)
    for work in works:
        works_of[work.creator_id].append(work)
    return [
        CreatorResult(
            creator,
            works_of[creator.id],
            qr_codes.get(creator.id),
            crawl_finished_at=crawl_finished_at.get(creator.id),
        )
        for creator in members
    ]

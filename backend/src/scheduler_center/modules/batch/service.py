from sqlalchemy.ext.asyncio import AsyncSession

from scheduler_center.modules.batch.models import Batch, Creator
from scheduler_center.modules.batch.repository import CreatorRepository
from scheduler_center.modules.report.importer import ImportRow


async def create_batch(session: AsyncSession, name: str, source: str, rows: list[ImportRow]):
    """按名单顺序建立批次和达人；同一账号重复出现时各行都保留。"""
    batch = Batch(name, source)
    session.add(batch)
    creators = CreatorRepository(session)
    qr_codes = []
    for position, row in enumerate(rows, 1):
        creator = Creator(batch.id, position, row.row, row.name, row.handle, row.oecuid)
        creators.add(creator)
        if row.qr:
            qr_codes.append(
                {
                    "creator_id": creator.id,
                    "content": row.qr.content,
                    "media_type": row.qr.media_type,
                }
            )
    await session.flush()
    await creators.add_qr_codes(qr_codes)
    return batch

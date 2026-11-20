from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update

from scheduler_center.core.database.repository import Repository
from scheduler_center.modules.batch.tables import works
from scheduler_center.modules.material.tables import materials


class MaterialRepository(Repository):
    async def live_ids(self) -> set[str]:
        stmt = select(materials.c.id).where(materials.c.discarded_at.is_(None))
        return {str(material_id) for material_id in await self._session.scalars(stmt)}

    async def discard_for_works(self, work_ids: Sequence[UUID], now: datetime) -> None:
        await self._session.execute(
            update(materials)
            .where(materials.c.work_id.in_(work_ids), materials.c.discarded_at.is_(None))
            .values(discarded_at=now)
        )

    async def discard_for_batch(self, batch_id: UUID, now: datetime) -> None:
        batch_works = select(works.c.id).where(works.c.batch_id == batch_id)
        await self._session.execute(
            update(materials)
            .where(materials.c.work_id.in_(batch_works), materials.c.discarded_at.is_(None))
            .values(discarded_at=now)
        )

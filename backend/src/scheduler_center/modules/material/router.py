from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import FileResponse

from scheduler_center.core.common.errors import NotFound
from scheduler_center.core.web.dependencies import DatabaseDep
from scheduler_center.modules.material.dependencies import StoreDep
from scheduler_center.modules.material.models import Material
from scheduler_center.modules.material.repository import MaterialRepository

router = APIRouter(tags=["素材"])


@router.get("/materials/{material_id}")
async def download(material_id: UUID, database: DatabaseDep, store: StoreDep) -> FileResponse:
    """分析服务下载原视频；支持 Range 断点续传。"""
    async with database.transaction() as session:
        material = await MaterialRepository(session).get(Material, material_id)
    path = store.path(material_id)
    if material is None or material.discarded_at is not None or not path.is_file():
        raise NotFound("原视频不存在或已清理")
    return FileResponse(path, media_type="video/mp4")

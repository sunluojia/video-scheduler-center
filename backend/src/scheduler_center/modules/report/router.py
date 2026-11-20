from typing import Annotated

import anyio
from fastapi import APIRouter, Form, UploadFile
from pydantic import BaseModel

from scheduler_center.modules.report.importer import read_roster

router = APIRouter(prefix="/imports", tags=["名单导入"])


class PreviewRow(BaseModel):
    row: int | None
    name: str
    handle: str | None
    oecuid: str | None
    has_qr: bool


class Preview(BaseModel):
    source: str
    rows: list[PreviewRow]
    invalid: int
    """无法识别为账号的行数。"""


@router.post("/preview")
async def preview(
    file: UploadFile | None = None, links: Annotated[str | None, Form()] = None
) -> Preview:
    """解析名单供确认，不保存。"""
    content = await file.read() if file else None
    rows, source = await anyio.to_thread.run_sync(
        read_roster, file and file.filename, content, links
    )
    return Preview(
        source=source,
        rows=[
            PreviewRow(
                row=row.row,
                name=row.name,
                handle=row.handle,
                oecuid=row.oecuid,
                has_qr=row.qr is not None,
            )
            for row in rows
        ],
        invalid=sum(row.handle is None for row in rows),
    )

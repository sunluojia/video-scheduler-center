from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from scheduler_center.modules.batch.models import (
    WORK_FINAL,
    Batch,
    Creator,
    CreatorStatus,
    Work,
    WorkKind,
    WorkStatus,
)
from scheduler_center.modules.batch.repository import CreatorRepository
from scheduler_center.modules.report.importer import ImportRow

LOGIN_REQUIRED_NOTE = "需要登录"
PLAYBACK_RESTRICTIONS = frozenset(
    {"挂车/受限视频（服务端未返回播放地址）", "受限视频（服务端未返回播放地址）"}
)
"""爬虫通过 download_failed 上报的确切原因。普通取链失败可能没有媒体地址，但要单独处理。"""


async def create_batch(
    session: AsyncSession,
    name: str,
    source: str,
    rows: list[ImportRow],
    *,
    ai_batch_mode: bool = False,
) -> Batch:
    """按名单顺序建立批次和达人；同一账号重复出现时各行都保留。"""
    batch = Batch(name, source, ai_batch_mode=ai_batch_mode)
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


def restricted_video_outcome(works: Sequence[Work]) -> tuple[CreatorStatus, str | None] | None:
    """视频均已结束、没有识别成功的视频时，按受限视频和图文的组合结算。"""
    videos = [work for work in works if work.kind == WorkKind.VIDEO]
    if any(work.status not in WORK_FINAL or work.status == WorkStatus.COMPLETED for work in videos):
        return None
    restricted = sum(
        work.status == WorkStatus.FAILED
        and (work.error or "").removeprefix("下载失败：") in PLAYBACK_RESTRICTIONS
        for work in videos
    )
    if not restricted:
        return None
    if any(work.kind == WorkKind.PHOTO for work in works):
        return CreatorStatus.COMPLETED, None
    if restricted == len(videos):
        note = "最新视频均为挂车或受限视频（服务端未返回播放地址），需要重新抓取"
    else:
        note = "视频全部失败（含挂车或受限视频），需要重新抓取"
    return CreatorStatus.RECRAWL, note

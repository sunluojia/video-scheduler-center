import asyncio
from collections.abc import AsyncIterable

from fastapi import APIRouter, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from scheduler_center.modules.live.hub import LiveHub

router = APIRouter(tags=["实时更新"])


@router.get("/live", response_class=EventSourceResponse)
async def live(request: Request) -> AsyncIterable[ServerSentEvent]:
    """数据变化时推送发生变化的批次 ID；"*" 表示可能有遗漏，页面应整体刷新。"""
    hub: LiveHub = request.app.state.live
    async with hub.subscribe() as subscription:
        while True:
            yield ServerSentEvent(event="changed", data=await subscription.next())
            await asyncio.sleep(0.5)  # 合并短时间内的连续变化

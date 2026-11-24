"""接收爬取程序和分析服务回传的事件：消息参数是一个 JSON 对象。

格式错误的事件直接进入死信队列；处理失败的按 Dramatiq 的退避策略重试，按事件 ID 去重。
"""

import dramatiq
from dramatiq import Broker
from pydantic import ValidationError

from scheduler_center.core.database.engine import Database
from scheduler_center.modules.analysis.messages import ANALYSIS_EVENTS, analysis_event
from scheduler_center.modules.crawl.messages import CRAWLER_EVENTS, crawler_event
from scheduler_center.modules.flow.service import Flow


def declare_actors(
    broker: Broker, database: Database, *, legacy_crawler_dispatch: bool = False
) -> None:
    async def on_crawler_event(payload: dict) -> None:
        event = crawler_event.validate_python(payload)
        async with database.transaction() as session:
            flow = Flow(session, legacy_crawler_dispatch=legacy_crawler_dispatch)
            await flow.on_crawler_event(event)

    async def on_analysis_event(payload: dict) -> None:
        event = analysis_event.validate_python(payload)
        async with database.transaction() as session:
            await Flow(session).on_analysis_event(event)

    for handler, route in (
        (on_crawler_event, CRAWLER_EVENTS),
        (on_analysis_event, ANALYSIS_EVENTS),
    ):
        dramatiq.actor(
            broker=broker,
            actor_name=route.actor,
            queue_name=route.queue,
            throws=(ValidationError,),
        )(handler)

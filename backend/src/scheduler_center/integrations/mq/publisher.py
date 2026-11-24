import asyncio

from dramatiq import Broker, Message
from sqlalchemy import Row

from scheduler_center.core.common.clock import utcnow
from scheduler_center.core.database.engine import Database
from scheduler_center.core.messaging.outbox import Outbox
from scheduler_center.modules.analysis.messages import ANALYZE_VIDEO
from scheduler_center.modules.crawl.messages import CRAWL_CREATOR

BATCH = 200


class OutboxPublisher:
    """把发件箱中的任务投递到执行程序的队列。

    写入 Redis 成功后才标记已发布，至少投递一次；执行程序按任务 ID 和轮次去重。
    """

    def __init__(
        self, database: Database, broker: Broker, *, legacy_crawler_dispatch: bool = False
    ) -> None:
        broker.declare_queue(ANALYZE_VIDEO.queue)
        if legacy_crawler_dispatch:
            broker.declare_queue(CRAWL_CREATOR.queue)
        self._database = database
        self._broker = broker
        self._exclude_queue = None if legacy_crawler_dispatch else CRAWL_CREATOR.queue

    async def publish_pending(self) -> int:
        """投递一批，返回条数；满一批说明还有积压。"""
        async with self._database.transaction() as session:
            outbox = Outbox(session)
            pending = await outbox.pending(limit=BATCH, exclude_queue=self._exclude_queue)
            if pending:
                await asyncio.to_thread(self._send, pending)
                await outbox.mark_published([row.id for row in pending], utcnow())
        return len(pending)

    def _send(self, rows: list[Row]) -> None:
        for row in rows:
            message = Message(
                queue_name=row.queue,
                actor_name=row.actor,
                args=(row.payload,),
                kwargs={},
                options={},
            )
            self._broker.enqueue(message)

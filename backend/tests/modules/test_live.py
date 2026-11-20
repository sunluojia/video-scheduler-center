"""页面实时更新：数据库通知经监听转给页面连接。"""

import asyncio
from uuid import uuid4

from scheduler_center.modules.live.hub import EVERYTHING, LiveHub
from scheduler_center.modules.live.notifier import Live
from tests.factories import new_batch


async def test_changes_reach_subscribers_after_commit(client, database, database_url):
    batch = await new_batch(database, "alice", start=False)
    hub = LiveHub(database_url)
    listening = asyncio.create_task(hub.run())
    try:
        async with hub.subscribe() as subscription:
            # 连上后先整体刷新一次
            assert await asyncio.wait_for(subscription.next(), 5) == [EVERYTHING]
            batch_id = uuid4()
            async with database.transaction() as session:
                await Live(session).changed(batch_id)
                await Live(session).changed(batch_id)

            assert await asyncio.wait_for(subscription.next(), 5) == [str(batch_id)]

            path = f"/api/batches/{batch.id}"
            assert (await client.delete(path)).status_code == 204
            assert await asyncio.wait_for(subscription.next(), 5) == [str(batch.id)]
            assert (await client.get(path)).status_code == 404
    finally:
        listening.cancel()
        await asyncio.gather(listening, return_exceptions=True)

"""队列：发件箱投递到执行程序的任务队列，事件队列交给业务推进处理。"""

import asyncio
from uuid import UUID, uuid4

import dramatiq
import pytest
from dramatiq import Message
from dramatiq.asyncio import get_event_loop_thread
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware import AsyncIO

from scheduler_center.core.database.engine import Database
from scheduler_center.integrations.mq.consumer import declare_actors
from scheduler_center.integrations.mq.publisher import OutboxPublisher
from scheduler_center.modules.crawl.models import CrawlerTask, CrawlerTaskStatus
from scheduler_center.modules.flow.history import events
from tests.factories import at, load, messages, new_batch


@pytest.fixture
async def broker(database_url, database):
    """处理器运行在 Dramatiq 自己的事件循环里，使用单独的连接池。"""
    broker = StubBroker()
    broker.add_middleware(AsyncIO())
    actors_database = Database(database_url)
    declare_actors(broker, actors_database)
    worker = dramatiq.Worker(broker, worker_timeout=100)
    worker.start()
    yield broker
    loop_thread = get_event_loop_thread()
    assert loop_thread is not None
    loop_thread.run_coroutine(actors_database.dispose())
    worker.stop()


def deliver(broker: StubBroker, queue: str, actor: str, payload: dict) -> None:
    broker.enqueue(Message(queue, actor, (payload,), {}, {}))
    broker.join(queue, timeout=10_000, fail_fast=False)


async def test_outbox_is_published_to_the_crawler_queue(database):
    broker = StubBroker()
    await new_batch(database, "alice", "bob")
    publisher = OutboxPublisher(database, broker)

    assert await publisher.publish_pending() == 2
    assert await publisher.publish_pending() == 0

    queued = [Message.decode(data) for data in broker.queues["crawler.tasks"].queue]
    assert [(m.actor_name, m.args[0]["creator"]["handle"]) for m in queued] == [
        ("crawl_creator", "alice"),
        ("crawl_creator", "bob"),
    ]


async def test_crawler_events_drive_the_flow(broker, database):
    await new_batch(database, "alice")
    [task] = await messages(database, "crawl_creator")
    event = {
        "event_id": str(uuid4()),
        "task_id": task["task_id"],
        "run": 1,
        "type": "accepted",
        "occurred_at": at(),
    }

    await asyncio.to_thread(deliver, broker, "crawler.events", "crawler_event", event)

    crawl = await load(database, CrawlerTask, UUID(task["task_id"]))
    assert crawl.status == CrawlerTaskStatus.ACCEPTED
    assert broker.dead_letters == []


async def test_malformed_events_go_straight_to_dead_letters(broker, database):
    await asyncio.to_thread(
        deliver, broker, "analysis.events", "analysis_event", {"type": "job", "job_id": "x"}
    )

    [dead] = broker.dead_letters
    assert dead.actor_name == "analysis_event"
    async with database.engine.connect() as connection:
        assert (await connection.execute(events.select())).all() == []

"""Worker 的定时工作：批次完成、对账重发、清理原视频文件。"""

import os
import time
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update

from scheduler_center.core.common.clock import utcnow
from scheduler_center.modules.batch.models import Batch, BatchStatus
from scheduler_center.modules.crawl.tables import crawler_tasks
from scheduler_center.modules.executor.models import ExecutorKind
from tests.factories import (
    CRAWLER_URL,
    FakeExecutors,
    build_worker,
    crawl,
    creators,
    load,
    messages,
    new_batch,
    photo,
    register,
)


def worker(database, database_url, tmp_path):
    return build_worker(database, database_url, tmp_path, FakeExecutors())


async def test_batch_completes_when_every_creator_is_concluded(database, database_url, tmp_path):
    batch = await new_batch(database, "alice")
    jobs = worker(database, database_url, tmp_path)

    await jobs.complete_batches()
    assert (await load(database, Batch, batch.id)).status == BatchStatus.RUNNING

    [alice] = await creators(database, batch)
    await crawl(database, alice, photo(1))
    await jobs.complete_batches()

    assert (await load(database, Batch, batch.id)).status == BatchStatus.COMPLETED


async def test_unaccepted_tasks_are_republished_only_while_the_executor_is_online(
    database, database_url, tmp_path
):
    await new_batch(database, "alice")
    async with database.transaction() as session:
        await session.execute(
            update(crawler_tasks).values(published_at=utcnow() - timedelta(hours=1))
        )
    jobs = worker(database, database_url, tmp_path)

    await jobs.reconcile()
    assert len(await messages(database, "crawl_creator")) == 1

    await register(database, ExecutorKind.CRAWLER, CRAWLER_URL)
    await jobs.reconcile()

    first, again = await messages(database, "crawl_creator")
    assert first == again
    await jobs.reconcile()  # 刚重发过，不再重复
    assert len(await messages(database, "crawl_creator")) == 2


@pytest.mark.parametrize("enabled", [True, False])
async def test_clean_up_respects_the_worker_setting(
    database, database_url, tmp_path, monkeypatch, enabled
):
    monkeypatch.setenv("SC_CLEANUP_ENABLED", str(enabled))
    root = tmp_path / "materials"
    (root / "incoming").mkdir(parents=True)
    old = time.time() - 3600
    stale, fresh, partial = root / str(uuid4()), root / str(uuid4()), root / "incoming" / "x.part"
    for path in (stale, fresh, partial):
        path.write_bytes(b"video")
    for path in (stale, partial):
        os.utime(path, (old, old))

    await worker(database, database_url, tmp_path).clean_up()

    assert [path.exists() for path in (stale, fresh, partial)] == [not enabled, True, not enabled]
    assert UUID(fresh.name)  # 刚写入、可能还没登记的文件保留

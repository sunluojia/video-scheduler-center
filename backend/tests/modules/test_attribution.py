import asyncio
import csv
import io
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from dramatiq.brokers.stub import StubBroker
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from scheduler_center.core.common.clock import utcnow
from scheduler_center.integrations.mq.publisher import OutboxPublisher
from scheduler_center.modules.batch.models import WorkStatus
from scheduler_center.modules.batch.tables import works as work_table
from scheduler_center.modules.crawl.attempts import CrawlerAttempts
from scheduler_center.modules.crawl.models import (
    CrawlerAttemptStatus,
    CrawlerRunKind,
    CrawlerRunStatus,
    CrawlerTask,
    CrawlerTaskAttempt,
    CrawlerTaskRun,
)
from scheduler_center.modules.crawl.tables import crawler_task_attempts
from scheduler_center.modules.flow.history import events
from scheduler_center.modules.material.tables import materials
from scheduler_center.modules.proxy.models import Lease, Use
from tests.factories import (
    analysis_says,
    build_worker,
    content,
    crawler_says,
    creators,
    job_event,
    load,
    messages,
    new_batch,
    recognized,
    video,
    works,
)


@pytest.fixture
def legacy_crawler_dispatch() -> bool:
    return False


def owner(device: str) -> dict:
    return {"executor_id": device, "executor_session_id": str(uuid4())}


async def claim(service, identity: dict, *, crawl=0, download=0) -> list[dict]:
    response = await service.post(
        "/api/crawler-tasks/claim",
        json={
            **identity,
            "kinds": {"crawl_creator": crawl, "download_video": download},
        },
    )
    return response.raise_for_status().json()["tasks"]


async def heartbeat(service, identity: dict, *attempt_ids: str) -> dict:
    response = await service.put(
        f"/api/crawler-services/{identity['executor_id']}",
        json={
            "url": "http://crawler.test",
            "version": "2.0",
            "executor_session_id": identity["executor_session_id"],
            "task_attempts": attempt_ids,
        },
    )
    return response.raise_for_status().json()


def upload_headers(data: bytes, digest_header: dict, identity: dict, attempt: dict) -> dict:
    return {
        **digest_header,
        "Content-Length": str(len(data)),
        "X-Attempt-ID": attempt["attempt_id"],
        "X-Executor-ID": identity["executor_id"],
        "X-Executor-Session-ID": identity["executor_session_id"],
    }


def upload_url(attempt: dict) -> str:
    return f"/api/download-tasks/{attempt['task_id']}/runs/{attempt['run']}/video"


async def crawler_result(database, attempt: dict, identity: dict, type_: str, **fields) -> None:
    await crawler_says(
        database,
        attempt["task_id"],
        type_,
        attempt["run"],
        legacy_crawler_dispatch=False,
        attempt_id=attempt["attempt_id"],
        **identity,
        **fields,
    )


async def expire_lease(database, attempt: dict) -> None:
    async with database.transaction() as session:
        await session.execute(
            update(crawler_task_attempts)
            .where(crawler_task_attempts.c.attempt_id == UUID(attempt["attempt_id"]))
            .values(lease_expires_at=utcnow() - timedelta(seconds=1))
        )


async def new_download(database, service, identity: dict):
    batch = await new_batch(database, "alice", legacy_crawler_dispatch=False)
    [crawl] = await claim(service, identity, crawl=1)
    await crawler_result(database, crawl, identity, "crawled", conclusion="ok", works=[video(1)])
    [creator] = await creators(database, batch)
    [work] = await works(database, creator)
    return batch, creator, work


async def test_two_devices_skip_locked_rows_and_claim_distinct_tasks(database, client, service):
    body = {**owner("device-a"), "kinds": {"crawl_creator": 1}}
    assert (await client.post("/api/crawler-tasks/claim", json=body)).status_code == 401
    response = await client.post(
        "/api/batches",
        data={
            "name": "并发批次",
            "links": "alice\nbob\ncharlie",
            "start": "true",
        },
    )
    assert response.status_code == 201
    assert await messages(database, "crawl_creator") == []

    a, b = owner("device-a"), owner("device-b")
    async with database.transaction() as session:
        [(first, first_attempt)] = await CrawlerAttempts(session).claim(
            a["executor_id"], UUID(a["executor_session_id"]), {CrawlerRunKind.CRAWL: 1}
        )
        await session.flush()  # A 持有锁且未提交，B 必须跳过该任务行
        other = await asyncio.wait_for(claim(service, b, crawl=3), timeout=5)
        assert len(other) == 2
        assert str(first.task_id) not in {task["task_id"] for task in other}
        assert all(task["body"]["task_id"] == task["task_id"] for task in other)
    assert await claim(service, a, crawl=1) == []
    task = await load(database, CrawlerTask, first.task_id)
    assert task.current_attempt_id == first_attempt.attempt_id
    assert task.last_started_executor_id == "device-a" and task.accepted_at is not None
    assert first_attempt.lease_expires_at - first_attempt.claimed_at == timedelta(seconds=90)
    # 数据库约束保护同一轮次，即使有写入绕过正常认领路径。
    with pytest.raises(IntegrityError, match="uq_crawler_task_attempts_active"):
        async with database.transaction() as session:
            await session.execute(
                insert(crawler_task_attempts).values(
                    attempt_id=uuid4(),
                    task_id=first.task_id,
                    run=first.run,
                    attempt_no=2,
                    executor_id=b["executor_id"],
                    executor_session_id=UUID(b["executor_session_id"]),
                    status=CrawlerAttemptStatus.LEASED,
                    claimed_at=first_attempt.claimed_at,
                    lease_expires_at=first_attempt.lease_expires_at,
                )
            )


async def test_pause_only_blocks_crawl_and_cancel_revokes_attempts(database, client, service):
    a = owner("device-a")
    batch = await new_batch(database, "alice", "bob", legacy_crawler_dispatch=False)
    [crawl] = await claim(service, a, crawl=1)
    await crawler_result(database, crawl, a, "crawled", conclusion="ok", works=[video(1)])
    assert (await client.post(f"/api/batches/{batch.id}/pause")).status_code == 200
    [download] = await claim(service, a, crawl=5, download=5)
    assert download["kind"] == "download_video"
    assert download["body"]["work"]["work_id"] == video(1)["work_id"]

    await crawler_result(database, download, a, "accepted")
    key = UUID(download["attempt_id"])
    assert (await load(database, CrawlerTaskAttempt, key)).status == CrawlerAttemptStatus.RUNNING
    assert (await client.post(f"/api/batches/{batch.id}/cancel")).status_code == 200
    cancelled = await load(database, CrawlerTaskAttempt, key)
    assert cancelled.status == CrawlerAttemptStatus.CANCELLED and cancelled.ended_at
    assert (await heartbeat(service, a, download["attempt_id"]))["revoked_attempts"] == [str(key)]
    assert await claim(service, a, crawl=5, download=5) == []


async def test_deferred_attempts_keep_retry_budget_by_run(database, service):
    a, b = owner("device-a"), owner("device-b")
    await new_batch(database, "alice", legacy_crawler_dispatch=False)
    [first] = await claim(service, a, crawl=1)
    path = f"/api/crawler-tasks/attempts/{first['attempt_id']}/defer"
    data = {"retry_after": 20, "reason": "暂无线路"}
    assert (await service.post(path, json={**b, **data})).status_code == 409
    assert (await service.post(path, json={**a, **data})).status_code == 204
    assert (await service.post(path, json={**a, **data})).status_code == 409
    assert await claim(service, b, crawl=1) == []
    key = (UUID(first["task_id"]), 1)
    queued = await load(database, CrawlerTaskRun, key)
    assert queued.status == CrawlerRunStatus.QUEUED and queued.current_attempt_id is None
    assert queued.next_eligible_at - queued.updated_at == timedelta(seconds=20)
    assert queued.attempts == 0 and queued.history == ["暂无线路"]
    async with database.transaction() as session:
        run = await session.get(CrawlerTaskRun, key)
        run.next_eligible_at = utcnow()

    [second] = await claim(service, b, crawl=1)
    assert second["attempt_no"] == 2 and second["attempts"] == 0
    path = f"/api/crawler-tasks/attempts/{second['attempt_id']}/defer"
    assert (
        await service.post(
            path,
            json={
                **b,
                "retry_after": 0,
                "reason": "线路超时",
                "consume_attempt": True,
                "avoid_line": "line-1",
            },
        )
    ).status_code == 204
    [third] = await claim(service, a, crawl=1)
    assert (third["attempt_no"], third["attempts"], third["avoid"], third["history"]) == (
        3,
        1,
        ["line-1"],
        ["暂无线路", "第 1 次：线路超时"],
    )

    await crawler_result(database, first, a, "crawled", conclusion="private")
    await crawler_result(database, third, a, "crawled", conclusion="ok", works=[])
    task = await load(database, CrawlerTask, key[0])
    assert task.result_executor_id == "device-a" and task.conclusion == "ok"
    assert task.current_attempt_id is None


async def test_heartbeat_expiry_takeover_and_late_upload(
    database, database_url, tmp_path, executors, client, service
):
    a, b = owner("device-a"), owner("device-b")
    batch, creator, work = await new_download(database, service, a)
    [first] = await claim(service, a, download=1)
    key = UUID(first["attempt_id"])
    async with database.transaction() as session:
        attempt = await session.get(CrawlerTaskAttempt, key)
        attempt.lease_expires_at = utcnow() + timedelta(seconds=15)
        previous = attempt.lease_expires_at
    wrong_session = {**a, "executor_session_id": str(uuid4())}
    assert (await heartbeat(service, wrong_session, str(key)))["revoked_attempts"] == [str(key)]
    assert (await load(database, CrawlerTaskAttempt, key)).lease_expires_at == previous
    assert (await heartbeat(service, a, str(key)))["revoked_attempts"] == []
    assert (await load(database, CrawlerTaskAttempt, key)).lease_expires_at > previous

    await expire_lease(database, first)
    assert (await heartbeat(service, a, str(key)))["revoked_attempts"] == [str(key)]
    worker = build_worker(
        database, database_url, tmp_path, executors, legacy_crawler_dispatch=False
    )
    await worker.reconcile()
    [second] = await claim(service, b, download=1)
    assert second["task_id"] == first["task_id"] and second["attempt_no"] == 2
    assert second["attempts"] == 0
    await heartbeat(service, b, second["attempt_id"])
    data, headers = content()

    async def must_not_read():
        pytest.fail("过期认领在收流前就应被拦截")
        yield data

    url = upload_url(first)
    old = upload_headers(data, headers, a, first)
    assert (await service.put(url, content=must_not_read(), headers=old)).status_code == 409
    assert (await service.put(url, content=data, headers=headers)).status_code == 409
    await crawler_result(database, first, a, "download_failed", reason="迟到失败")
    valid = upload_headers(data, headers, b, second)
    assert (await service.put(url, content=data, headers=valid)).status_code == 204
    assert (await service.put(url, content=data, headers=valid)).status_code == 204
    assert (await service.put(url, content=data, headers=old)).status_code == 409
    assert (
        await service.put(
            url, content=data, headers={**valid, "X-Executor-Session-ID": str(uuid4())}
        )
    ).status_code == 409

    detail = (await client.get(f"/api/works/{work.id}")).raise_for_status().json()
    assert detail["download"]["last_started_executor_id"] == "device-b"
    assert detail["download"]["result_executor_id"] == "device-b"
    assert [attempt["status"] for attempt in detail["download_attempts"]] == [
        "succeeded",
        "expired",
    ]
    assert {(event["payload"]["type"], event["executor_id"]) for event in detail["history"]} == {
        ("download_failed", "device-a"),
        ("uploaded", "device-b"),
    }
    async with database.transaction() as session:
        [material] = (await session.execute(select(materials))).all()
        assert material.source_attempt_id == UUID(second["attempt_id"])
        assert material.source_executor_id == "device-b"

    creator_detail = (await client.get(f"/api/creators/{creator.id}")).raise_for_status().json()
    assert creator_detail["crawl"]["result_executor_id"] == "device-a"
    assert len(creator_detail["crawl_attempts"]) == 1

    assert (await service.delete("/api/crawler-services/device-a")).status_code == 204
    listed = (await client.get("/api/executors")).raise_for_status().json()
    withdrawn = next(item for item in listed if item["instance_id"] == "device-a")
    assert withdrawn["withdrawn_at"] is not None
    activity = {item["instance_id"]: item["activity"] for item in listed}
    assert activity["device-a"] == {
        "active": 0,
        "completed_24h": 1,
        "expired_24h": 1,
        "takeovers_24h": 0,
    }
    assert activity["device-b"] == {
        "active": 0,
        "completed_24h": 1,
        "expired_24h": 0,
        "takeovers_24h": 1,
    }
    response = await client.get(f"/api/batches/{batch.id}/execution-attempts")
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert [(row["执行器"], row["阶段"], row["状态"]) for row in rows] == [
        ("device-a", "crawl_creator", "succeeded"),
        ("device-a", "download_video", "expired"),
        ("device-b", "download_video", "succeeded"),
    ]


async def test_upload_is_rechecked_after_receiving_content(database, service, tmp_path):
    a, b = owner("device-a"), owner("device-b")
    await new_download(database, service, a)
    [first] = await claim(service, a, download=1)
    data, digest = content()

    async def slow_upload():
        yield data[:2]
        await expire_lease(database, first)
        async with database.transaction() as session:
            assert await CrawlerAttempts(session).expire(utcnow()) == 1
        [second] = await claim(service, b, download=1)
        assert second["attempt_no"] == 2
        yield data[2:]

    response = await service.put(
        upload_url(first), content=slow_upload(), headers=upload_headers(data, digest, a, first)
    )
    assert response.status_code == 409
    async with database.transaction() as session:
        assert (await session.execute(select(materials))).all() == []
    assert list((tmp_path / "materials").glob("*")) == [tmp_path / "materials" / "incoming"]


async def test_heartbeat_only_renews_attempts_reported_by_the_session(database, service):
    a, b = owner("device-a"), owner("device-b")
    await new_batch(database, "alice", "bob", legacy_crawler_dispatch=False)
    [reported, lost_response] = await claim(service, a, crawl=2)
    ids = [UUID(attempt["attempt_id"]) for attempt in (reported, lost_response)]
    deadline = utcnow() + timedelta(seconds=5)
    async with database.transaction() as session:
        await session.execute(
            update(crawler_task_attempts)
            .where(crawler_task_attempts.c.attempt_id.in_(ids))
            .values(lease_expires_at=deadline)
        )
    assert (await heartbeat(service, a))["revoked_attempts"] == []
    assert (await load(database, CrawlerTaskAttempt, ids[0])).lease_expires_at == deadline
    assert (await heartbeat(service, a, reported["attempt_id"]))["revoked_attempts"] == []
    assert (await load(database, CrawlerTaskAttempt, ids[0])).lease_expires_at > deadline
    assert (await load(database, CrawlerTaskAttempt, ids[1])).lease_expires_at == deadline

    await expire_lease(database, lost_response)
    async with database.transaction() as session:
        assert await CrawlerAttempts(session).expire(utcnow()) == 1
    [takeover] = await claim(service, b, crawl=2)
    assert takeover["task_id"] == lost_response["task_id"] and takeover["attempt_no"] == 2


async def test_redownload_cancels_previous_attempt_and_tracks_proxy_lease(
    database, client, service
):
    a = owner("device-a")
    _, _, work = await new_download(database, service, a)
    [first] = await claim(service, a, download=1)
    response = await service.post(
        "/api/proxy-leases",
        json={
            "device": a["executor_id"],
            "use": Use.DOWNLOAD,
            "task_id": first["task_id"],
            "attempt_id": first["attempt_id"],
        },
    )
    lease = response.raise_for_status().json()["lease"]
    assigned = await load(database, Lease, UUID(lease["lease_id"]))
    assert assigned.attempt_id == UUID(first["attempt_id"])
    attempt = await load(database, CrawlerTaskAttempt, assigned.attempt_id)
    assert attempt.route_kind == lease["route"]["kind"]

    async with database.transaction() as session:
        await session.execute(
            update(work_table).where(work_table.c.id == work.id).values(status=WorkStatus.FAILED)
        )
    assert (await client.post(f"/api/works/{work.id}/redownload")).status_code == 200
    assert (await load(database, CrawlerTaskAttempt, assigned.attempt_id)).status == "cancelled"
    [second] = await claim(service, a, download=1)
    assert (second["task_id"], second["run"], second["attempt_no"]) == (first["task_id"], 2, 1)


async def test_analysis_attributes_stages_and_ignores_an_earlier_retry_generation(
    database, client, service
):
    crawler = owner("device-a")
    batch, creator, work = await new_download(database, service, crawler)
    [download] = await claim(service, crawler, download=1)
    data, digest = content()
    assert (
        await service.put(
            upload_url(download),
            content=data,
            headers=upload_headers(data, digest, crawler, download),
        )
    ).status_code == 204
    [job] = await messages(database, "analyze_video")
    job_id = job["job_id"]
    first, second = owner("analysis-a"), owner("analysis-b")
    old_attempt, stage_attempt, job_attempt, unrelated_attempt = (str(uuid4()) for _ in range(4))
    start = utcnow() - timedelta(seconds=10)

    def stage(identity, attempt, generation, status, seconds):
        return {
            "type": "stage",
            "job_id": job_id,
            "run": 1,
            "stage": "listen",
            "segment": 0,
            "attempt": 1,
            "status": status,
            "occurred_at": (start + timedelta(seconds=seconds)).isoformat(),
            "retry_generation": generation,
            "attempt_id": attempt,
            "model": "audio-model",
            **identity,
        }

    await analysis_says(database, stage(first, old_attempt, 0, "failed", 1))
    # 任务事件可先到，同 ID 的阶段明细到达后应取代它。
    await analysis_says(
        database,
        job_event(
            job_id,
            "processing",
            **second,
            attempt_id=stage_attempt,
            occurred_at=(start + timedelta(seconds=2)).isoformat(),
        ),
    )
    await analysis_says(
        database,
        job_event(
            job_id,
            "processing",
            **second,
            attempt_id=job_attempt,
            retry_generation=1,
            occurred_at=(start + timedelta(seconds=3)).isoformat(),
        ),
    )
    await analysis_says(database, stage(second, stage_attempt, 1, "succeeded", 4))
    await analysis_says(database, stage(second, stage_attempt, 1, "started", 2))
    # 其他阶段仍可使用自己的代次，乱序送达的阶段不会覆写最新执行器。
    await analysis_says(
        database,
        {
            **stage(first, unrelated_attempt, 0, "succeeded", 3),
            "stage": "extract_frames",
            "segment": None,
        },
    )
    before_result = (await client.get(f"/api/works/{work.id}")).raise_for_status().json()
    assert before_result["analysis"]["last_executor_id"] == "analysis-b"
    assert {item["stage"] for item in before_result["stages"]} == {"listen", "extract_frames"}
    finished = (start + timedelta(seconds=5)).isoformat()
    # 分析服务的结束事件沿用阶段尝试 ID，可省略补跑代次。
    await analysis_says(
        database,
        job_event(
            job_id,
            "completed",
            **second,
            attempt_id=stage_attempt,
            occurred_at=finished,
            result=recognized("口播"),
        ),
    )
    await analysis_says(
        database,
        job_event(
            job_id,
            "failed",
            **first,
            attempt_id=old_attempt,
            occurred_at=utcnow().isoformat(),
            error="旧代次错误",
        ),
    )

    result = (await client.get(f"/api/works/{work.id}")).raise_for_status().json()
    assert result["work"]["status"] == "completed"
    assert result["analysis"]["result_executor_id"] == "analysis-b"
    assert result["stages"][0]["result_executor_id"] == "analysis-b"
    attempts = {item["attempt_id"]: item for item in result["analysis_attempts"]}
    assert attempts[stage_attempt]["stage"] == "listen"
    assert attempts[stage_attempt]["status"] == "succeeded"
    assert attempts[stage_attempt]["retry_generation"] == 1
    assert datetime.fromisoformat(attempts[stage_attempt]["started_at"]) == start + timedelta(
        seconds=2
    )
    assert attempts[old_attempt]["status"] == "failed" and attempts[job_attempt]["stage"] == "job"
    assert (
        await service.put(
            "/api/analysis-services/analysis-b",
            json={
                "url": "http://analysis.test",
                "version": "2.0",
            },
        )
    ).status_code == 200
    listed = (await client.get("/api/executors")).raise_for_status().json()
    assert (
        next(item for item in listed if item["instance_id"] == "analysis-b")["activity"][
            "completed_24h"
        ]
        == 1
    )
    async with database.transaction() as session:
        logged = (
            await session.execute(select(events).where(events.c.attempt_id == UUID(job_attempt)))
        ).one()
        assert logged.executor_id == "analysis-b" and logged.executor_session_id == UUID(
            second["executor_session_id"]
        )
    response = await client.get(f"/api/batches/{batch.id}/execution-attempts")
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert any(row["阶段"] == "listen" and row["平台作品ID"] == work.platform_id for row in rows)

    [summary] = await messages(database, "summarize_creator")
    await analysis_says(
        database,
        job_event(summary["job_id"], "completed", kind="summary", result={"tags": ["口播"]}),
    )
    assert (await client.post(f"/api/creators/{creator.id}/recrawl")).status_code == 200
    assert (await client.get(f"/api/works/{work.id}")).status_code == 404
    archived = await client.get(f"/api/batches/{batch.id}/execution-attempts")
    archived_rows = list(csv.DictReader(io.StringIO(archived.content.decode("utf-8-sig"))))
    assert rows == archived_rows


async def test_migration_0016_backfills_published_and_accepted_tasks(
    database, database_url, service
):
    batch = await new_batch(database, "alice", "bob")
    [finished, published] = await messages(database, "crawl_creator")
    await crawler_says(database, finished["task_id"], "crawled", conclusion="ok", works=[video(1)])
    [accepted] = await messages(database, "download_video")
    await crawler_says(database, accepted["task_id"], "accepted")
    previous = await load(database, CrawlerTask, UUID(accepted["task_id"]))
    assert previous.accepted_at

    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    await database.dispose()
    await asyncio.to_thread(command.downgrade, config, "0015")
    try:
        await asyncio.to_thread(command.upgrade, config, "0016")
        publisher = OutboxPublisher(database, StubBroker())
        assert await publisher.publish_pending() == 0  # 历史的待发 Redis 消息不会重复投递
        claimed = await claim(service, owner("new-device"), crawl=5, download=5)
        assert [item["body"] for item in claimed] == [published, accepted]
        assert all(item["body"]["batch_id"] == str(batch.id) for item in claimed)
        task = await load(database, CrawlerTask, previous.id)
        assert task.accepted_at == previous.accepted_at
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")

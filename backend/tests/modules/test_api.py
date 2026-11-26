"""HTTP 接口：页面使用的批次接口、执行程序使用的注册、上传、下载接口，以及转发。"""

import asyncio
import io
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from openpyxl import load_workbook
from sqlalchemy import event, update

from scheduler_center.core.common.clock import utcnow
from scheduler_center.entrypoints.api import create_app
from scheduler_center.modules.analysis.models import AnalysisJob, JobStatus
from scheduler_center.modules.batch.models import Creator, Work, WorkKind, WorkStatus
from scheduler_center.modules.crawl.models import (
    CrawlerRunKind,
    CrawlerRunStatus,
    CrawlerTask,
    CrawlerTaskKind,
    CrawlerTaskRun,
    CrawlerTaskStatus,
)
from scheduler_center.modules.executor.models import ExecutorKind
from scheduler_center.modules.executor.tables import executors as executor_table
from scheduler_center.modules.material.models import Material
from tests.factories import (
    ANALYSIS_URL,
    CRAWLER_URL,
    analysis_says,
    api_settings,
    at,
    content,
    crawl,
    crawl_task,
    crawler_says,
    creators,
    job_event,
    load,
    messages,
    new_batch,
    recognized,
    register,
    upload,
    video,
    works,
)


async def test_batch_is_created_from_links_and_listed_with_progress(client, database):
    response = await client.post(
        "/api/batches",
        data={
            "name": "十一月",
            "links": "alice\nhttps://www.tiktok.com/@bob\n不是账号!",
            "start": "true",
        },
    )

    assert response.status_code == 201
    batch = response.json()
    assert (batch["name"], batch["source"], batch["status"]) == ("十一月", "粘贴链接", "running")
    assert len(await messages(database, "crawl_creator")) == 2

    page = (await client.get("/api/batches", params={"status": "running"})).json()
    assert page["total"] == 1
    assert page["items"][0]["creators"] == {"crawling": 2, "invalid": 1}
    assert page["items"][0]["creator_progress"] == {"crawling": 2, "invalid": 1}

    found = (await client.get(f"/api/batches/{batch['id']}/creators", params={"q": "bo"})).json()
    assert [item["handle"] for item in found["items"]] == ["bob"]
    invalid = await client.get(f"/api/batches/{batch['id']}/creators", params={"status": "invalid"})
    assert invalid.json()["items"][0]["note"] == "名单中的达人名称无法识别为 TikTok 账号"


async def test_workbench_uses_the_earliest_incomplete_video_phase_of_the_current_run(
    client, service, database
):
    batch = await new_batch(
        database, "alice", "private_user", "retry_user", "不是账号!", start=False
    )
    alice, private, retry, _ = await creators(database, batch)
    path = f"/api/batches/{batch.id}"

    async def counts():
        return (await client.get(path)).json()["creator_progress"]

    async def stage(job_id, name, status, **fields):
        await analysis_says(
            database,
            {
                "type": "stage",
                "job_id": job_id,
                "run": 1,
                "stage": name,
                "status": status,
                "occurred_at": at(2),
                **fields,
            },
        )

    assert await counts() == {"pending": 3, "invalid": 1}
    assert (await client.post(f"{path}/start")).is_success
    assert await counts() == {"crawling": 3, "invalid": 1}
    await crawler_says(
        database, await crawl_task(database, private), "crawled", conclusion="private"
    )
    await crawler_says(
        database,
        await crawl_task(database, retry),
        "crawled",
        conclusion="failed",
        reason="读取超时",
    )
    outcomes = {"failed": 1, "recrawl": 1, "invalid": 1}
    await crawl(database, alice, video(1), video(2))
    first_download, second_download = await messages(database, "download_video")
    assert (await upload(service, first_download["task_id"], text="one")).is_success
    [first] = await messages(database, "analyze_video")
    await stage(first["job_id"], "extract_audio", "succeeded", result={"segment_count": 2})
    await stage(first["job_id"], "listen", "succeeded", segment=0)
    assert await counts() == {**outcomes, "downloading": 1}

    assert (await upload(service, second_download["task_id"], text="two")).is_success
    _, second = await messages(database, "analyze_video")
    await stage(second["job_id"], "fetch", "succeeded", result={"has_audio": False})
    assert await counts() == {**outcomes, "audio_analysis": 1}
    listed = (await client.get(f"{path}/creators", params={"progress": "audio_analysis"})).json()
    assert [item["id"] for item in listed["items"]] == [str(alice.id)]
    assert listed["total"] == 1 and listed["items"][0]["progress"] == "audio_analysis"
    assert (await client.get(f"{path}/creators", params={"progress": "video_analysis"})).json()[
        "total"
    ] == 0

    await stage(first["job_id"], "listen", "succeeded", segment=1)
    assert await counts() == {**outcomes, "video_analysis": 1}
    for job in (first, second):
        await stage(job["job_id"], "recognize", "started")
        await analysis_says(
            database, job_event(job["job_id"], "completed", seconds=3, result=recognized("穿搭"))
        )
    assert await counts() == {**outcomes, "summarizing": 1}
    [summary] = await messages(database, "summarize_creator")
    await analysis_says(
        database,
        job_event(summary["job_id"], "completed", kind="summary", result={"tags": ["穿搭"]}),
    )
    assert await counts() == {**outcomes, "completed": 1}

    first_work, _ = await works(database, alice)
    assert (await client.post(f"/api/works/{first_work.id}/redownload")).is_success
    assert await counts() == {**outcomes, "downloading": 1}
    assert (await upload(service, first_download["task_id"], run=2, text="new one")).is_success
    assert await counts() == {**outcomes, "audio_analysis": 1}


async def test_preview_reports_unrecognized_rows(client):
    response = await client.post("/api/imports/preview", data={"links": "alice\n@@@"})

    assert response.json() == {
        "source": "粘贴链接",
        "rows": [
            {"row": None, "name": "alice", "handle": "alice", "oecuid": None, "has_qr": False},
            {"row": None, "name": "@@@", "handle": None, "oecuid": None, "has_qr": False},
        ],
        "invalid": 1,
    }
    assert (await client.post("/api/imports/preview", data={})).status_code == 422


async def test_export_downloads_the_delivery_workbook(client, database):
    batch = await new_batch(database, "alice")

    response = await client.get(f"/api/batches/{batch.id}/export")

    assert response.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    book = load_workbook(io.BytesIO(response.content))
    assert book.sheetnames == ["成功达人", "失败达人", "待重跑达人"]
    assert book["待重跑达人"]["D2"].value == "尚未爬取完成"


async def test_inactivity_tag_in_creator_list_detail_and_export(client, database, app):
    batch = await new_batch(database, "recorded", "retried", "fallback", "empty", start=False)
    recorded, retried, fallback, empty = await creators(database, batch)
    published_at = datetime(2024, 1, 31, 2, tzinfo=UTC)
    one_month = datetime(2024, 2, 29, 2, tzinfo=UTC)
    three_months = datetime(2024, 4, 30, 2, tzinfo=UTC)
    six_months = datetime(2024, 7, 31, 2, tzinfo=UTC)
    reanalysis = datetime(2025, 7, 31, 2, tzinfo=UTC)
    async with database.transaction() as session:
        for member, updated_at in (
            (recorded, reanalysis),
            (retried, reanalysis),
            (fallback, one_month),
        ):
            creator = await session.get(Creator, member.id)
            work = Work(
                batch.id,
                creator.id,
                1,
                creator.name,
                f"/photo/{creator.name}",
                WorkKind.PHOTO,
                published_at=published_at,
            )
            session.add(work)
            creator.crawled([work], updated_at)
        (await session.get(Creator, empty.id)).crawled([], reanalysis)

        session.add(
            CrawlerTask(
                CrawlerTaskKind.CRAWL,
                batch.id,
                recorded.id,
                {},
                status=CrawlerTaskStatus.SUCCEEDED,
                finished_at=three_months,
            )
        )
        task = CrawlerTask(
            CrawlerTaskKind.CRAWL,
            batch.id,
            retried.id,
            {},
            run=3,
            status=CrawlerTaskStatus.FAILED,
            finished_at=reanalysis,
        )
        session.add(task)
        for run, status, finished in (
            (1, CrawlerRunStatus.SUCCEEDED, one_month),
            (2, CrawlerRunStatus.SUCCEEDED, six_months),
            (3, CrawlerRunStatus.FAILED, reanalysis),
        ):
            session.add(
                CrawlerTaskRun(
                    task.id,
                    run,
                    batch.id,
                    retried.id,
                    None,
                    CrawlerRunKind.CRAWL,
                    {},
                    status=status,
                    updated_at=finished,
                )
            )
        session.add(
            CrawlerTaskRun(
                uuid4(),
                1,
                batch.id,
                retried.id,
                "video",
                CrawlerRunKind.DOWNLOAD,
                {},
                status=CrawlerRunStatus.SUCCEEDED,
                updated_at=reanalysis,
            )
        )

    lookups = []

    def count_crawl_lookups(_connection, _cursor, statement, *_):
        if "crawler_task_runs" in statement:
            lookups.append(statement)

    engine = app.state.database.engine.sync_engine
    path = f"/api/batches/{batch.id}"
    event.listen(engine, "before_cursor_execute", count_crawl_lookups)
    try:
        listing = await client.get(f"{path}/creators")
        assert listing.is_success
        assert len(lookups) == 1
        export = await client.get(f"{path}/export")
        assert export.is_success
        assert len(lookups) == 2
    finally:
        event.remove(engine, "before_cursor_execute", count_crawl_lookups)

    expected = [
        ("图文作者", "3个月内未更新"),
        ("图文作者", "半年内未更新"),
        ("图文作者", "1个月内未更新"),
        ("无作品", None),
    ]
    rows = listing.json()["items"]
    assert [row["id"] for row in rows] == [str(c.id) for c in (recorded, retried, fallback, empty)]
    assert [(row["labels"], row["inactivity_tag"]) for row in rows] == [
        ([label], tag) for label, tag in expected
    ]
    for member, (label, tag) in zip((recorded, retried, fallback, empty), expected, strict=True):
        detail = await client.get(f"/api/creators/{member.id}")
        assert detail.is_success
        assert detail.json()["creator"]["labels"] == [label]
        assert detail.json()["creator"]["inactivity_tag"] == tag

    sheet = load_workbook(io.BytesIO(export.content), rich_text=True)["成功达人"]
    assert list(sheet.iter_rows(min_row=3, min_col=14, max_col=15, values_only=True)) == [
        (label, f"{label}、{tag}" if tag else label) for label, tag in expected
    ]


async def test_creator_detail_shows_the_crawled_profile(client, database):
    batch = await new_batch(database, "alice")
    [alice] = await creators(database, batch)
    profile = {
        "handle": "alice",
        "url": "https://www.tiktok.com/@alice",
        "nickname": "Alice",
        "platform_user_id": "6800000000000000001",
    }

    task_id = await crawl_task(database, alice)
    await crawler_says(database, task_id, "crawled", conclusion="private", profile=profile)

    detail = (await client.get(f"/api/creators/{alice.id}")).json()
    assert detail["creator"]["profile"] == profile


async def test_running_batch_must_be_cancelled_before_deletion(client, database):
    batch = await new_batch(database, "alice")

    assert (await client.delete(f"/api/batches/{batch.id}")).status_code == 409
    assert (await client.post(f"/api/batches/{batch.id}/cancel")).status_code == 200
    assert (await client.delete(f"/api/batches/{batch.id}")).status_code == 204
    assert (await client.get(f"/api/batches/{batch.id}")).status_code == 404


async def test_pausing_collection_only_notifies_the_crawler(client, database, executors):
    """暂停采集时下载和分析照常；继续时两边都通知，解除旧版本留下的整批暂停。"""
    batch = await new_batch(database, "alice")
    await register(database, ExecutorKind.CRAWLER, CRAWLER_URL)
    await register(database, ExecutorKind.ANALYSIS, ANALYSIS_URL)

    response = await client.post(f"/api/batches/{batch.id}/pause")

    assert (response.json()["batch"]["status"], response.json()["warnings"]) == ("paused", [])
    assert executors.calls("crawler.test") == [f"POST /api/batches/{batch.id}/pause"]
    assert executors.calls("analysis.test") == []

    await client.post(f"/api/batches/{batch.id}/resume")
    assert executors.calls("analysis.test") == [f"POST /api/batches/{batch.id}/resume"]


async def test_every_online_crawler_hears_about_the_pause(client, database, executors):
    """多台爬取设备时每台都要通知到，否则没收到的设备会接着采集。"""
    batch = await new_batch(database, "alice")
    await register(database, ExecutorKind.CRAWLER, CRAWLER_URL)
    await register(database, ExecutorKind.CRAWLER, "http://crawler2.test", instance_id="c2")

    response = await client.post(f"/api/batches/{batch.id}/pause")

    assert response.json()["warnings"] == []
    pause = [f"POST /api/batches/{batch.id}/pause"]
    assert executors.calls("crawler.test") == executors.calls("crawler2.test") == pause


async def test_unreachable_executors_are_reported(client, database):
    batch = await new_batch(database, "alice")

    response = await client.post(f"/api/batches/{batch.id}/pause")

    assert response.json()["batch"]["status"] == "paused"
    assert response.json()["warnings"] == ["爬取程序不在线"]


async def test_pages_get_the_frontend_and_unknown_api_paths_get_404(database_url, tmp_path):
    (tmp_path / "index.html").write_text("<html>调度中心</html>", encoding="utf-8")
    app = create_app(api_settings(database_url=database_url, static_root=tmp_path))
    transport = httpx.ASGITransport(app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/", "/batches/1"):
            page = await client.get(path)
            assert "调度中心" in page.text
            assert page.headers["cache-control"] == "no-cache"
        assert (await client.get("/api/no-such-thing")).status_code == 404
    await app.state.gateway.aclose()
    await app.state.database.dispose()


async def test_service_endpoints_require_the_token(client):
    assert (
        await client.put("/api/crawler-services/c1", json={"url": "x", "version": "1"})
    ).status_code == 401
    assert (await client.get(f"/api/materials/{UUID(int=1)}")).status_code == 401


async def test_executor_list_distinguishes_withdrawal_from_timeout(client, service, database):
    body = {"url": CRAWLER_URL, "version": "2.0"}
    reply = await service.put("/api/crawler-services/c1", json=body)
    # 不报身份池的程序不分账号池，并发按默认
    assert reply.json() == {
        "identity_target": None,
        "crawl_slots": 2,
        "download_slots": 4,
        "warm_slots": 1,
        "revoked_attempts": [],
    }
    stale = utcnow() - timedelta(minutes=5)
    await register(database, ExecutorKind.ANALYSIS, ANALYSIS_URL, stale)
    await register(database, ExecutorKind.CRAWLER, CRAWLER_URL, stale, instance_id="timed-out")
    await register(database, ExecutorKind.CRAWLER, CRAWLER_URL, instance_id="working")

    response = await client.get("/api/executors")
    before = {item["instance_id"]: item for item in response.raise_for_status().json()}
    assert before["c1"]["online"] is True and before["c1"]["version"] == "2.0"
    assert before["analysis-1"]["online"] is False

    assert (await service.delete("/api/crawler-services/c1")).status_code == 204
    response = await client.get("/api/executors")
    crawlers = {
        item["instance_id"]: item
        for item in response.raise_for_status().json()
        if item["kind"] == "crawler"
    }
    active = {
        name: item["online"] for name, item in crawlers.items() if item["withdrawn_at"] is None
    }
    assert active == {"timed-out": False, "working": True}
    assert crawlers["timed-out"]["last_seen_at"] is not None

    withdrawn = crawlers["c1"]
    assert withdrawn["online"] is False and withdrawn["withdrawn_at"] is not None
    assert withdrawn["registered_at"] == before["c1"]["registered_at"]
    assert withdrawn["last_seen_at"] == before["c1"]["last_seen_at"]


async def test_crawler_slots_set_on_the_page_go_out_with_the_heartbeat(client, service):
    body = {"url": CRAWLER_URL, "version": "2.0"}
    await service.put("/api/crawler-services/c1", json=body)

    path = "/api/executors/crawler/c1/slots"
    slots = {"crawl_slots": 4, "download_slots": 6, "warm_slots": 3}
    response = await client.put(path, json=slots)
    assert response.json()["crawl_slots"] == 4
    assert response.json()["warm_slots"] == 3
    reply = (await service.put("/api/crawler-services/c1", json=body)).json()
    assert (reply["crawl_slots"], reply["download_slots"], reply["warm_slots"]) == (4, 6, 3)

    for invalid in ({"crawl_slots": 99}, {"warm_slots": 0}, {"warm_slots": 17}):
        assert (await client.put(path, json={**slots, **invalid})).status_code == 422
    legacy = await client.put(path, json={"crawl_slots": 2, "download_slots": 5})
    assert legacy.json()["warm_slots"] == 3
    assert (await client.put("/api/executors/crawler/nobody/slots", json=slots)).status_code == 404
    reply = await service.put("/api/analysis-services/a1", json=body)
    assert reply.json()["crawl_slots"] is None and reply.json()["warm_slots"] is None


async def test_crawl_progress_shows_both_stages_from_online_crawlers(client, service, executors):
    for name in ("c1", "c2", "legacy", "offline"):
        response = await service.put(
            f"/api/crawler-services/{name}", json={"url": f"{CRAWLER_URL}/{name}", "version": "1"}
        )
        assert response.is_success
    await service.delete("/api/crawler-services/offline")
    profile = {
        "task_id": "00000000-0000-4000-8000-000000000001",
        "handle": "alice",
        "stage": "profile",
        "pages_read": 0,
        "works_found": 0,
        "max_works": 5,
    }
    listing = {
        **profile,
        "task_id": "00000000-0000-4000-8000-000000000002",
        "handle": "bob",
        "stage": "listing",
        "pages_read": 2,
        "works_found": 3,
    }
    executors.responses["GET", "/c1/api/crawl-progress"] = httpx.Response(200, json=[profile])
    executors.responses["GET", "/c2/api/crawl-progress"] = httpx.Response(200, json=[listing])
    executors.responses["GET", "/legacy/api/crawl-progress"] = httpx.Response(404)

    response = await client.get("/api/executors/crawler/crawl-progress")
    assert response.json() == [
        {"instance_id": "c1", "crawls": [profile]},
        {"instance_id": "c2", "crawls": [listing]},
        {"instance_id": "legacy", "crawls": None},
    ]


async def test_download_progress_collects_online_crawlers_even_if_one_is_older(
    client, service, executors
):
    for name in ("c1", "c2", "legacy", "old-progress", "offline"):
        response = await service.put(
            f"/api/crawler-services/{name}", json={"url": f"{CRAWLER_URL}/{name}", "version": "1"}
        )
        assert response.is_success
    await service.delete("/api/crawler-services/offline")
    first = {
        "task_id": "00000000-0000-4000-8000-000000000001",
        "work_id": "7001",
        "stage": "downloading",
        "received_bytes": 400,
        "total_bytes": 1000,
    }
    second = {
        **first,
        "task_id": "00000000-0000-4000-8000-000000000002",
        "work_id": "7002",
        "total_bytes": None,
    }
    speed_header = "X-Download-Bytes-Per-Second"
    executors.responses["GET", "/c1/api/download-progress"] = httpx.Response(
        200, json=[first], headers={speed_header: "1024"}
    )
    executors.responses["GET", "/c2/api/download-progress"] = httpx.Response(
        200, json=[second], headers={speed_header: "0"}
    )
    executors.responses["GET", "/legacy/api/download-progress"] = httpx.Response(404)
    executors.responses["GET", "/old-progress/api/download-progress"] = httpx.Response(200, json=[])

    response = await client.get("/api/executors/crawler/download-progress")
    assert response.json() == [
        {"instance_id": "c1", "downloads": [first], "download_bytes_per_second": 1024},
        {"instance_id": "c2", "downloads": [second], "download_bytes_per_second": 0},
        {"instance_id": "legacy", "downloads": None, "download_bytes_per_second": None},
        {"instance_id": "old-progress", "downloads": [], "download_bytes_per_second": None},
    ]
    assert {item.url.path for item in executors.requests} == {
        "/c1/api/download-progress",
        "/c2/api/download-progress",
        "/legacy/api/download-progress",
        "/old-progress/api/download-progress",
    }


async def test_manual_executor_waits_for_heartbeat_and_only_deletes_offline(
    client, service, database
):
    await service.put("/api/crawler-services/c1", json={"url": CRAWLER_URL, "version": "1"})
    body = {"kind": "crawler", "instance_id": "c2", "url": "http://crawler-next.test:8091/"}
    registration = await client.post("/api/executors", json=body)
    assert registration.status_code == 201
    pending = registration.json()
    assert pending["online"] is False and pending["last_seen_at"] is None
    assert pending["url"] == "http://crawler-next.test:8091"
    assert (await client.get("/api/executors")).json()[1] == pending
    assert (await client.get("/api/executors/crawler/status")).is_success  # 已上线的实例照常响应
    assert (await client.post("/api/executors", json=body)).status_code == 409
    assert (await client.delete("/api/executors/crawler/c1")).status_code == 409

    heartbeat = {"url": "http://crawler-next.test:8092", "version": "2.0"}
    assert (await service.put("/api/crawler-services/c2", json=heartbeat)).is_success
    online = (await client.get("/api/executors")).json()[1]
    assert online["online"] and online["last_seen_at"]
    assert (online["url"], online["version"]) == (heartbeat["url"], heartbeat["version"])

    async with database.transaction() as session:
        await session.execute(
            update(executor_table)
            .where(executor_table.c.instance_id == "c2")
            .values(last_seen_at=utcnow() - timedelta(minutes=5))
        )
    path = "/api/executors/crawler/c2"
    assert (await client.delete(path)).status_code == 204
    assert (await client.delete(path)).status_code == 404
    assert [item["instance_id"] for item in (await client.get("/api/executors")).json()] == ["c1"]

    assert (await service.put("/api/crawler-services/c2", json=heartbeat)).is_success
    assert (await client.get("/api/executors")).json()[1]["online"]


async def test_concurrent_manual_registration_cannot_overwrite_executor(client):
    body = {"kind": "analysis", "instance_id": "a1", "url": ANALYSIS_URL}
    results = await asyncio.gather(*(client.post("/api/executors", json=body) for _ in range(2)))
    assert sorted(result.status_code for result in results) == [201, 409]
    assert (await client.delete("/api/executors/analysis/a1")).status_code == 204


@pytest.mark.parametrize(
    "url", ["ftp://executor.test", "http://user@executor.test", "http://executor.test/?q=1"]
)
async def test_manual_executor_validates_the_address(client, url):
    body = {"kind": "crawler", "instance_id": "c1", "url": url}
    assert (await client.post("/api/executors", json=body)).status_code == 422


async def test_upload_checks_content_and_round(service, database):
    batch = await new_batch(database, "alice")
    [alice] = await creators(database, batch)
    await crawl(database, alice, video(1))
    [download] = await messages(database, "download_video")
    task_id = download["task_id"]
    data, _ = content("video")

    wrong = await service.put(
        f"/api/download-tasks/{task_id}/runs/1/video",
        content=data,
        headers={"X-Content-SHA256": "0" * 64},
    )
    assert wrong.status_code == 422
    assert (await upload(service, task_id, run=2)).status_code == 409
    assert (await upload(service, str(UUID(int=7)))).status_code == 404
    assert (await upload(service, task_id)).status_code == 204
    assert (await upload(service, task_id)).status_code == 204  # 相同内容重复上传
    assert (await upload(service, task_id, text="other")).status_code == 409
    assert len(await messages(database, "analyze_video")) == 1

    [work] = await works(database, alice)
    response = await service.get(
        f"/api/materials/{work.material_id}", headers={"Range": "bytes=1-3"}
    )
    assert (response.status_code, response.content) == (206, data[1:4])


async def test_discarded_material_cannot_be_downloaded(service, database):
    batch = await new_batch(database, "alice")
    [alice] = await creators(database, batch)
    await crawl(database, alice, video(1))
    [download] = await messages(database, "download_video")
    await upload(service, download["task_id"])
    [analysis] = await messages(database, "analyze_video")

    await analysis_says(
        database,
        job_event(analysis["job_id"], "completed", result={"recognition": {"tags": ["美妆"]}}),
    )

    material = await load(database, Material, UUID(analysis["material"]["id"]))
    assert material.discarded_at is not None
    assert (await service.get(f"/api/materials/{material.id}")).status_code == 404


async def test_retry_is_forwarded_and_reopens_the_video(client, service, database, executors):
    batch = await new_batch(database, "alice")
    [alice] = await creators(database, batch)
    await crawl(database, alice, video(1))
    [download] = await messages(database, "download_video")
    await upload(service, download["task_id"])
    [analysis] = await messages(database, "analyze_video")
    job_id = analysis["job_id"]
    await analysis_says(database, job_event(job_id, "failed", error="模型拒绝"))
    await register(database, ExecutorKind.ANALYSIS, ANALYSIS_URL)
    executors.responses[("POST", f"/api/jobs/{job_id}/retry")] = httpx.Response(
        200,
        json={
            "job_id": job_id,
            "run": 1,
            "status": "processing",
            "error": None,
            "occurred_at": at(5),
        },
    )
    job = await load(database, AnalysisJob, UUID(job_id))

    response = await client.post(f"/api/works/{job.work_id}/retry")

    assert response.json()["status"] == "analyzing"
    assert (await load(database, AnalysisJob, job.id)).status == JobStatus.PROCESSING
    assert (await load(database, Work, job.work_id)).status == WorkStatus.ANALYZING
    detail = (await client.get(f"/api/works/{job.work_id}")).json()
    assert detail["analysis"]["status"] == "processing"
    history = [
        (item["payload"]["type"], item["payload"].get("status")) for item in detail["history"]
    ]
    assert history == [("job", "failed"), ("uploaded", None)]  # 测试里分析事件的时间在上传之前


async def test_ai_configuration_is_forwarded_to_the_analysis_service(client, database, executors):
    assert (await client.get("/api/ai/models")).status_code == 503

    await register(database, ExecutorKind.ANALYSIS, ANALYSIS_URL)
    executors.responses[("GET", "/api/ai/models")] = httpx.Response(200, json=[{"id": "m1"}])

    response = await client.get("/api/ai/models", params={"provider": "p1"})

    assert response.json() == [{"id": "m1"}]
    [request] = executors.requests
    assert (request.url.params["provider"], request.headers["authorization"]) == (
        "p1",
        "Bearer test-service-token",
    )

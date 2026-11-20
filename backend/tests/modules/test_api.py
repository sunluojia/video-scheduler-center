"""HTTP 接口：页面使用的批次接口、执行程序使用的注册、上传、下载接口，以及转发。"""

import asyncio
import io
from datetime import timedelta
from uuid import UUID

import httpx
import pytest
from openpyxl import load_workbook
from sqlalchemy import update

from scheduler_center.core.common.clock import utcnow
from scheduler_center.entrypoints.api import create_app
from scheduler_center.modules.analysis.models import AnalysisJob, JobStatus
from scheduler_center.modules.batch.models import Work, WorkStatus
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

    found = (await client.get(f"/api/batches/{batch['id']}/creators", params={"q": "bo"})).json()
    assert [item["handle"] for item in found["items"]] == ["bob"]
    invalid = await client.get(f"/api/batches/{batch['id']}/creators", params={"status": "invalid"})
    assert invalid.json()["items"][0]["note"] == "名单中的达人名称无法识别为 TikTok 账号"


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
    assert book.sheetnames == ["成功识别", "失败账号", "重新抓取"]
    assert book["重新抓取"]["D2"].value == "尚未爬取完成"


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


async def test_pause_notifies_executors_and_reports_the_unreachable(client, database, executors):
    batch = await new_batch(database, "alice")
    await register(database, ExecutorKind.ANALYSIS, ANALYSIS_URL)

    response = await client.post(f"/api/batches/{batch.id}/pause")

    assert response.json()["batch"]["status"] == "paused"
    assert response.json()["warnings"] == ["爬取程序不在线"]
    assert executors.calls("analysis.test") == [f"POST /api/batches/{batch.id}/pause"]


async def test_pages_get_the_frontend_and_unknown_api_paths_get_404(database_url, tmp_path):
    (tmp_path / "index.html").write_text("<html>调度中心</html>", encoding="utf-8")
    app = create_app(api_settings(database_url=database_url, static_root=tmp_path))
    transport = httpx.ASGITransport(app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert "调度中心" in (await client.get("/batches/1")).text
        assert (await client.get("/api/no-such-thing")).status_code == 404
    await app.state.gateway.aclose()
    await app.state.database.dispose()


async def test_service_endpoints_require_the_token(client):
    assert (
        await client.put("/api/crawler-services/c1", json={"url": "x", "version": "1"})
    ).status_code == 401
    assert (await client.get(f"/api/materials/{UUID(int=1)}")).status_code == 401


async def test_executors_register_and_go_offline(client, service, database):
    body = {"url": CRAWLER_URL, "version": "2.0"}
    reply = await service.put("/api/crawler-services/c1", json=body)
    # 不报身份池的程序不分账号池，并发按默认
    assert reply.json() == {"identity_target": None, "crawl_slots": 2, "download_slots": 4}
    await register(database, ExecutorKind.ANALYSIS, ANALYSIS_URL, utcnow() - timedelta(minutes=5))

    listed = {item["kind"]: item for item in (await client.get("/api/executors")).json()}
    assert (listed["crawler"]["online"], listed["crawler"]["version"]) == (True, "2.0")
    assert listed["analysis"]["online"] is False

    assert (await service.delete("/api/crawler-services/c1")).status_code == 204
    [crawler] = [i for i in (await client.get("/api/executors")).json() if i["kind"] == "crawler"]
    assert crawler["online"] is False


async def test_crawler_slots_set_on_the_page_go_out_with_the_heartbeat(client, service):
    body = {"url": CRAWLER_URL, "version": "2.0"}
    await service.put("/api/crawler-services/c1", json=body)

    slots = {"crawl_slots": 4, "download_slots": 6}
    response = await client.put("/api/executors/crawler/c1/slots", json=slots)
    assert response.json()["crawl_slots"] == 4
    reply = (await service.put("/api/crawler-services/c1", json=body)).json()
    assert (reply["crawl_slots"], reply["download_slots"]) == (4, 6)

    too_many = {"crawl_slots": 99, "download_slots": 6}
    assert (await client.put("/api/executors/crawler/c1/slots", json=too_many)).status_code == 422
    assert (await client.put("/api/executors/crawler/nobody/slots", json=slots)).status_code == 404
    reply = await service.put("/api/analysis-services/a1", json=body)
    assert reply.json()["crawl_slots"] is None  # 分析服务不用


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
    assert [event["payload"]["status"] for event in detail["history"]] == ["failed"]


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

"""账号池：心跳带来的身份池快照、需要人处理的问题、转给设备的刷新和退休。"""

import httpx

from scheduler_center.core.common.clock import utcnow
from tests.factories import CRAWLER_URL


def snapshot(**fields) -> dict:
    """爬取程序报来的快照，时间是 Unix 秒。"""
    now = utcnow().timestamp()
    return {
        "target": 3,
        "ready": 1,
        "in_use": 0,
        "warming": 1,
        "cooling": 0,
        "total": 2,
        "short_since": None,
        "warm_total": 4,
        "warm_failed": 3,
        "items": [
            {"id": "a1", "state": "ready", "fails": 0, "warmed_at": now, "browser": "146"},
            {"id": "b2", "state": "retired", "fails": 6, "retired_at": now, "retire_reason": "x"},
        ],
        **fields,
    }


async def heartbeat(service, instance_id: str, identities: dict | None = None) -> int | None:
    """发一次心跳，返回调度中心分给这台设备的可用身份数。"""
    body = {"url": CRAWLER_URL, "version": "1", "identities": identities}
    reply = await service.put(f"/api/crawler-services/{instance_id}", json=body)
    return reply.raise_for_status().json()["identity_target"]


async def test_the_pool_target_is_shared_by_online_crawlers(client, service):
    assert await heartbeat(service, "c1", snapshot()) == 8  # 没设置过，默认 8 个
    assert (await client.put("/api/identity-pool", json={"target": 0})).status_code == 422
    assert (await client.put("/api/identity-pool", json={"target": 5})).is_success

    assert await heartbeat(service, "c1", snapshot()) == 5
    assert await heartbeat(service, "c2", snapshot()) == 3  # 两台平均分摊，向上取整
    assert await heartbeat(service, "fake") is None  # 不报身份池的不分
    assert (await client.get("/api/identities")).json()["target"] == 5

    await service.delete("/api/crawler-services/c2")
    assert await heartbeat(service, "c1", snapshot()) == 5  # 一台离线，剩下的补上


async def test_each_device_shows_its_latest_snapshot(client, service):
    await heartbeat(service, "c1", snapshot())
    await heartbeat(service, "fake")  # 模拟爬取程序不报快照，不出现在账号池

    board = (await client.get("/api/identities")).json()
    [device] = board["devices"]
    assert (device["instance_id"], device["online"]) == ("c1", True)
    assert [item["state"] for item in device["snapshot"]["items"]] == ["ready", "retired"]
    assert board["alerts"] == []

    await service.delete("/api/crawler-services/c1")
    [device] = (await client.get("/api/identities")).json()["devices"]
    assert device["online"] is False  # 离线后仍显示最后的样子


async def test_too_few_identities_for_ten_minutes_needs_attention(client, service):
    await heartbeat(service, "c1", snapshot(short_since=utcnow().timestamp() - 60))
    assert (await client.get("/api/identities")).json()["alerts"] == []

    await heartbeat(service, "c1", snapshot(short_since=utcnow().timestamp() - 12 * 60))
    [alert] = (await client.get("/api/identities")).json()["alerts"]
    assert alert.startswith(
        "设备 c1 可用身份 1/3，已经 12 分钟不够；最近一小时预热 4 次，成功 1 次"
    )
    assert "采集线路" in alert


async def test_actions_are_forwarded_to_the_device(client, service, executors):
    await heartbeat(service, "c1", snapshot())

    assert (await client.post("/api/identities/c1/a1/retire")).status_code == 204
    assert executors.calls("crawler.test") == ["POST /api/identities/a1/retire"]

    executors.responses["POST", "/api/identities/a1/refresh"] = httpx.Response(
        409, json={"detail": "这个身份正在使用或预热，稍后再试"}
    )
    busy = await client.post("/api/identities/c1/a1/refresh")
    assert (busy.status_code, busy.json()["detail"]) == (
        502,
        "爬取程序返回 409：这个身份正在使用或预热，稍后再试",
    )
    assert (await client.post("/api/identities/nobody/a1/refresh")).status_code == 404
    assert (await client.post("/api/identities/c1/a1/delete")).status_code == 422

    await service.delete("/api/crawler-services/c1")
    assert (await client.post("/api/identities/c1/a1/refresh")).status_code == 503


async def test_heartbeats_with_a_wrong_snapshot_are_refused(service):
    body = {"url": CRAWLER_URL, "version": "1", "identities": {"target": "many"}}
    assert (await service.put("/api/crawler-services/c1", json=body)).status_code == 422
    body["identities"] = None  # 分析服务和模拟爬取程序不报快照
    assert (await service.put("/api/crawler-services/c1", json=body)).is_success

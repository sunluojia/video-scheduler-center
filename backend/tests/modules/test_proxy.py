"""代理：代理池（文件夹、用途标签）、各用途的规则，线路的分配、归还和好坏判断，以及交给设备的测试。"""

import asyncio
import json
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select, update

from scheduler_center.core.common.clock import utcnow
from scheduler_center.modules.executor.models import ExecutorKind
from scheduler_center.modules.proxy.dispatcher import Dispatcher
from scheduler_center.modules.proxy.models import (
    CheckResult,
    Lease,
    Line,
    LineState,
    ListingResult,
    Proxy,
    ProxyStatus,
    Use,
)
from scheduler_center.modules.proxy.tables import proxies, proxy_leases, proxy_lines
from tests.factories import CRAWLER_URL, build_worker, load, register

DEVICE = "crawler-1"
DOWNLOAD, CRAWL = Use.DOWNLOAD.value, Use.CRAWL.value


async def add(client, text: str, uses=(DOWNLOAD,), folder_id=None) -> dict[str, str]:
    """导入代理，返回 主机 → 代理 ID。"""
    body = {"text": text, "uses": [*uses], "folder_id": folder_id}
    assert (await client.post("/api/proxies/import", json=body)).status_code == 200
    items = (await client.get("/api/proxies", params={"size": 200})).json()["items"]
    return {item["host"]: item["id"] for item in items}


async def passed(database, exits: dict[str, str]) -> None:
    """模拟设备测试通过：按主机写上出口。"""
    async with database.transaction() as session:
        for proxy in await session.scalars(select(Proxy)):
            if proxy.host in exits:
                proxy.tested(CheckResult.OK, utcnow(), DEVICE, exit_ip=exits[proxy.host])


async def lines(client, database, count: int, uses=(DOWNLOAD,)) -> dict[str, str]:
    """添加几个出口各不相同的代理：10.0.0.N 的出口是 203.0.113.N。"""
    hosts = [f"10.0.0.{n}" for n in range(1, count + 1)]
    ids = await add(client, "\n".join(f"{host}:80" for host in hosts), uses)
    await passed(database, {host: f"203.0.113.{host.split('.')[-1]}" for host in hosts})
    return ids


async def use_pool(client, use: str = DOWNLOAD, **rule) -> None:
    body = {"source": "pool", **rule}
    assert (await client.put(f"/api/proxy-uses/{use}/rule", json=body)).status_code == 200


async def lease(
    service, device: str = DEVICE, avoid=(), use: str = DOWNLOAD, *, task_id=None
) -> dict:
    body = {"device": device, "use": use, "avoid": [*avoid], "task_id": task_id}
    response = await service.post("/api/proxy-leases", json=body)
    assert response.status_code == 200
    return response.json()


def line_of(reply: dict) -> str | None:
    return reply["lease"] and reply["lease"]["line"]


async def give_back(service, reply: dict, outcome: str = "normal", **extra) -> None:
    lease_id = reply["lease"]["lease_id"]
    body = {"outcome": outcome, **extra}
    response = await service.post(f"/api/proxy-leases/{lease_id}/release", json=body)
    assert response.status_code == 204


async def line(database, key: str, use: str = DOWNLOAD) -> Line:
    return await load(database, Line, (Use(use), key))


async def card(client, use: str = DOWNLOAD) -> dict:
    """页面顶部的用途卡片。"""
    return next(item for item in (await client.get("/api/proxy-uses")).json() if item["key"] == use)


async def folder(client, name: str, parent_id=None) -> str:
    body = {"name": name, "parent_id": parent_id}
    return (await client.post("/api/proxy-folders", json=body)).json()["id"]


async def expire_cooldowns(database) -> None:
    async with database.transaction() as session:
        await session.execute(update(proxy_lines).values(cooldown_until=utcnow()))


async def test_only_repeated_empty_bodies_across_distinct_tasks_cool_the_crawl_line(
    client, service, database
):
    key = "203.0.113.1"
    await lines(client, database, 1, uses=[CRAWL])
    await use_pool(client, CRAWL, line_limit=3)
    a, b, c = (str(uuid4()) for _ in range(3))

    async def report(task_id, result, outcome="suspect"):
        reply = await lease(service, use=CRAWL, task_id=task_id)
        assert line_of(reply) == key
        await give_back(service, reply, outcome, listing_result=result)
        return await load(database, Lease, UUID(reply["lease"]["lease_id"]))

    for _ in range(3):
        released = await report(a, "empty_response")
    assert released.listing_result == ListingResult.EMPTY_RESPONSE and released.released_at
    for task_id in (a, b, c):
        await report(task_id, "read_failed")
    await report(b, "empty_response")
    active = await line(database, key, CRAWL)
    assert active.state(utcnow()) == LineState.OPEN and active.score == 0

    await report(c, "complete", "normal")
    await report(b, "empty_response")
    await report(c, "empty_response")
    assert (await line(database, key, CRAWL)).state(utcnow()) == LineState.OPEN
    await report(a, "empty_response")
    cooling = await line(database, key, CRAWL)
    assert cooling.state(utcnow()) == LineState.COOLING and cooling.backoff == 0
    assert cooling.cooldown_until - cooling.updated_at == timedelta(seconds=60)
    assert (await lease(service, use=CRAWL))["lease"] is None

    await expire_cooldowns(database)
    await report(a, "empty_response")
    active = await line(database, key, CRAWL)
    assert active.state(utcnow()) == LineState.OPEN and active.backoff == 0


async def test_a_failed_list_does_not_extend_an_existing_probe_cooldown(client, service, database):
    key = "203.0.113.1"
    await lines(client, database, 1, uses=[CRAWL])
    await use_pool(client, CRAWL)
    for _ in range(3):
        await give_back(service, await lease(service, use=CRAWL), "suspect")
    await expire_cooldowns(database)
    before = await line(database, key, CRAWL)

    for result in ("read_failed", "empty_response"):
        probe = await lease(service, use=CRAWL, task_id=str(uuid4()))
        assert probe["lease"]["probe"]
        await give_back(service, probe, "suspect", listing_result=result)
        current = await line(database, key, CRAWL)
        assert current.state(utcnow()) == LineState.PROBING
        assert (current.backoff, current.cooldown_until) == (before.backoff, before.cooldown_until)

    recovered = await lease(service, use=CRAWL, task_id=str(uuid4()))
    await give_back(service, recovered, listing_result="complete")
    assert (await line(database, key, CRAWL)).state(utcnow()) == LineState.OPEN


async def test_import_previews_rows_and_skips_duplicates(client):
    text = "1.2.3.4:80:alice:secret\n不是代理\n1.2.3.4:80:alice:other"
    preview = (await client.post("/api/proxies/parse", json={"text": text})).json()
    assert [(row["number"], row["host"], row["duplicate"]) for row in preview] == [
        (1, "1.2.3.4", False),
        (2, None, False),
        (3, "1.2.3.4", True),
    ]
    assert preview[1]["error"] and preview[0]["has_password"]
    assert "secret" not in json.dumps(preview)

    us = await folder(client, "美国")
    body = {"text": text, "folder_id": us, "uses": ["crawler", DOWNLOAD]}
    imported = await client.post("/api/proxies/import", json=body)
    assert imported.json() == {"created": 1, "skipped": 2}

    response = await client.get("/api/proxies")
    [item] = response.json()["items"]
    assert (item["username"], item["has_password"], item["status"], item["testing"]) == (
        "a***e",
        True,
        "untested",
        True,
    )
    assert (item["folder_id"], item["uses"]) == (us, ["crawler"])  # 整个服务已包含下载
    assert [state["use"] for state in item["states"]] == [CRAWL, DOWNLOAD]
    assert "secret" not in response.text


async def test_changing_address_or_credentials_retests_the_proxy(client, database):
    ids = await add(client, "1.2.3.4:80:alice:secret\n1.2.3.6:80:alice:other")
    await passed(database, {"1.2.3.4": "203.0.113.1"})
    path = f"/api/proxies/{ids['1.2.3.4']}"

    renamed = (await client.patch(path, json={"name": "美国 1", "uses": [CRAWL]})).json()
    assert (renamed["status"], renamed["exit_ip"], renamed["testing"], renamed["uses"]) == (
        "available",
        "203.0.113.1",
        False,
        [CRAWL],
    )
    password = (await client.patch(path, json={"password": "new"})).json()
    assert (password["status"], password["exit_ip"], password["testing"]) == (
        "untested",
        "203.0.113.1",
        True,
    )
    moved = (await client.patch(path, json={"host": "1.2.3.5"})).json()
    assert moved["exit_ip"] is None
    clash = await client.patch(f"/api/proxies/{ids['1.2.3.6']}", json={"host": "1.2.3.5"})
    assert clash.status_code == 409


async def test_a_use_goes_direct_until_it_is_set_to_the_pool(client, service, database):
    await add(client, "socks5://alice:secret@10.0.0.1:1080\n10.0.0.2:3128")
    await passed(database, {"10.0.0.1": "203.0.113.1", "10.0.0.2": "203.0.113.2"})
    direct = await lease(service)
    assert (line_of(direct), direct["lease"]["route"]["kind"]) == (f"direct:{DEVICE}", "direct")

    await use_pool(client)
    first = await lease(service)
    assert first["lease"]["route"] == {
        "kind": "proxy",
        "protocol": "socks5",
        "host": "10.0.0.1",
        "port": 1080,
        "username": "alice",
        "password": "secret",
        "private_key": None,
        "host_key": None,
    }
    replies = [first] + [await lease(service) for _ in range(3)]
    assert [line_of(reply) for reply in replies] == [
        "203.0.113.1",
        "203.0.113.1",
        "203.0.113.2",
        "203.0.113.2",
    ]
    full = await lease(service)
    assert (full["lease"], full["retry_after"], full["reason"]) == (None, 5, "线路都占满了")

    await give_back(service, first)
    assert line_of(await lease(service)) == "203.0.113.1"
    crawl = await lease(service, use=CRAWL)  # 获取信息没改规则，仍是直连
    assert crawl["lease"]["route"]["kind"] == "direct"


async def test_each_use_keeps_its_own_lines(client, service, database):
    """同一个出口：下载视频被判可疑而冷却，获取信息照常使用；并发也各算各的。"""
    await lines(client, database, 1, uses=["crawler"])
    await use_pool(client, DOWNLOAD, line_limit=1)
    await use_pool(client, CRAWL, line_limit=1)

    for _ in range(3):
        await give_back(service, await lease(service), "suspect")
    assert (await lease(service))["reason"] == "线路都在冷却或限流"
    held = await lease(service, use=CRAWL)
    assert line_of(held) == "203.0.113.1"
    assert (await lease(service, use=CRAWL))["reason"] == "线路都占满了"

    [item] = (await client.get("/api/proxies")).json()["items"]
    states = {state["use"]: state["state"] for state in item["states"]}
    assert states == {CRAWL: "open", DOWNLOAD: "cooling"}
    [download_line] = (await card(client, DOWNLOAD))["lines"]
    assert (download_line["state"], download_line["in_use"]) == ("cooling", 0)
    [crawl_line] = (await card(client, CRAWL))["lines"]
    assert (crawl_line["state"], crawl_line["in_use"]) == ("open", 1)


async def test_priority_follows_the_folder_order(client, service, database):
    later = await folder(client, "备用")
    first = await folder(client, "主力")
    await add(client, "10.0.0.1:80", folder_id=later)
    await add(client, "10.0.0.2:80", folder_id=first)
    await add(client, "10.0.0.3:80")  # 没放文件夹的排最后
    await passed(
        database,
        {"10.0.0.1": "203.0.113.1", "10.0.0.2": "203.0.113.2", "10.0.0.3": "203.0.113.3"},
    )
    await use_pool(client)
    assert line_of(await lease(service)) == "203.0.113.1"

    top = {"parent_id": None, "position": 0}
    assert (await client.post(f"/api/proxy-folders/{first}/move", json=top)).status_code == 204
    assert [
        item["name"] for item in (await client.get("/api/proxy-folders")).json()["folders"]
    ] == [
        "主力",
        "备用",
    ]
    assert [line_of(await lease(service)) for _ in range(5)] == [
        "203.0.113.2",
        "203.0.113.2",
        "203.0.113.1",
        "203.0.113.3",
        "203.0.113.3",
    ]


async def test_proxies_sharing_an_exit_are_one_line_for_the_whole_cluster(
    client, service, database
):
    await add(client, "10.0.0.1:80\n10.0.0.2:80")
    await passed(database, {"10.0.0.1": "203.0.113.9", "10.0.0.2": "203.0.113.9"})
    await use_pool(client)

    one, two = await lease(service, "crawler-1"), await lease(service, "crawler-2")
    assert {line_of(one), line_of(two)} == {"203.0.113.9"}
    assert one["lease"]["route"]["host"] == two["lease"]["route"]["host"] == "10.0.0.1"
    assert line_of(await lease(service, "crawler-2")) is None

    found = (await card(client))["lines"]
    assert [(item["key"], item["proxies"], item["in_use"]) for item in found] == [
        ("203.0.113.9", ["10.0.0.1:80", "10.0.0.2:80"], 2)
    ]
    filtered = await client.get("/api/proxies", params={"line": "203.0.113.9"})
    assert filtered.json()["total"] == 2


async def test_rotation_starts_after_the_last_line(client, service, database):
    await lines(client, database, 3)
    await use_pool(client, mode="rotate")

    seen = []
    for _ in range(4):
        reply = await lease(service)
        seen.append(line_of(reply))
        await give_back(service, reply)

    assert seen == ["203.0.113.1", "203.0.113.2", "203.0.113.3", "203.0.113.1"]


async def test_selected_sources_use_priority_and_share_capacity_per_exit(client, service, database):
    first = await folder(client, "主力")
    child = await folder(client, "主力子组", first)
    backup = await folder(client, "备用")
    await add(client, "10.0.0.1:80", folder_id=child)
    await add(client, "10.0.0.2:80\n10.0.0.3:80", folder_id=backup)
    ids = await add(client, "10.0.0.4:80\n10.0.0.5:80")
    await passed(
        database,
        {
            "10.0.0.1": "203.0.113.1",
            "10.0.0.2": "203.0.113.1",
            "10.0.0.3": "203.0.113.3",
            "10.0.0.4": "203.0.113.4",
            "10.0.0.5": "203.0.113.5",
        },
    )
    targets = [
        {"kind": "folder", "id": backup, "priority": 3},
        {"kind": "folder", "id": first, "priority": 1},
        {"kind": "proxy", "id": ids["10.0.0.4"], "priority": 2},
    ]
    await use_pool(client, targets=targets, line_limit=1)
    configured = (await card(client))["rule"]["targets"]
    assert [item["label"] for item in configured] == ["备用", "主力", "10.0.0.4:80"]
    replies = [await lease(service, device) for device in ("a", "b", "c", "d")]
    assert [line_of(reply) for reply in replies] == [
        "203.0.113.1",
        "203.0.113.4",
        "203.0.113.3",
        None,
    ]
    assert replies[0]["lease"]["route"]["host"] == "10.0.0.1"


async def test_weight_is_shared_by_free_lines_in_each_source(
    client, service, database, monkeypatch
):
    first = await folder(client, "主力")
    child = await folder(client, "子组", first)
    await add(client, "10.0.0.1:80\n10.0.0.2:80", folder_id=first)
    ids = await add(client, "10.0.0.3:80\n10.0.0.4:80", folder_id=child)
    await passed(database, {f"10.0.0.{n}": f"203.0.113.{n}" for n in range(1, 5)})
    targets = [
        {"kind": "folder", "id": first, "weight": 6},
        {"kind": "folder", "id": child, "weight": 2},
        {"kind": "proxy", "id": ids["10.0.0.4"], "weight": 1},
    ]
    await use_pool(client, targets=targets, mode="weighted", line_limit=1)
    calls = []

    def choose(keys, weights, *, k):
        assert k == 1
        calls.append(dict(zip(keys, weights, strict=True)))
        return ["203.0.113.1"] if "203.0.113.1" in keys else ["203.0.113.2"]

    monkeypatch.setattr("scheduler_center.modules.proxy.dispatcher.choices", choose)
    assert line_of(await lease(service)) == "203.0.113.1"
    assert line_of(await lease(service)) == "203.0.113.2"
    assert calls == [
        {"203.0.113.1": 3, "203.0.113.2": 3, "203.0.113.3": 2, "203.0.113.4": 1},
        {"203.0.113.2": 6, "203.0.113.3": 2, "203.0.113.4": 1},
    ]


async def test_device_targets_stay_restricted_when_a_selected_folder_is_deleted(
    client, service, database
):
    selected = await folder(client, "专用")
    await add(client, "10.0.0.1:80", folder_id=selected)
    await add(client, "10.0.0.2:80")
    await passed(database, {"10.0.0.1": "203.0.113.1", "10.0.0.2": "203.0.113.2"})
    await use_pool(client)
    path = f"/api/proxy-uses/{DOWNLOAD}/devices/other"
    targets = [{"kind": "folder", "id": selected}]
    response = await client.put(path, json={"source": "pool", "targets": targets})
    assert response.is_success
    assert line_of(await lease(service, "other")) == "203.0.113.1"
    assert (await client.delete(f"/api/proxy-folders/{selected}")).status_code == 204
    waiting = await lease(service, "other")
    assert (waiting["lease"], waiting["reason"]) == (None, "规则里没有可用的代理")
    assert (await client.put(path, json={"source": "pool", "targets": targets})).status_code == 422
    assert (
        await client.put(path, json={"source": "pool", "targets": targets * 2})
    ).status_code == 422


async def test_retries_avoid_failed_lines_unless_none_is_left(client, service, database):
    await lines(client, database, 2)
    await use_pool(client)

    assert line_of(await lease(service, avoid=["203.0.113.1"])) == "203.0.113.2"
    assert line_of(await lease(service, avoid=["203.0.113.1", "203.0.113.2"])) == "203.0.113.1"


async def test_suspect_results_cool_a_line_and_a_probe_restores_it(client, service, database):
    await lines(client, database, 2)
    await use_pool(client)

    await give_back(service, await lease(service), "suspect")
    await give_back(service, await lease(service), "normal")  # 成功会把分数拉回去
    for _ in range(2):
        await give_back(service, await lease(service), "suspect", detail="连接平台超时")
    assert (await line(database, "203.0.113.1")).score == 2.5
    await give_back(service, await lease(service), "suspect", detail="连接平台超时")

    cooled = await line(database, "203.0.113.1")
    assert (cooled.backoff, cooled.reason) == (60, "连接平台超时")
    assert line_of(await lease(service, "crawler-2")) == "203.0.113.2"  # 其他设备也避开它

    await expire_cooldowns(database)
    probe = await lease(service)
    assert (line_of(probe), probe["lease"]["probe"]) == ("203.0.113.1", True)
    assert line_of(await lease(service)) == "203.0.113.2"  # 试探期间只放一个
    await give_back(service, probe)
    restored = await line(database, "203.0.113.1")
    assert (restored.backoff, restored.cooldown_until, restored.score) == (0, None, 0)


async def test_failed_probe_doubles_the_cooldown(client, service, database):
    await lines(client, database, 2)
    await use_pool(client)
    for _ in range(3):
        await give_back(service, await lease(service), "suspect")

    await expire_cooldowns(database)
    await give_back(service, await lease(service), "suspect")

    cooled = await line(database, "203.0.113.1")
    assert cooled.backoff == 120
    assert timedelta(seconds=90) < cooled.cooldown_until - utcnow() < timedelta(seconds=150)


async def test_when_every_line_is_cooling_one_is_probed_early_once_a_minute(
    client, service, database
):
    await lines(client, database, 1)
    await use_pool(client)
    for _ in range(3):
        await give_back(service, await lease(service), "suspect")

    waiting = await lease(service)
    assert (waiting["lease"], waiting["reason"]) == (None, "线路都在冷却或限流")
    assert 5 <= waiting["retry_after"] <= 60

    async with database.transaction() as session:
        await session.execute(update(proxy_lines).values(probe_at=utcnow() - timedelta(seconds=61)))
    early = await lease(service)
    assert early["lease"]["probe"] is True
    await give_back(service, early, "suspect")
    assert (await line(database, "203.0.113.1")).backoff == 120
    assert (await lease(service))["lease"] is None


async def test_throttling_rests_one_line_while_others_carry_on(client, service, database):
    await lines(client, database, 2)
    await use_pool(client)

    await give_back(service, await lease(service), "throttled", retry_after=120)
    throttled = await line(database, "203.0.113.1")
    assert throttled.backoff == 0
    assert timedelta(seconds=110) < throttled.throttled_until - utcnow() <= timedelta(seconds=120)
    assert line_of(await lease(service)) == "203.0.113.2"

    states = {item["key"]: item["state"] for item in (await card(client))["lines"]}
    assert states["203.0.113.1"] == "throttled"


async def test_an_older_lease_reporting_normal_does_not_lift_a_throttle(client, service, database):
    await lines(client, database, 1)
    await use_pool(client)
    held = await lease(service)
    await give_back(service, await lease(service), "throttled", retry_after=600)
    await give_back(service, held)
    assert (await lease(service))["lease"] is None
    assert (await card(client))["lines"][0]["state"] == "throttled"


async def test_a_throttle_holds_the_line_even_from_probes(client, service, database):
    await lines(client, database, 1)
    await use_pool(client)
    for _ in range(3):
        await give_back(service, await lease(service), "suspect")
    async with database.transaction() as session:  # 冷却到期，提前试探的间隔也早已过去
        long_ago = utcnow() - timedelta(seconds=61)
        await session.execute(
            update(proxy_lines).values(cooldown_until=long_ago, probe_at=long_ago)
        )
    probe = await lease(service)
    assert probe["lease"]["probe"] is True
    await give_back(service, probe, "throttled", retry_after=600)

    waiting = await lease(service)  # 限流期内，普通申请和提前试探都不放
    assert (waiting["lease"], waiting["reason"], waiting["retry_after"]) == (
        None,
        "线路都在冷却或限流",
        60,
    )
    assert (await card(client))["lines"][0]["state"] == "throttled"

    async with database.transaction() as session:  # 限流到期：回到冷却到期后的试探，只放一个
        await session.execute(update(proxy_lines).values(throttled_until=utcnow()))
    assert (await lease(service))["lease"]["probe"] is True
    assert (await lease(service))["lease"] is None


async def test_proxy_failures_stay_with_the_proxy(client, service, database):
    ids = await lines(client, database, 2)
    await use_pool(client)

    await give_back(service, await lease(service), "proxy_down", detail="连不上代理")
    down = await load(database, Proxy, UUID(ids["10.0.0.1"]))
    assert (down.status, down.error, down.backoff) == (ProxyStatus.UNREACHABLE, "连不上代理", 60)
    assert timedelta(seconds=45) < down.test_at - utcnow() < timedelta(seconds=75)
    assert (await line(database, "203.0.113.1")).cooldown_until is None  # 出口本身没问题

    second = await lease(service)
    assert line_of(second) == "203.0.113.2"
    await give_back(service, second, "auth_failed", detail="407")
    assert (await load(database, Proxy, UUID(ids["10.0.0.2"]))).test_at is None
    assert "1 个代理认证失败，更新凭据后自动恢复" in (await client.get("/api/proxy-alerts")).json()

    waiting = await lease(service)
    assert (waiting["lease"], waiting["reason"]) == (None, "规则里没有可用的代理")
    fixed = await client.patch(f"/api/proxies/{ids['10.0.0.2']}", json={"password": "new"})
    assert (fixed.json()["status"], fixed.json()["testing"]) == ("untested", True)


async def test_leases_expire_unless_the_heartbeat_renews_them(client, service, database):
    await lines(client, database, 1)
    await use_pool(client, line_limit=1)
    held = await lease(service)
    assert (await lease(service))["lease"] is None

    async with database.transaction() as session:
        await session.execute(
            update(proxy_leases).values(expires_at=utcnow() + timedelta(seconds=5))
        )
    heartbeat = {"url": CRAWLER_URL, "version": "1", "leases": [held["lease"]["lease_id"]]}
    assert (await service.put(f"/api/crawler-services/{DEVICE}", json=heartbeat)).is_success
    renewed = await load(database, Lease, UUID(held["lease"]["lease_id"]))
    assert renewed.expires_at - utcnow() > timedelta(seconds=55)

    async with database.transaction() as session:
        await session.execute(update(proxy_leases).values(expires_at=utcnow()))
    assert line_of(await lease(service)) == "203.0.113.1"  # 设备没再心跳，名额收回


async def test_a_late_heartbeat_cannot_revive_an_expired_lease(client, service, database):
    await lines(client, database, 1)
    await use_pool(client, line_limit=1)
    stale = await lease(service)
    async with database.transaction() as session:  # 超过 60 秒没续期
        await session.execute(
            update(proxy_leases).values(expires_at=utcnow() - timedelta(seconds=1))
        )

    heartbeat = {"url": CRAWLER_URL, "version": "1", "leases": [stale["lease"]["lease_id"]]}
    late, taken = await asyncio.gather(  # 迟到的心跳和另一台设备的申请同时到
        service.put(f"/api/crawler-services/{DEVICE}", json=heartbeat),
        lease(service, "crawler-2"),
    )
    assert late.is_success and line_of(taken) == "203.0.113.1"
    assert (await service.put(f"/api/crawler-services/{DEVICE}", json=heartbeat)).is_success
    assert (await card(client))["lines"][0]["in_use"] == 1
    assert (await lease(service))["lease"] is None


async def test_a_device_exception_overrides_the_cluster_rule(client, service, database):
    japan = await folder(client, "日本")
    ids = await lines(client, database, 2)
    await client.post("/api/proxies/move", json={"ids": [ids["10.0.0.2"]], "folder_id": japan})
    await use_pool(client)
    path = f"/api/proxy-uses/{DOWNLOAD}/devices"
    local = {"source": "local_port", "local_port": 10808}
    assert (await client.put(f"{path}/crawler-2", json=local)).is_success
    assert (await client.put(f"{path}/crawler-3", json={"source": "direct"})).is_success
    only_japan = {"source": "pool", "folder_id": japan}
    assert (await client.put(f"{path}/crawler-4", json=only_japan)).is_success

    own = await lease(service, "crawler-2")
    assert (line_of(own), own["lease"]["route"]) == (
        "port:crawler-2:10808",
        {**own["lease"]["route"], "kind": "local_port", "port": 10808, "host": None},
    )
    assert (await lease(service, "crawler-3"))["lease"]["route"]["kind"] == "direct"
    assert line_of(await lease(service, "crawler-4")) == "203.0.113.2"
    assert line_of(await lease(service, "crawler-1")) == "203.0.113.2"  # 没放文件夹的排最后
    assert (await lease(service, "crawler-2", use=CRAWL))["lease"]["route"]["kind"] == "direct"

    devices = {item["instance_id"]: item for item in (await card(client))["devices"]}
    assert devices["crawler-2"]["rule"]["local_port"] == 10808
    assert devices["crawler-2"]["line"]["key"] == "port:crawler-2:10808"
    assert devices["crawler-2"]["in_use"] == 1

    await client.delete(f"{path}/crawler-2")
    assert line_of(await lease(service, "crawler-2")) == "203.0.113.1"  # 日本那条已占满
    rejected = await client.put(f"/api/proxy-uses/{DOWNLOAD}/rule", json=local)
    assert rejected.status_code == 422


async def test_folders_nest_and_deleting_one_keeps_its_contents(client, service, database):
    asia = await folder(client, "亚洲")
    japan = await folder(client, "日本", asia)
    await add(client, "10.0.0.1:80", folder_id=japan)
    await add(client, "10.0.0.2:80")
    twin = await client.post("/api/proxy-folders", json={"name": "日本", "parent_id": asia})
    assert twin.status_code == 409
    into_self = {"parent_id": japan, "position": 0}
    assert (await client.post(f"/api/proxy-folders/{asia}/move", json=into_self)).status_code == 422

    in_asia = await client.get("/api/proxies", params={"folder_id": asia})
    assert [item["host"] for item in in_asia.json()["items"]] == ["10.0.0.1"]  # 含子文件夹
    unfiled = await client.get("/api/proxies", params={"unfiled": True})
    assert [item["host"] for item in unfiled.json()["items"]] == ["10.0.0.2"]

    await passed(database, {"10.0.0.1": "203.0.113.1"})
    await use_pool(client, folder_id=japan)
    assert (await client.delete(f"/api/proxy-folders/{japan}")).status_code == 204
    folders = (await client.get("/api/proxy-folders")).json()
    assert [(item["name"], item["count"]) for item in folders["folders"]] == [("亚洲", 1)]
    assert folders["unfiled"] == 1
    assert (await card(client))["rule"]["folder_id"] == asia  # 规则改用上一级
    assert line_of(await lease(service)) == "203.0.113.1"


async def test_tags_and_folders_change_in_batches(client, database):
    ids = await add(client, "10.0.0.1:80\n10.0.0.2:80", uses=[DOWNLOAD])
    everything = list(ids.values())

    tagged = await client.post("/api/proxies/uses", json={"ids": everything, "add": [CRAWL]})
    assert tagged.json() == {"count": 2}
    narrowed = {"ids": [ids["10.0.0.1"]], "remove": [DOWNLOAD]}
    await client.post("/api/proxies/uses", json=narrowed)
    served = await client.get("/api/proxies", params={"use": DOWNLOAD})
    assert [item["host"] for item in served.json()["items"]] == ["10.0.0.2"]
    whole = {"ids": everything, "add": ["crawler"]}
    await client.post("/api/proxies/uses", json=whole)
    items = (await client.get("/api/proxies")).json()["items"]
    assert {tuple(item["uses"]) for item in items} == {("crawler",)}

    us = await folder(client, "美国")
    moved = await client.post("/api/proxies/move", json={"ids": everything, "folder_id": us})
    assert moved.json() == {"count": 2}
    assert (await client.get("/api/proxy-folders")).json()["folders"][0]["count"] == 2


async def test_worker_asks_online_devices_to_test_due_proxies(
    client, database, database_url, tmp_path, executors
):
    ids = await add(client, "10.0.0.1:80\n10.0.0.2:80\n10.0.0.3:80\n10.0.0.4:80")
    worker = build_worker(database, database_url, tmp_path, executors)
    assert await worker.proxies.test_due() == 0  # 没有在线设备，代理等着

    await register(database, ExecutorKind.CRAWLER, CRAWLER_URL)
    replies = [
        {"key": ids["10.0.0.1"], "result": "ok", "protocol": "socks5", "exit_ip": "203.0.113.5"},
        {"key": ids["10.0.0.2"], "result": "auth_failed", "error": "407"},
        {"key": ids["10.0.0.3"], "result": "unreachable", "error": "连接超时"},
    ]
    executors.responses[("POST", "/api/proxy-tests")] = httpx.Response(200, json=replies)
    assert await worker.proxies.test_due() == 4

    sent = json.loads(executors.requests[-1].content)
    assert sent["echo_url"] == "https://api64.ipify.org"
    assert {item["host"] for item in sent["items"]} == {
        "10.0.0.1",
        "10.0.0.2",
        "10.0.0.3",
        "10.0.0.4",
    }
    found = {proxy.host: proxy for proxy in await all_proxies(database)}
    ok = found["10.0.0.1"]
    assert (ok.status, ok.protocol, ok.line, ok.tested_by) == (
        ProxyStatus.AVAILABLE,
        "socks5",
        "203.0.113.5",
        "crawler-1",
    )
    assert (found["10.0.0.2"].status, found["10.0.0.2"].test_at) == (ProxyStatus.AUTH_FAILED, None)
    assert found["10.0.0.3"].status == ProxyStatus.UNREACHABLE
    assert found["10.0.0.3"].test_at > utcnow()
    assert found["10.0.0.4"].test_at > utcnow()  # 设备没回结果，稍后再测
    assert await worker.proxies.test_due() == 0


def ok(proxy_id: str, exit_ip: str) -> httpx.Response:
    return httpx.Response(200, json=[{"key": proxy_id, "result": "ok", "exit_ip": exit_ip}])


async def test_an_online_pool_can_add_and_switch_proxies_with_active_leases(
    client, service, database, database_url, tmp_path, executors
):
    await lines(client, database, 1)
    await use_pool(client, line_limit=1)
    held = await lease(service)
    assert line_of(held) == "203.0.113.1"

    new = await client.post(
        "/api/proxies", json={"host": "10.0.0.2", "port": 80, "uses": [DOWNLOAD]}
    )
    assert new.status_code == 201
    proxy_id = new.json()["id"]
    assert (await lease(service))["lease"] is None  # 新代理测试通过之前不能分配
    await register(database, ExecutorKind.CRAWLER, CRAWLER_URL)
    worker = build_worker(database, database_url, tmp_path, executors)
    executors.responses["POST", "/api/proxy-tests"] = ok(proxy_id, "203.0.113.2")
    assert await worker.proxies.test_due() == 1
    assert line_of(await lease(service)) == "203.0.113.2"

    await use_pool(client, line_limit=2, targets=[{"kind": "proxy", "id": proxy_id}])
    await give_back(service, held)
    assert line_of(await lease(service)) == "203.0.113.2"


async def test_old_leases_do_not_override_a_proxy_credential_update(
    client, service, database, database_url, tmp_path, executors
):
    proxy_id = (await add(client, "10.0.0.1:80:alice:old"))["10.0.0.1"]
    await passed(database, {"10.0.0.1": "203.0.113.1"})
    await use_pool(client, line_limit=2)
    first, second = await lease(service), await lease(service)

    edited = await client.patch(f"/api/proxies/{proxy_id}", json={"password": "new"})
    assert edited.json()["status"] == "untested"
    await give_back(service, first, "auth_failed")
    waiting = await load(database, Proxy, UUID(proxy_id))
    assert waiting.status == ProxyStatus.UNTESTED and waiting.test_at is not None

    await register(database, ExecutorKind.CRAWLER, CRAWLER_URL)
    worker = build_worker(database, database_url, tmp_path, executors)
    executors.responses["POST", "/api/proxy-tests"] = ok(proxy_id, "203.0.113.1")
    assert await worker.proxies.test_due() == 1
    assert (await lease(service))["lease"]["route"]["password"] == "new"

    await give_back(service, second, "proxy_down")
    assert (await load(database, Proxy, UUID(proxy_id))).status == ProxyStatus.AVAILABLE
    assert line_of(await lease(service)) == "203.0.113.1"


async def test_proxy_list_filters_enabled_and_disabled_records(client):
    ids = await add(client, "10.0.0.1:80\n10.0.0.2:80")
    disabled = await client.post("/api/proxies/disable", json={"ids": [ids["10.0.0.2"]]})
    assert disabled.raise_for_status().json() == {"count": 1}

    for params, expected in (
        ({"enabled": "true"}, {ids["10.0.0.1"]}),
        ({"enabled": "false"}, {ids["10.0.0.2"]}),
        ({}, set(ids.values())),
    ):
        response = await client.get("/api/proxies", params=params)
        payload = response.raise_for_status().json()
        assert payload["total"] == len(expected)
        assert {item["id"] for item in payload["items"]} == expected


async def test_a_pending_lease_does_not_take_a_proxy_disabled_during_selection(
    client, service, database, monkeypatch
):
    proxy_id = (await lines(client, database, 1))["10.0.0.1"]
    await use_pool(client)
    selected, resume = asyncio.Event(), asyncio.Event()
    original = Dispatcher.candidates

    async def read_candidates(dispatcher, *args):
        result = await original(dispatcher, *args)
        selected.set()
        await resume.wait()
        return result

    monkeypatch.setattr(Dispatcher, "candidates", read_candidates)
    pending = asyncio.create_task(lease(service))
    try:
        await asyncio.wait_for(selected.wait(), 3)
        disabled = await client.post("/api/proxies/disable", json={"ids": [proxy_id]})
        assert disabled.json() == {"count": 1}
    finally:
        resume.set()
        reply = await pending
    assert reply["lease"] is None


async def test_a_late_result_for_the_old_address_is_ignored(
    client, database, database_url, tmp_path, executors, monkeypatch
):
    proxy_id = (await add(client, "10.0.0.1:80"))["10.0.0.1"]
    await register(database, ExecutorKind.CRAWLER, CRAWLER_URL)
    worker = build_worker(database, database_url, tmp_path, executors)
    tested = worker.proxies._run

    async def edited_meanwhile(device, items):  # 设备测旧地址期间，页面上改了地址
        replies = await tested(device, items)
        await client.patch(f"/api/proxies/{proxy_id}", json={"host": "10.0.0.9"})
        return replies

    monkeypatch.setattr(worker.proxies, "_run", edited_meanwhile)
    executors.responses[("POST", "/api/proxy-tests")] = ok(proxy_id, "203.0.113.1")
    assert await worker.proxies.test_due() == 1
    edited = (await client.get("/api/proxies")).json()["items"][0]
    assert (edited["status"], edited["exit_ip"], edited["testing"]) == ("untested", None, True)

    monkeypatch.undo()
    executors.responses[("POST", "/api/proxy-tests")] = ok(proxy_id, "203.0.113.9")
    assert await worker.proxies.test_due() == 1  # 新地址另测，通过后才能分配
    sent = json.loads(executors.requests[-1].content)
    assert sent["items"][0]["host"] == "10.0.0.9"
    fresh = (await client.get("/api/proxies")).json()["items"][0]
    assert (fresh["status"], fresh["exit_ip"]) == ("available", "203.0.113.9")


async def test_an_interrupted_test_is_claimed_again(
    client, database, database_url, tmp_path, executors, monkeypatch
):
    proxy_id = (await add(client, "10.0.0.1:80"))["10.0.0.1"]
    await register(database, ExecutorKind.CRAWLER, CRAWLER_URL)
    worker = build_worker(database, database_url, tmp_path, executors)

    async def interrupted(device, items):
        raise RuntimeError("Worker 中途退出")

    monkeypatch.setattr(worker.proxies, "_run", interrupted)
    with pytest.raises(RuntimeError):
        await worker.proxies.test_due()
    monkeypatch.undo()
    assert await worker.proxies.test_due() == 0  # 还在领取期内
    assert (await client.get("/api/proxies")).json()["items"][0]["testing"]

    async with database.transaction() as session:  # 领取到期
        await session.execute(update(proxies).values(testing_until=utcnow()))
    executors.responses[("POST", "/api/proxy-tests")] = ok(proxy_id, "203.0.113.1")
    assert await worker.proxies.test_due() == 1
    assert (await client.get("/api/proxies")).json()["items"][0]["status"] == "available"


async def test_an_echo_that_is_not_an_ip_fails_only_its_own_proxy(
    client, database, database_url, tmp_path, executors
):
    ids = await add(client, "10.0.0.1:80\n10.0.0.2:80")
    await register(database, ExecutorKind.CRAWLER, CRAWLER_URL)
    worker = build_worker(database, database_url, tmp_path, executors)
    replies = [
        {"key": ids["10.0.0.1"], "result": "ok", "exit_ip": "203.0.113.1"},
        {"key": ids["10.0.0.2"], "result": "ok", "exit_ip": "<html>blocked</html>"},
    ]
    executors.responses[("POST", "/api/proxy-tests")] = httpx.Response(200, json=replies)
    assert await worker.proxies.test_due() == 2

    found = {proxy.host: proxy for proxy in await all_proxies(database)}
    good, bad = found["10.0.0.1"], found["10.0.0.2"]
    assert (good.status, good.line) == (ProxyStatus.AVAILABLE, "203.0.113.1")
    assert (bad.status, bad.exit_ip) == (ProxyStatus.UNTESTED, None)
    assert bad.error.startswith("回显内容不是 IP 地址") and bad.test_at > utcnow()


async def test_speed_tests_go_to_a_device_one_proxy_at_a_time(
    client, database, database_url, tmp_path, executors
):
    await lines(client, database, 2)
    ids = await add(client, "10.0.0.9:80")
    body = {"ids": [*ids.values()]}
    assert (await client.post("/api/proxies/speed", json=body)).json() == {
        "count": 2
    }  # 没测试通过的不测速
    await register(database, ExecutorKind.CRAWLER, CRAWLER_URL)
    worker = build_worker(database, database_url, tmp_path, executors)

    fast = {"key": "", "single_mbps": 18.9, "parallel_mbps": 32.1}
    executors.responses[("POST", "/api/proxy-speed")] = httpx.Response(200, json=fast)
    assert await worker.proxies.speed_due() == 1  # 一台设备一次只测一个，免得互相抢带宽
    first = json.loads(executors.requests[-1].content)
    queued = (await client.get("/api/proxies")).json()["items"]
    assert sum(item["speed_testing"] for item in queued) == 1  # 另一个还在排队
    assert (first["url"], first["size"], first["streams"]) == (
        "https://speed.cloudflare.com/__down?bytes=10000000",
        10_000_000,
        4,
    )

    blocked = {"key": "", "error": "测速地址返回 403"}
    executors.responses[("POST", "/api/proxy-speed")] = httpx.Response(200, json=blocked)
    assert await worker.proxies.speed_due() == 1
    second = json.loads(executors.requests[-1].content)
    assert await worker.proxies.speed_due() == 0

    items = {item["id"]: item for item in (await client.get("/api/proxies")).json()["items"]}
    measured, failed = items[first["item"]["key"]], items[second["item"]["key"]]
    assert (measured["speed_mbps"], measured["parallel_mbps"]) == (18.9, 32.1)
    assert (failed["speed_mbps"], failed["speed_error"]) == (None, "测速地址返回 403")
    assert failed["status"] == "available"  # 测速失败不改代理状态
    assert not any(item["speed_testing"] for item in items.values())


async def test_a_dynamic_proxy_refreshes_only_after_its_leases_return(
    client, service, database, database_url, tmp_path, executors
):
    body = {
        "host": "10.0.0.1",
        "port": 80,
        "refresh_url": "https://vendor.test/refresh?key=k",
        "uses": [DOWNLOAD],
    }
    proxy = (await client.post("/api/proxies", json=body)).json()
    assert proxy["dynamic"] and "refresh_url" not in proxy
    await passed(database, {"10.0.0.1": "203.0.113.1"})
    await use_pool(client)
    await register(database, ExecutorKind.CRAWLER, CRAWLER_URL)
    worker = build_worker(database, database_url, tmp_path, executors)
    held = await lease(service)

    assert (await client.post("/api/proxies/refresh", json={"ids": [proxy["id"]]})).json() == {
        "count": 1
    }
    assert (await lease(service))["reason"] == "规则里没有可用的代理"
    assert await worker.proxies.test_due() == 0  # 先排空

    await give_back(service, held, "auth_failed")  # 旧连接失败不能撤销已安排的刷新
    reply = [{"key": proxy["id"], "result": "ok", "exit_ip": "203.0.113.2"}]
    executors.responses[("POST", "/api/proxy-tests")] = httpx.Response(200, json=reply)
    assert await worker.proxies.test_due() == 1
    sent = json.loads(executors.requests[-1].content)
    assert sent["items"][0]["refresh_url"] == "https://vendor.test/refresh?key=k"
    assert line_of(await lease(service)) == "203.0.113.2"


async def test_a_running_connectivity_test_does_not_cancel_a_new_exit_refresh(
    client, service, database, database_url, tmp_path, executors, monkeypatch
):
    body = {
        "host": "10.0.0.1",
        "port": 80,
        "refresh_url": "https://vendor.test/refresh",
        "uses": [DOWNLOAD],
    }
    proxy_id = (await client.post("/api/proxies", json=body)).json()["id"]
    await passed(database, {"10.0.0.1": "203.0.113.1"})
    await use_pool(client)
    held = await lease(service)
    await register(database, ExecutorKind.CRAWLER, CRAWLER_URL)
    worker = build_worker(database, database_url, tmp_path, executors)
    tested = worker.proxies._run
    ids = {"ids": [proxy_id]}

    async def refreshed_meanwhile(device, items):
        result = await tested(device, items)
        assert (await client.post("/api/proxies/refresh", json=ids)).is_success
        return result

    with monkeypatch.context() as patch:
        patch.setattr(worker.proxies, "_run", refreshed_meanwhile)
        executors.responses["POST", "/api/proxy-tests"] = ok(proxy_id, "203.0.113.1")
        assert (await client.post("/api/proxies/test", json=ids)).is_success
        assert await worker.proxies.test_due() == 1

    assert (await load(database, Proxy, UUID(proxy_id))).status == ProxyStatus.REFRESHING
    assert await worker.proxies.test_due() == 0
    await give_back(service, held)
    executors.responses["POST", "/api/proxy-tests"] = ok(proxy_id, "203.0.113.2")
    assert await worker.proxies.test_due() == 1
    assert line_of(await lease(service)) == "203.0.113.2"


async def test_a_device_tests_its_own_local_port(client, database, executors):
    await register(database, ExecutorKind.CRAWLER, CRAWLER_URL)
    path = f"/api/proxy-uses/{DOWNLOAD}/devices/{DEVICE}"
    assert (await client.post(f"{path}/test")).status_code == 422

    await client.put(path, json={"source": "local_port", "local_port": 10808})
    reply = [{"key": "port", "result": "ok", "exit_ip": "198.51.100.7"}]
    executors.responses[("POST", "/api/proxy-tests")] = httpx.Response(200, json=reply)
    tested_line = (await client.post(f"{path}/test")).json()

    assert (tested_line["kind"], tested_line["exit_ip"]) == ("port", "198.51.100.7")
    sent = json.loads(executors.requests[-1].content)["items"][0]
    assert (sent["host"], sent["port"]) == (None, 10808)


async def test_alerts_point_at_what_needs_a_person(client, service, database):
    await add(client, "10.0.0.1:80")
    assert (await client.get("/api/proxy-alerts")).json() == [
        "没有在线的爬取程序，新添加的代理暂时无法测试"
    ]
    await register(database, ExecutorKind.CRAWLER, CRAWLER_URL)
    await use_pool(client)
    assert (await client.get("/api/proxy-alerts")).json() == [
        "下载视频：设备 crawler-1 没有可用的代理，在等待"
    ]

    await passed(database, {"10.0.0.1": "203.0.113.1"})
    await lease(service)
    assert (await client.get("/api/proxy-alerts")).json() == []
    async with database.transaction() as session:  # 试探一直失败，冷却翻倍到了最长
        await session.execute(update(proxy_lines).values(backoff=1800))
    assert (await client.get("/api/proxy-alerts")).json() == [
        "下载视频：设备 crawler-1 的线路试探一直失败，冷却已到最长 30 分钟"
    ]


async def all_proxies(database) -> list[Proxy]:
    async with database.transaction() as session:
        return list(await session.scalars(select(Proxy).order_by(proxies.c.host)))


async def test_a_device_bound_pool_respects_its_limit_without_capping_direct_devices(
    client, service, database
):
    pool_id = await folder(client, "设备 B 下载")
    proxy = await client.post(
        "/api/proxies",
        json={
            "host": "127.0.0.1",
            "port": 26401,
            "protocol": "socks5",
            "uses": [DOWNLOAD],
            "folder_id": pool_id,
            "bound_device": "crawler-b",
        },
    )
    assert proxy.status_code == 201
    assert proxy.json()["bound_device"] == "crawler-b"
    await passed(database, {"127.0.0.1": "203.0.113.1"})
    await use_pool(client, line_limit=4)
    pool_rule = f"/api/proxy-uses/{DOWNLOAD}/devices/crawler-b"
    body = {"source": "pool", "folder_id": pool_id}
    assert (await client.put(pool_rule, json={**body, "line_limit": 1})).status_code == 200
    assert (await client.put(pool_rule, json=body)).json()["device_line_limit"] == 1
    assert line_of(await lease(service, "crawler-b")) == "203.0.113.1"
    assert line_of(await lease(service, "crawler-b")) is None
    assert line_of(await lease(service, "crawler-other")) is None

    route = f"/api/proxy-uses/{DOWNLOAD}/devices/crawler-a"
    assert (await client.put(route, json={"source": "direct"})).status_code == 200
    for _ in range(4):
        assert (await lease(service, "crawler-a"))["lease"]["route"]["kind"] == "direct"
    assert line_of(await lease(service, "crawler-a")) is None


async def test_connectivity_and_speed_tests_use_only_the_bound_device(
    client, database, database_url, tmp_path, executors
):
    owner = "crawler-b"
    response = await client.post(
        "/api/proxies",
        json={"host": "127.0.0.1", "port": 26401, "bound_device": owner},
    )
    assert response.status_code == 201
    proxy_id = response.json()["id"]
    await register(database, ExecutorKind.CRAWLER, CRAWLER_URL)
    worker = build_worker(database, database_url, tmp_path, executors)
    assert await worker.proxies.test_due() == 0
    await register(database, ExecutorKind.CRAWLER, "http://crawler-b.test", instance_id=owner)
    executors.responses["POST", "/api/proxy-tests"] = ok(proxy_id, "203.0.113.1")
    assert await worker.proxies.test_due() == 1
    assert executors.requests[-1].url.host == "crawler-b.test"

    assert (await client.post("/api/proxies/speed", json={"ids": [proxy_id]})).status_code == 200
    executors.responses["POST", "/api/proxy-speed"] = httpx.Response(
        200, json={"key": proxy_id, "single_mbps": 18.9}
    )
    assert await worker.proxies.speed_due() == 1
    assert executors.requests[-1].url.host == "crawler-b.test"

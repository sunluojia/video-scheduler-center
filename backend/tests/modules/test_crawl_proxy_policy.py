import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from scheduler_center.core.common.clock import utcnow
from scheduler_center.modules.proxy import dispatcher
from scheduler_center.modules.proxy.models import CrawlLinePolicy, Lease, Line, Proxy, Use
from scheduler_center.modules.proxy.repository import LeaseRepository
from tests.factories import api_settings, load, new_batch, video
from tests.modules.test_attribution import claim, crawler_result, owner
from tests.modules.test_proxy import add, folder, give_back, line_of, lines, passed, use_pool

A, B, EPOCH = "device-a", "device-b", "crawl-test"
FIRST_EXIT = "203.0.113.1"


@pytest.fixture
def legacy_crawler_dispatch():
    return False


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = utcnow()

        def advance(self, seconds):
            self.now += timedelta(seconds=seconds)

    clock = Clock()
    monkeypatch.setattr(dispatcher, "utcnow", lambda: clock.now)
    return clock


def enable(app, keys=(FIRST_EXIT,), **settings):
    app.state.settings = api_settings(
        **{
            "crawl_policy_enabled": True,
            "crawl_policy_devices": (A,),
            "crawl_policy_exits": keys,
            "crawl_policy_epoch": EPOCH,
            **settings,
        }
    )


async def pool(client, database, count=1, *, limit=1):
    await lines(client, database, count, uses=[Use.CRAWL])
    await use_pool(client, Use.CRAWL, line_limit=limit, mode="rotate")


async def active_tasks(database, service, count=1):
    identity = owner(A)
    await new_batch(database, *(f"creator{i}" for i in range(count)), legacy_crawler_dispatch=False)
    return await claim(service, identity, crawl=count)


async def request(service, task=None, *, device=A, use=Use.CRAWL, bootstrap=False):
    body = {"device": device, "use": use, "warm_bootstrap": bootstrap}
    if task:
        body.update(task_id=task["task_id"], attempt_id=task["attempt_id"])
    return (await service.post("/api/proxy-leases", json=body)).raise_for_status().json()


async def check_wait(service, task, seconds, code="crawl_empty_cooldown", **kwargs):
    reply = await request(service, task, **kwargs)
    assert (reply["lease"], reply["retry_after"], reply["reason_code"]) == (None, seconds, code)
    assert reply["reason"]


async def report_empty(service, grant):
    assert grant["lease"]
    await give_back(service, grant, "suspect", listing_result="empty_response")


async def policy_row(database, key=FIRST_EXIT, epoch=EPOCH):
    return await load(database, CrawlLinePolicy, (epoch, key))


async def test_six_exits_and_aliases_share_45_second_pacing(app, client, service, database, clock):
    await pool(client, database, count=6, limit=3)
    await add(client, "10.0.0.9:80", [Use.CRAWL])
    await passed(database, {"10.0.0.9": FIRST_EXIT})
    (task,) = await active_tasks(database, service)
    enable(app, tuple(f"203.0.113.{i}" for i in range(1, 7)))

    first = [await request(service, task) for _ in range(6)]
    assert {line_of(grant) for grant in first} == set(app.state.settings.crawl_policy_exits)
    await check_wait(service, task, 45, "crawl_paced")
    clock.advance(6.25)
    for grant in first:
        await give_back(service, grant)
    clock.advance(44.75)
    await check_wait(service, task, 1, "crawl_paced")
    clock.advance(0.25)
    second = [await request(service, task) for _ in range(6)]
    assert {line_of(grant) for grant in second} == {line_of(grant) for grant in first}
    await check_wait(service, task, 45, "crawl_paced")
    assert 6 * 3600 // app.state.settings.crawl_pacing_min_seconds == 480


async def test_empty_backoff_keeps_its_stages_and_only_complete_recovers(
    app,
    client,
    service,
    database,
    clock,
):
    await pool(client, database, limit=2)
    tasks = await active_tasks(database, service, 3)
    enable(app)
    await report_empty(service, await request(service, tasks[0]))
    clock.advance(45)
    await report_empty(service, await request(service, tasks[1]))
    clock.advance(45)
    warm = await request(service)
    await report_empty(service, await request(service, tasks[2]))
    await give_back(service, warm, listing_result="complete")
    assert (await policy_row(database)).empty_step == 1
    await check_wait(service, tasks[0], 60)
    await check_wait(service, None, 60, "warm_empty_wait", bootstrap=True)
    clock.advance(60)
    await check_wait(service, None, 5, "warm_empty_wait")
    warm = await request(service, bootstrap=True)
    assert warm["lease"]
    await give_back(service, warm)
    state = await policy_row(database)
    assert state.empty_step == 1 and state.probe_lease_id is None
    await check_wait(service, None, 90, "warm_empty_wait", bootstrap=True)

    probe = await request(service, tasks[0])
    assert probe["lease"]
    await give_back(service, probe)  # NORMAL without COMPLETE keeps the first step.
    await check_wait(service, tasks[0], 60)
    clock.advance(60)
    for step, seconds in ((2, 180), (3, 360), (4, 600), (4, 600)):
        await report_empty(service, await request(service, tasks[0]))
        assert (await policy_row(database)).empty_step == step
        await check_wait(service, tasks[0], seconds)
        # The legacy early probe can bypass this generic COOLDOWN, but not the empty hold.
        if step == 2:
            async with database.transaction() as session:
                generic = await session.get(Line, (Use.CRAWL, FIRST_EXIT))
                generic.backoff = 120
                generic.probe_at = clock.now - timedelta(seconds=90)
                generic.cooldown_until = clock.now + timedelta(seconds=90)
            await check_wait(service, tasks[0], seconds)
            await check_wait(service, None, seconds, "warm_empty_wait", bootstrap=True)
        clock.advance(seconds)
    recovered = await request(service, tasks[0])
    await give_back(service, recovered, listing_result="complete")
    state = await policy_row(database)
    assert (state.empty_step, state.empty_until, state.probe_lease_id) == (0, None, None)
    await check_wait(service, tasks[1], 45, "crawl_paced")


async def test_concurrent_probe_renewal_loss_and_late_feedback(
    app, client, service, database, clock
):
    await pool(client, database, limit=6)
    tasks = await active_tasks(database, service, 2)
    enable(app, crawl_empty_tasks=1, crawl_pacing_min_seconds=0)
    await report_empty(service, await request(service, tasks[0]))
    clock.advance(60)
    results = await asyncio.wait_for(
        asyncio.gather(*(request(service, task) for task in tasks)),
        timeout=10,
    )
    [probe] = [reply for reply in results if reply["lease"]]
    [wait] = [reply for reply in results if not reply["lease"]]
    assert (wait["reason_code"], wait["retry_after"]) == ("crawl_probe_busy", 5)
    probe_id = UUID(probe["lease"]["lease_id"])
    assert (await policy_row(database)).probe_lease_id == probe_id
    clock.advance(50)
    async with database.transaction() as session:
        await LeaseRepository(session).renew(A, [probe_id], clock.now)
    clock.advance(11)
    await check_wait(service, tasks[0], 5, "crawl_probe_busy")
    clock.advance(49)
    await check_wait(service, tasks[0], 60)
    await give_back(service, probe, listing_result="complete")
    state = await policy_row(database)
    assert state.empty_step == 1 and state.probe_lease_id is None
    clock.advance(60)
    newer = await request(service, tasks[0])
    await give_back(service, probe, listing_result="complete")
    assert (await policy_row(database)).probe_lease_id == UUID(newer["lease"]["lease_id"])
    await give_back(service, newer, listing_result="complete")
    assert (await policy_row(database)).empty_step == 0


async def test_preclaim_filters_only_crawl_and_b_routes_bypass_policy(
    app,
    client,
    service,
    database,
    clock,
):
    await pool(client, database)
    address_ports = range(26501, 26507)
    media_ports = range(26401, 26411)
    for use, name, ports in (
        (Use.CRAWL, "B address", address_ports),
        (Use.DOWNLOAD, "B media", media_ports),
    ):
        group = await folder(client, name)
        exits = {}
        for i, port in enumerate(ports, 1):
            host = f"198.51.100.{i + (0 if use == Use.CRAWL else 10)}"
            exits[host] = f"203.0.113.{i}"
            added = await client.post(
                "/api/proxies",
                json={
                    "host": host,
                    "port": port,
                    "protocol": "socks5",
                    "uses": [use],
                    "folder_id": group,
                    "bound_device": B,
                },
            )
            assert added.status_code == 201
        await passed(database, exits)
        device_rule = {"source": "pool", "folder_id": group, "line_limit": 1}
        response = await client.put(f"/api/proxy-uses/{use}/devices/{B}", json=device_rule)
        assert response.is_success
    a, b = owner(A), owner(B)
    await new_batch(database, "seed1", "seed2", legacy_crawler_dispatch=False)
    for i, task in enumerate(await claim(service, b, crawl=2), 1):
        await crawler_result(
            database, task, b, "crawled", conclusion="ok", works=[video(1, f"7{i:018d}")]
        )
    (download_b,) = await claim(service, b, download=1)
    await new_batch(database, "creator1", "creator2", legacy_crawler_dispatch=False)
    enable(app, crawl_empty_tasks=1)
    (listing,) = await claim(service, a, crawl=2)
    assert await policy_row(database) is None  # Claim neither creates state nor reserves a line.
    await report_empty(service, await request(service, listing))

    response = await service.post(
        "/api/crawler-tasks/claim",
        json={
            **a,
            "kinds": {"crawl_creator": 2, "download_video": 1},
        },
    )
    payload = response.raise_for_status().json()
    (download_a,) = payload["tasks"]
    assert download_a["kind"] == "download_video"
    assert payload["crawl_wait"]["retry_after"] == 60
    assert payload["crawl_wait"]["reason_code"] == "crawl_empty_cooldown"
    address_a = await request(service, download_a)
    assert line_of(address_a) == FIRST_EXIT and address_a["lease"]["route"]["port"] == 80
    await give_back(service, address_a)
    for use, expected in ((Use.CRAWL, address_ports), (Use.DOWNLOAD, media_ports)):
        grants = [await request(service, download_b, device=B, use=use) for _ in expected]
        assert {grant["lease"]["route"]["port"] for grant in grants} == set(expected)
        for grant in grants:
            assert grant["lease"]["route"]["kind"] == "proxy" and "reason_code" not in grant
            saved = await load(database, Lease, UUID(grant["lease"]["lease_id"]))
            assert saved.crawl_policy_epoch is None
            await give_back(service, grant)
    assert (await policy_row(database)).empty_step == 1

    enable(app, crawl_policy_enabled=False, crawl_empty_tasks=1)
    assert (await request(service, listing))["lease"] is not None
    assert len(await claim(service, a, crawl=1)) == 1
    assert (await policy_row(database)).empty_step == 1


async def test_only_verified_in_window_current_revision_evidence_counts(
    app,
    client,
    service,
    database,
    clock,
):
    await pool(client, database, limit=2)
    a, b, c = await active_tasks(database, service, 3)
    enable(app, crawl_pacing_min_seconds=0)
    for _ in range(3):
        await report_empty(service, await request(service, a))
    await give_back(service, await request(service, b), "suspect", listing_result="read_failed")
    await report_empty(service, await request(service, {**b, "attempt_id": str(uuid4())}))
    previous = await request(service, b)
    async with database.transaction() as session:
        (proxy,) = await session.scalars(select(Proxy))
        proxy.config_revision = uuid4()
    await report_empty(service, previous)
    await report_empty(service, await request(service, c))
    await report_empty(service, await request(service, a))
    assert (await policy_row(database)).empty_step == 0
    clock.advance(301)
    await report_empty(service, await request(service, b))
    assert (await policy_row(database)).empty_step == 0
    await report_empty(service, await request(service, a))
    await report_empty(service, await request(service, c))
    assert (await policy_row(database)).empty_step == 1


async def test_generic_retry_after_is_respected_and_new_epoch_starts_open(
    app,
    client,
    service,
    database,
    clock,
):
    await pool(client, database)
    (task,) = await active_tasks(database, service)
    enable(app, crawl_empty_tasks=1)
    await report_empty(service, await request(service, task))
    clock.advance(60)
    probe = await request(service, task)
    await give_back(service, probe, "throttled", retry_after=700)
    await check_wait(service, task, 700, "crawl_line_busy")
    clock.advance(700)
    enable(app, crawl_policy_epoch="new-epoch")
    assert (await request(service, task))["lease"]
    assert (await policy_row(database, epoch="new-epoch")).empty_step == 0
    assert (await policy_row(database)).empty_step == 1

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from scheduler_center.modules.batch.models import Batch, BatchStatus, Creator
from scheduler_center.modules.crawl.models import (
    CrawlConclusion,
    CrawlerAttemptStatus,
    CrawlerRunKind,
    CrawlerTask,
    CrawlerTaskAttempt,
    CrawlerTaskKind,
    CrawlerTaskRun,
    CrawlerTaskStatus,
)
from scheduler_center.modules.proxy.models import (
    CrawlLinePolicy,
    Lease,
    Line,
    ListingResult,
    Outcome,
    Proxy,
    Use,
)
from tests.factories import api_settings

PATH = "/api/stats/crawl-ips"
BATCHES_PATH = "/api/stats/crawl-ip-batches"
START = datetime(2026, 11, 25, 8, tzinfo=UTC)
LINE_A, LINE_B, LINE_C = "192.0.2.11", "192.0.2.12", "2001:db8:17::/64"


def _batch(session, name, *, start=0, end=None):
    batch = Batch(name, "test", created_at=START + timedelta(seconds=start))
    batch.start(batch.created_at)
    if end is not None:
        batch.status, batch.finished_at = BatchStatus.COMPLETED, START + timedelta(seconds=end)
    session.add(batch)
    return batch


def _task(session, batch, position, *, handle=None, conclusion=None, kind=CrawlerTaskKind.CRAWL):
    creator = Creator(batch.id, position, None, f"达人 {position}", handle)
    task = CrawlerTask(kind, batch.id, creator.id, {}, published_at=batch.created_at)
    if conclusion is not None:
        task.conclusion = conclusion
        task.finish(CrawlerTaskStatus.SUCCEEDED, START + timedelta(minutes=5))
    session.add_all([creator, task])
    return task


def _lease(
    session,
    task,
    line,
    second,
    *,
    outcome=Outcome.NORMAL,
    listing=None,
    detail=None,
    proxy=None,
    use=Use.CRAWL,
    attempt_id=None,
):
    acquired = START + timedelta(seconds=second)
    lease = Lease(
        "device",
        use,
        line,
        proxy and proxy.id,
        task and task.id,
        acquired_at=acquired,
        expires_at=acquired + timedelta(seconds=60),
        listing_result=listing,
        attempt_id=attempt_id,
        proxy_revision=proxy and proxy.config_revision,
    )
    if outcome is not None:
        lease.release(outcome, detail, acquired + timedelta(seconds=1))
    session.add(lease)
    return lease


def _proxy(session, line, name, *, port=8000):
    proxy = Proxy("proxy.example", port, line=line, name=name, config_revision=uuid4())
    session.add(proxy)
    return proxy


async def _stats(client, batch=None):
    response = await client.get(PATH, params={"batch_id": str(batch.id)} if batch else None)
    assert response.status_code == 200, response.text
    return response.json()


async def test_aggregates_and_attributes_each_creator_once_across_batch_exits(client, database):
    async with database.transaction() as session:
        batch = _batch(session, "运行中")
        other = _batch(session, "后完成", start=120, end=500)
        no_activity = _batch(session, "刚开始", start=150)
        first = _task(session, batch, 1, conclusion=CrawlConclusion.OK)
        second = _task(session, batch, 2, conclusion=CrawlConclusion.OK)
        third = _task(session, batch, 3)
        fourth = _task(session, batch, 4)
        foreign = _task(session, other, 1)
        download = _task(session, batch, 5, kind=CrawlerTaskKind.DOWNLOAD)
        a = _proxy(session, LINE_A, "直连美国家宽 01")
        b1 = _proxy(session, LINE_B, "家宽 02", port=8001)
        b2 = _proxy(session, LINE_B, "家宽 02 别名", port=8002)
        _lease(session, first, LINE_B, 0, proxy=b1)  # 旧的正常归还早于确知成功。
        _lease(session, first, LINE_A, 15, proxy=a, listing=ListingResult.COMPLETE)
        _lease(session, first, LINE_B, 30, proxy=b2, listing=ListingResult.COMPLETE)
        _lease(session, second, LINE_B, 60, proxy=b1)  # 唯一旧租约可匹配已完成结论。
        _lease(session, third, LINE_C, 75, listing=ListingResult.COMPLETE)
        _lease(session, fourth, LINE_A, 120, proxy=a, outcome=None)
        _lease(session, foreign, "192.0.2.99", 180, listing=ListingResult.COMPLETE)
        _lease(session, fourth, "192.0.2.98", 500, use=Use.DOWNLOAD)
        _lease(session, download, "192.0.2.97", 500, listing=ListingResult.COMPLETE)
        _lease(session, None, "192.0.2.96", 500)

    result = await _stats(client)
    assert result["batch"] == {
        "id": str(batch.id),
        "name": "运行中",
        "status": "running",
        "has_leases": True,
    }
    assert result["data_status"] == "available"
    summary = result["summary"]
    assert summary["active_ips"] == 3
    assert summary["crawl_leases"] == 6
    assert summary["collected_creators"] == 3
    assert summary["active_hours"] == pytest.approx(1 / 30)
    assert summary["creators_per_hour"] == 90
    assert summary["leases_per_hour"] == 180
    assert summary["risk_rate_pct"] == 0
    assert datetime.fromisoformat(summary["first_lease_at"]) == START
    assert datetime.fromisoformat(summary["last_lease_at"]) == START + timedelta(minutes=2)
    lines = {item["line"]: item for item in result["lines"]}
    assert set(lines) == {LINE_A, LINE_B, LINE_C}
    assert [lines[line]["crawl_leases"] for line in (LINE_A, LINE_B, LINE_C)] == [2, 3, 1]
    assert [row["collected_creators"] for row in lines.values()] == [1, 1, 1]
    assert [row["creators_per_hour"] for row in lines.values()] == [30, 30, 30]
    assert sorted(row["creator_share_pct"] for row in lines.values()) == [33.33, 33.33, 33.34]
    assert sum(round(row["creator_share_pct"] * 100) for row in lines.values()) == 10_000
    assert all(row["risk_share_pct"] == 0 for row in lines.values())
    assert lines[LINE_A]["proxy_name"] == "直连美国家宽 01"
    assert lines[LINE_B]["proxy_name"] == LINE_B
    assert lines[LINE_C]["proxy_name"] == LINE_C
    assert lines[LINE_C]["active_hours"] == 0
    assert (await _stats(client, other))["summary"]["collected_creators"] == 1
    assert (await _stats(client, no_activity))["data_status"] == "no_leases"

    async with database.transaction() as session:
        current = await session.get(Batch, batch.id)
        current.status = BatchStatus.COMPLETED
    assert (await _stats(client))["batch"]["id"] == str(other.id)


async def test_repeated_roster_accounts_count_as_one_creator(client, database):
    async with database.transaction() as session:
        batch = _batch(session, "重复账号")
        gone = _task(session, batch, 1, handle="Taylor", conclusion=CrawlConclusion.NOT_FOUND)
        first = _task(session, batch, 2, handle="taylor")
        later = _task(session, batch, 3, handle="TAYLOR")
        _lease(session, gone, LINE_C, 0)
        _lease(session, first, LINE_A, 60, listing=ListingResult.COMPLETE)
        _lease(session, later, LINE_B, 120, listing=ListingResult.COMPLETE)

    result = await _stats(client, batch)
    assert result["summary"]["crawl_leases"] == 3
    assert result["summary"]["collected_creators"] == 1
    assert result["summary"]["not_found"] == 0
    assert [(row["line"], row["creator_share_pct"]) for row in result["lines"]] == [
        (LINE_A, 100),
        (LINE_B, 0),
        (LINE_C, 0),
    ]


async def test_accounts_and_old_leases_require_an_attributable_result(client, database):
    async with database.transaction() as session:
        batch = _batch(session, "账号结论")
        private = _task(session, batch, 1, conclusion=CrawlConclusion.PRIVATE)
        private.run = 2
        not_found = _task(session, batch, 2, conclusion=CrawlConclusion.NOT_FOUND)
        login = _task(session, batch, 3, conclusion=CrawlConclusion.LOGIN_REQUIRED)
        ambiguous = _task(session, batch, 4, conclusion=CrawlConclusion.OK)
        legacy_ok = _task(session, batch, 5, conclusion=CrawlConclusion.OK)
        for run_number, line, second in [(1, LINE_A, 5), (2, LINE_B, 20)]:
            run = CrawlerTaskRun(
                private.id, run_number, batch.id, private.creator_id, None, CrawlerRunKind.CRAWL, {}
            )
            attempt = CrawlerTaskAttempt(
                private.id,
                run_number,
                1,
                "device",
                uuid4(),
                START,
                START + timedelta(minutes=5),
                status=CrawlerAttemptStatus.SUCCEEDED,
            )
            session.add_all([run, attempt])
            _lease(session, private, line, second, attempt_id=attempt.attempt_id)
        _lease(session, not_found, LINE_B, 30, detail="10221 user banned")
        _lease(session, login, LINE_A, 40)
        _lease(session, ambiguous, LINE_A, 50)
        _lease(session, ambiguous, LINE_B, 60)
        _lease(session, legacy_ok, LINE_C, 65, outcome="ok")

    data = await _stats(client, batch)
    lines = {row["line"]: row for row in data["lines"]}
    assert data["summary"]["collected_creators"] == 1
    assert data["summary"]["private"] == data["summary"]["not_found"] == 1
    assert data["summary"]["login_required"] == 1
    assert data["summary"]["risk_count"] == 0
    assert lines[LINE_A]["private"] == 0
    assert lines[LINE_B]["private"] == lines[LINE_B]["not_found"] == 1
    assert lines[LINE_A]["login_required"] == 1
    assert lines[LINE_C]["collected_creators"] == 1


async def test_risk_is_once_per_lease_and_excludes_network_and_account_failures(client, database):
    async with database.transaction() as session:
        batch = _batch(session, "风险")
        task = _task(session, batch, 1)
        cases_a = [
            (Outcome.SUSPECT, ListingResult.EMPTY_RESPONSE, "空正文且疑似验证码"),
            (Outcome.SUSPECT, None, "empty_body"),
            (Outcome.SUSPECT, ListingResult.READ_FAILED, "主页没有页面数据，可能停在了挑战页"),
            (Outcome.SUSPECT, ListingResult.READ_FAILED, "Captcha verification"),
            (Outcome.THROTTLED, None, "验证码（429）"),
            (Outcome.SUSPECT, ListingResult.READ_FAILED, "作品列表读失败，statusCode=209002"),
            (Outcome.SUSPECT, None, "出口 IP 已被平台封禁"),
            (Outcome.SUSPECT, ListingResult.READ_FAILED, "作品列表 HTTP 403"),
            (Outcome.SUSPECT, None, "risk_control"),
            (Outcome.SUSPECT, ListingResult.READ_FAILED, "作品列表等待响应超时"),
            (Outcome.SUSPECT, None, "主页没有账号资料（10221 user banned）"),
            (Outcome.PROXY_DOWN, None, "连接代理失败，复查代理端口无响应"),
            (Outcome.AUTH_FAILED, None, "达人主页代理认证失败（407）"),
            (Outcome.SUSPECT, ListingResult.READ_FAILED, "作品列表读取中止"),
            (Outcome.NORMAL, ListingResult.COMPLETE, "作品列表完整，无符合条件的作品"),
            (None, None, None),
        ]
        for i, (outcome, listing, detail) in enumerate(cases_a):
            _lease(session, task, LINE_A, i * 5, outcome=outcome, listing=listing, detail=detail)
        _lease(session, task, LINE_B, 80, outcome=Outcome.SUSPECT, detail="达人主页限流（429）")
        _lease(session, task, LINE_B, 90, outcome=Outcome.SUSPECT, detail="net::ERR_TIMED_OUT")

    data = await _stats(client, batch)
    lines = {row["line"]: row for row in data["lines"]}
    assert lines[LINE_A]["risk_breakdown"] == {
        "empty_body": 2,
        "challenge": 2,
        "throttled": 2,
        "blocked": 2,
        "other": 1,
    }
    assert lines[LINE_A]["risk_count"] == 9
    assert lines[LINE_A]["risk_rate_pct"] == 56.25
    assert lines[LINE_B]["risk_rate_pct"] == 50
    assert lines[LINE_B]["risk_share_pct"] == 10
    assert sum(round(row["risk_share_pct"] * 100) for row in data["lines"]) == 10_000
    assert data["summary"]["risk_count"] == 10
    assert data["summary"]["risk_rate_pct"] == 55.56
    assert data["summary"]["risk_breakdown"]["throttled"] == 3


@pytest.mark.parametrize("window, expected_speed", [(None, None), (59.9, None), (60, 60)])
async def test_rate_is_only_shown_after_a_full_minute(client, database, window, expected_speed):
    async with database.transaction() as session:
        batch = _batch(session, "时速")
        task = _task(session, batch, 1)
        _lease(session, task, LINE_A, 0, listing=ListingResult.COMPLETE)
        if window is not None:
            _lease(session, task, LINE_B, window, listing=ListingResult.COMPLETE)
    result = await _stats(client, batch)
    assert result["summary"]["active_hours"] == pytest.approx((window or 0) / 3600)
    assert result["summary"]["creators_per_hour"] == expected_speed
    by_line = {row["line"]: row for row in result["lines"]}
    assert by_line[LINE_A]["creators_per_hour"] == expected_speed
    assert by_line[LINE_A]["creator_share_pct"] == 100


async def test_missing_history_and_paginated_batch_choices(client, database):
    empty = await _stats(client)
    assert empty["batch"] is None
    assert empty["data_status"] == "no_batch"
    assert empty["summary"]["active_ips"] == 0
    async with database.transaction() as session:
        old = _batch(session, "无明细历史批次", start=-172800, end=-172700)
        recent = _batch(session, "新批次")
        _lease(session, _task(session, recent, 1), LINE_A, 60)
    old_stats = await _stats(client, old)
    assert old_stats["data_status"] == "history_unavailable"
    assert old_stats["batch"]["has_leases"] is False
    assert old_stats["lines"] == []
    assert old_stats["summary"]["creators_per_hour"] is None
    assert (await client.get(PATH, params={"batch_id": str(uuid4())})).status_code == 404
    first = (await client.get(BATCHES_PATH, params={"size": 1})).json()
    second = (await client.get(BATCHES_PATH, params={"size": 1, "page": 2})).json()
    assert first["total"] == second["total"] == 2
    assert first["items"] == [
        {"id": str(recent.id), "name": "新批次", "has_leases": True, "status": "running"}
    ]
    assert second["items"][0]["id"] == str(old.id)
    assert second["items"][0]["has_leases"] is False


async def test_only_current_and_scoped_backoff_is_shown(app, client, database, monkeypatch):
    now = START + timedelta(minutes=2)
    monkeypatch.setattr("scheduler_center.modules.stats.crawl_ips.utcnow", lambda: now)
    app.state.settings = api_settings(
        database_url=app.state.settings.database_url,
        crawl_policy_enabled=True,
        crawl_policy_epoch="today",
        crawl_policy_exits=(LINE_A, LINE_B, LINE_C),
        crawl_policy_devices=("device",),
    )
    legacy_key = "192.0.2.14"
    async with database.transaction() as session:
        batch = _batch(session, "退避")
        task = _task(session, batch, 1)
        for line in (LINE_A, LINE_B, LINE_C, legacy_key):
            _lease(session, task, line, 60)
        legacy = Line(Use.CRAWL, legacy_key)
        legacy.cool_after_empty_lists(now)
        session.add(legacy)
        session.add_all(
            [
                CrawlLinePolicy(
                    "today", LINE_A, START, empty_step=2, empty_until=now + timedelta(seconds=30)
                ),
                CrawlLinePolicy(
                    "today", LINE_B, START, empty_step=1, empty_until=now - timedelta(seconds=1)
                ),
                CrawlLinePolicy(
                    "last-epoch",
                    LINE_C,
                    START,
                    empty_step=4,
                    empty_until=now + timedelta(minutes=5),
                ),
            ]
        )
    lines = {row["line"]: row for row in (await _stats(client, batch))["lines"]}
    assert lines[LINE_A]["backoff_state"] == "cooling"
    assert lines[LINE_A]["backoff_step"] == 2
    assert datetime.fromisoformat(lines[LINE_A]["backoff_until"]) == now + timedelta(seconds=30)
    assert lines[LINE_B]["backoff_state"] == "probing"
    assert lines[LINE_B]["backoff_step"] == 1
    assert lines[LINE_B]["backoff_until"] is None
    assert lines[LINE_C]["backoff_state"] == "normal"
    assert lines[LINE_C]["backoff_step"] == 0
    assert lines[legacy_key]["backoff_state"] == "cooling"
    assert lines[legacy_key]["backoff_step"] == 1


async def test_credentials_and_stale_proxy_metadata_never_leave_the_api(client, database):
    async with database.transaction() as session:
        batch = _batch(session, "脱敏")
        task = _task(session, batch, 1)
        a = _proxy(session, LINE_A, "住宅代理 secretUsername")
        a.username, a.password = "secretUsername", "verySecretPassword"
        a.private_key, a.refresh_url = "privateKeyContents", "https://proxy.example/secretToken"
        b = _proxy(session, LINE_B, "Bearer secretAuthString", port=8001)
        c = _proxy(session, LINE_C, "现在的代理名称", port=8002)
        _lease(session, task, LINE_A, 0, proxy=a, outcome=Outcome.SUSPECT, detail=a.private_key)
        _lease(session, task, LINE_B, 60, proxy=b)
        _lease(session, task, LINE_C, 80, proxy=c)
        c.config_revision = uuid4()
        _lease(session, task, "direct:my-device@user:auth", 100)
        _lease(session, task, "fe80::1%secretZone", 120)
    response = await client.get(PATH, params={"batch_id": str(batch.id)})
    assert response.status_code == 200
    for secret in (
        "secretUsername",
        "verySecretPassword",
        "privateKeyContents",
        "secretToken",
        "secretAuthString",
        "my-device",
        "secretZone",
        "proxy.example",
    ):
        assert secret not in response.text
    lines = {row["line"]: row for row in response.json()["lines"]}
    assert lines[LINE_A]["proxy_name"] == LINE_A
    assert lines[LINE_B]["proxy_name"] == LINE_B
    assert lines[LINE_C]["proxy_name"] == LINE_C
    assert len([line for line in lines if line.startswith("线路 ")]) == 2
    assert {"username", "password", "private_key", "detail", "proxy_id"}.isdisjoint(lines[LINE_A])

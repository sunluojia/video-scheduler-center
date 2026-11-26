"""业务推进：从开始批次到达人得出结论的各条路径。"""

import asyncio
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from openpyxl import load_workbook

from scheduler_center.modules.analysis.messages import JobState
from scheduler_center.modules.analysis.models import AnalysisJob, JobStatus
from scheduler_center.modules.batch.models import (
    Batch,
    BatchStatus,
    Creator,
    CreatorStatus,
    Work,
    WorkStatus,
)
from scheduler_center.modules.crawl.models import CrawlerTask, CrawlerTaskStatus
from scheduler_center.modules.flow.history import events
from scheduler_center.modules.flow.service import Flow
from scheduler_center.modules.material.models import Material
from tests.factories import (
    analysis_says,
    at,
    crawl,
    crawl_task,
    crawler_message,
    crawler_says,
    creators,
    job_event,
    load,
    messages,
    new_batch,
    photo,
    recognized,
    upload,
    video,
    works,
)


async def test_start_publishes_a_crawl_task_for_each_recognizable_creator(database):
    batch = await new_batch(
        database, "alice", "@Bob", "https://www.tiktok.com/@carol?lang=en", "不是账号!"
    )

    tasks = await messages(database, "crawl_creator")
    assert [task["creator"]["handle"] for task in tasks] == ["alice", "bob", "carol"]
    assert {(task["run"], task["max_works"]) for task in tasks} == {(1, 5)}
    statuses = [creator.status for creator in await creators(database, batch)]
    assert statuses == [CreatorStatus.CRAWLING] * 3 + [CreatorStatus.INVALID]


async def test_creator_goes_from_crawl_to_summary(database, service):
    batch = await new_batch(database, "alice")
    [alice] = await creators(database, batch)

    await crawl(database, alice, video(1), photo(2), video(3))

    downloads = await messages(database, "download_video")
    assert [item["work"]["work_id"] for item in downloads] == [
        video(1)["work_id"],
        video(3)["work_id"],
    ]
    alice = await load(database, Creator, alice.id)
    assert (alice.status, alice.labels) == (CreatorStatus.ANALYZING, ["图文作者"])

    for item, text in zip(downloads, ("one", "two"), strict=True):
        assert (await upload(service, item["task_id"], text=text)).status_code == 204
    analyses = await messages(database, "analyze_video")
    assert [(item["material"]["size"], item["allow_reuse"]) for item in analyses] == [
        (3, True),
        (3, True),
    ]

    for item, tags in zip(analyses, (["美妆"], ["穿搭", "美妆"]), strict=True):
        await analysis_says(database, job_event(item["job_id"], "accepted"))
        await analysis_says(database, stage_started(item["job_id"], "fetch"))
        await analysis_says(
            database, job_event(item["job_id"], "completed", seconds=3, result=recognized(*tags))
        )

    [summary] = await messages(database, "summarize_creator")
    assert [item["job_id"] for item in summary["videos"]] == [a["job_id"] for a in analyses]
    assert summary["videos"][1]["recognition"]["tags"] == ["穿搭", "美妆"]
    assert (await load(database, Creator, alice.id)).status == CreatorStatus.SUMMARIZING

    await analysis_says(
        database,
        job_event(summary["job_id"], "completed", kind="summary", result={"tags": ["美妆博主"]}),
    )

    alice = await load(database, Creator, alice.id)
    assert (alice.status, alice.tags) == (CreatorStatus.COMPLETED, ["美妆博主"])
    assert [(work.status, work.tags) for work in await works(database, alice)] == [
        (WorkStatus.COMPLETED, ["美妆"]),
        (WorkStatus.SKIPPED, None),
        (WorkStatus.COMPLETED, ["穿搭", "美妆"]),
    ]
    for work in await works(database, alice):
        if work.material_id:  # 分析完成后原视频不再需要
            assert (await load(database, Material, work.material_id)).discarded_at is not None

    async with database.transaction() as session:
        await Flow(session).complete_if_done(batch.id)
    assert (await load(database, Batch, batch.id)).status == BatchStatus.COMPLETED


async def test_crawl_conclusions(database, client):
    batch = await new_batch(database, "private", "missing", "broken", "empty", "photos", "locked")
    private, missing, broken, empty, photos, locked = await creators(database, batch)

    await crawler_says(
        database, await crawl_task(database, private), "crawled", conclusion="private"
    )
    await crawler_says(
        database, await crawl_task(database, locked), "crawled", conclusion="login_required"
    )
    await crawler_says(
        database, await crawl_task(database, missing), "crawled", conclusion="not_found"
    )
    await crawler_says(
        database,
        await crawl_task(database, broken),
        "crawled",
        conclusion="failed",
        reason="登录失效",
    )
    await crawl(database, empty)
    await crawl(database, photos, photo(1), photo(2))

    concluded = await creators(database, batch)
    assert [(c.status, c.note, c.labels) for c in concluded] == [
        (CreatorStatus.FAILED, "私密账号", []),
        (CreatorStatus.FAILED, "找不到此达人", []),
        (CreatorStatus.RECRAWL, "爬取失败：登录失效", []),
        (CreatorStatus.COMPLETED, None, ["无作品"]),
        (CreatorStatus.COMPLETED, None, ["图文作者"]),
        (CreatorStatus.RECRAWL, "需要登录", []),
    ]
    assert await messages(database, "download_video") == []

    path = f"/api/batches/{batch.id}"
    detail = (await client.get(path)).raise_for_status().json()
    assert detail["creators"] == detail["creator_progress"] == {
        "failed": 2,
        "recrawl": 2,
        "completed": 2,
    }
    results = (await client.get(f"{path}/creators", params={"progress": "recrawl"})).json()
    assert [(item["handle"], item["note"]) for item in results["items"]] == [
        ("broken", "爬取失败：登录失效"),
        ("locked", "需要登录"),
    ]

    response = (await client.get(f"{path}/export")).raise_for_status()
    book = load_workbook(BytesIO(response.content))
    assert book["失败达人"]["D2"].value == "私密账号"
    assert book["待重跑达人"]["D3"].value == "需要登录"


async def test_migration_keeps_existing_login_required_creators_in_recrawl(database, database_url):
    batch = await new_batch(database, "locked", "private")
    locked, private = await creators(database, batch)
    await crawler_says(
        database, await crawl_task(database, locked), "crawled", conclusion="login_required"
    )
    await crawler_says(
        database, await crawl_task(database, private), "crawled", conclusion="private"
    )
    original = await load(database, Creator, locked.id)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    await database.dispose()
    await asyncio.to_thread(command.downgrade, config, "0018")
    try:
        previous = await load(database, Creator, locked.id)
        assert (previous.status, previous.note) == (CreatorStatus.FAILED, "需要登录")
    finally:
        await database.dispose()
        await asyncio.to_thread(command.upgrade, config, "head")

    migrated = await load(database, Creator, locked.id)
    assert (migrated.status, migrated.note, migrated.updated_at) == (
        CreatorStatus.RECRAWL,
        "需要登录",
        original.updated_at,
    )
    unchanged = await load(database, Creator, private.id)
    assert (unchanged.status, unchanged.note) == (CreatorStatus.FAILED, "私密账号")


async def test_duplicate_and_stale_events_change_nothing(database):
    batch = await new_batch(database, "alice")
    [alice] = await creators(database, batch)
    task_id = await crawl_task(database, alice)

    event = await crawler_says(database, task_id, "accepted")
    async with database.transaction() as session:
        await Flow(session).on_crawler_event(event)  # 重复投递
    await crawler_says(database, task_id, "crawled", run=2, conclusion="private")  # 不是当前轮次

    task = await load(database, CrawlerTask, UUID(task_id))
    assert task.status == CrawlerTaskStatus.ACCEPTED
    assert (await load(database, Creator, alice.id)).status == CreatorStatus.CRAWLING
    async with database.engine.connect() as connection:
        recorded = (await connection.execute(events.select())).all()
    assert len(recorded) == 2  # 重复的不再保存，其他轮次的记入历史


async def test_out_of_order_job_events_never_move_backwards(database, service):
    job_id = await _analyzing(database, service)

    await analysis_says(database, job_event(job_id, "completed", seconds=5, result=recognized("a")))
    await analysis_says(database, job_event(job_id, "accepted", seconds=1))
    await analysis_says(database, job_event(job_id, "failed", seconds=2, error="超时"))
    await analysis_says(database, stage_started(job_id, "recognize"))

    job = await load(database, AnalysisJob, UUID(job_id))
    assert (job.status, job.result["recognition"]["tags"]) == (JobStatus.COMPLETED, ["a"])


async def test_failed_download_then_recrawl_starts_over(database):
    batch = await new_batch(database, "alice")
    [alice] = await creators(database, batch)
    await crawl(database, alice, video(1))
    [download] = await messages(database, "download_video")

    await crawler_says(database, download["task_id"], "download_failed", reason="链接失效")

    [work] = await works(database, alice)
    assert (work.status, work.error) == (WorkStatus.FAILED, "下载失败：链接失效")
    assert (await load(database, Creator, alice.id)).status == CreatorStatus.RECRAWL

    async with database.transaction() as session:
        await Flow(session, legacy_crawler_dispatch=True).recrawl(alice.id)

    assert await works(database, alice) == []
    assert (await load(database, Creator, alice.id)).status == CreatorStatus.CRAWLING
    await crawl(database, alice, video(1, "8001"), run=2)
    [work] = await works(database, alice)
    assert (work.platform_id, work.status) == ("8001", WorkStatus.DOWNLOADING)


async def test_needs_review_waits_for_abandon(database, service):
    job_id = await _analyzing(database, service, videos=2)
    other = (await messages(database, "analyze_video"))[1]["job_id"]
    await analysis_says(database, job_event(other, "completed", result=recognized("a")))

    await analysis_says(database, job_event(job_id, "needs_review", error="请求超时，结果不明"))

    job = await load(database, AnalysisJob, UUID(job_id))
    assert (await load(database, Creator, job.creator_id)).status == CreatorStatus.ANALYZING
    assert await messages(database, "summarize_creator") == []

    async with database.transaction() as session:
        await Flow(session).abandon(job.work_id)

    assert (await load(database, Work, job.work_id)).status == WorkStatus.ABANDONED
    [summary] = await messages(database, "summarize_creator")
    assert [item["job_id"] for item in summary["videos"]] == [other]


async def test_all_videos_failing_needs_recrawl_without_summary(database, service):
    job_id = await _analyzing(database, service)

    await analysis_says(database, job_event(job_id, "failed", error="模型拒绝"))

    job = await load(database, AnalysisJob, UUID(job_id))
    work = await load(database, Work, job.work_id)
    assert (work.status, work.error) == (WorkStatus.FAILED, "分析失败：模型拒绝")
    creator = await load(database, Creator, job.creator_id)
    assert (creator.status, creator.note) == (CreatorStatus.RECRAWL, "视频全部失败，需要重新抓取")
    assert await messages(database, "summarize_creator") == []


@pytest.mark.parametrize(
    ("items", "expected_status", "expected_note"),
    [
        (
            ("shop", "restricted"),
            CreatorStatus.RECRAWL,
            "最新视频均为挂车或受限视频（服务端未返回播放地址），需要重新抓取",
        ),
        (("photo", "shop"), CreatorStatus.COMPLETED, None),
        (("photo", "shop", "network"), CreatorStatus.COMPLETED, None),
        (("recognized", "restricted"), CreatorStatus.COMPLETED, None),
        (
            ("shop", "network"),
            CreatorStatus.RECRAWL,
            "视频全部失败（含挂车或受限视频），需要重新抓取",
        ),
        (
            ("restricted", "ai_failure"),
            CreatorStatus.RECRAWL,
            "视频全部失败（含挂车或受限视频），需要重新抓取",
        ),
        (
            ("photo", "network"),
            CreatorStatus.RECRAWL,
            "视频全部失败，需要重新抓取",
        ),
    ],
    ids=[
        "all-restricted",
        "photo-and-restricted",
        "photo-restricted-and-network",
        "recognized-and-restricted",
        "restricted-and-network",
        "restricted-and-ai-failure",
        "photo-and-network-only",
    ],
)
async def test_restricted_videos_settle_and_export(
    database, client, service, items, expected_status, expected_note
):
    batch = await new_batch(database, "alice")
    [alice] = await creators(database, batch)
    roster = [
        photo(position) if item == "photo" else video(position)
        for position, item in enumerate(items, 1)
    ]
    await crawl(database, alice, *roster)
    downloads = {
        item["work"]["work_id"]: item for item in await messages(database, "download_video")
    }
    reasons = {
        "shop": "挂车/受限视频（服务端未返回播放地址）",
        "restricted": "受限视频（服务端未返回播放地址）",
        "network": "第 3 次：作品页里没有媒体地址",
    }
    for item, entry in zip(items, roster, strict=True):
        if item == "photo":
            continue
        task = downloads[entry["work_id"]]
        if item in reasons:
            await crawler_says(database, task["task_id"], "download_failed", reason=reasons[item])
        else:
            assert (await upload(service, task["task_id"])).status_code == 204
            [analysis] = await messages(database, "analyze_video")
            if item == "recognized":
                await analysis_says(
                    database,
                    job_event(analysis["job_id"], "completed", result=recognized("美妆", "美妆")),
                )
            else:
                await analysis_says(
                    database, job_event(analysis["job_id"], "failed", error="识别超时")
                )

    summaries = await messages(database, "summarize_creator")
    if "recognized" in items:
        [summary] = summaries
        assert len(summary["videos"]) == 1
        await analysis_says(
            database,
            job_event(
                summary["job_id"], "completed", kind="summary", result={"tags": ["美妆博主"]}
            ),
        )
    else:
        assert summaries == []

    settled = await load(database, Creator, alice.id)
    assert (settled.status, settled.note) == (expected_status, expected_note)
    response = (await client.get(f"/api/batches/{batch.id}/export")).raise_for_status()
    book = load_workbook(BytesIO(response.content))
    success, failed, retry = book["成功达人"], book["失败达人"], book["待重跑达人"]
    assert failed.max_row == 1
    if expected_status == CreatorStatus.RECRAWL:
        assert success.max_row == 2
        assert retry["D2"].value == expected_note
    else:
        assert retry.max_row == 1
        for work, item in zip(await works(database, alice), items, strict=True):
            if item in {"shop", "restricted"}:
                assert work.status == WorkStatus.FAILED
                assert work.error == f"下载失败：{reasons[item]}"
                assert success.cell(3, 3 + 2 * work.position).value == work.error
        if "photo" in items:
            assert settled.tags == []
            assert success["E3"].value == success["N3"].value == success["O3"].value == "图文作者"
            assert success["P3"].value is None
        else:
            assert success["E3"].value == success["N3"].value == "美妆"
            assert success["O3"].value == "美妆博主"
            assert success["P3"].value == roster[0]["work_id"]


async def test_retry_reopens_the_job_and_creator(database, service):
    job_id = await _analyzing(database, service)
    await analysis_says(database, job_event(job_id, "failed", error="模型拒绝"))
    job = await load(database, AnalysisJob, UUID(job_id))

    await _retried(database, job, seconds=5)

    assert (await load(database, AnalysisJob, job.id)).status == JobStatus.PROCESSING
    assert (await load(database, Work, job.work_id)).status == WorkStatus.ANALYZING
    assert (await load(database, Creator, job.creator_id)).status == CreatorStatus.ANALYZING


async def test_late_retry_receipt_keeps_the_newer_failure(database, service):
    job_id = await _analyzing(database, service)
    await analysis_says(database, job_event(job_id, "failed", error="模型拒绝"))
    # 补跑之后又失败，事件先于补跑接口的回执到达
    await analysis_says(database, job_event(job_id, "failed", seconds=9, error="补跑也失败"))
    job = await load(database, AnalysisJob, UUID(job_id))

    await _retried(database, job, seconds=5)

    job = await load(database, AnalysisJob, job.id)
    assert (job.status, job.error) == (JobStatus.FAILED, "补跑也失败")
    work = await load(database, Work, job.work_id)
    assert (work.status, work.error) == (WorkStatus.FAILED, "分析失败：补跑也失败")


async def test_late_event_does_not_undo_a_failed_redownload(database, service):
    job_id = await _analyzing(database, service)
    await analysis_says(database, job_event(job_id, "completed", seconds=5, result=recognized("a")))
    job = await load(database, AnalysisJob, UUID(job_id))
    async with database.transaction() as session:
        await Flow(session, legacy_crawler_dispatch=True).redownload(job.work_id)
    download = (await messages(database, "download_video"))[-1]
    await crawler_says(
        database, download["task_id"], "download_failed", run=download["run"], reason="链接失效"
    )

    await analysis_says(database, job_event(job_id, "accepted"))  # 首轮迟到的受理

    work = await load(database, Work, job.work_id)
    assert (work.status, work.error) == (WorkStatus.FAILED, "下载失败：链接失效")
    creator = await load(database, Creator, job.creator_id)
    assert creator.status == CreatorStatus.RECRAWL


async def test_reanalysis_runs_again_without_reuse(database, service):
    job_id = await _analyzing(database, service)
    await analysis_says(database, job_event(job_id, "failed", error="模型拒绝"))
    job = await load(database, AnalysisJob, UUID(job_id))

    async with database.transaction() as session:
        await Flow(session).reanalyze(job.work_id)  # 原视频还在，直接重新分析

    again = (await messages(database, "analyze_video"))[-1]
    assert (again["job_id"], again["run"], again["allow_reuse"]) == (job_id, 2, False)

    await analysis_says(database, job_event(job_id, "completed", run=2, result=recognized("a")))
    async with database.transaction() as session:
        await Flow(session, legacy_crawler_dispatch=True).reanalyze(job.work_id)

    download = (await messages(database, "download_video"))[-1]
    assert download["run"] == 2
    assert (await load(database, Work, job.work_id)).status == WorkStatus.DOWNLOADING
    assert (await upload(service, download["task_id"], run=2, text="new")).status_code == 204
    latest = (await messages(database, "analyze_video"))[-1]
    assert (latest["run"], latest["allow_reuse"], latest["material"]["size"]) == (3, False, 3)


async def test_cancel_stops_the_batch(database, service):
    batch = await new_batch(database, "alice")
    [alice] = await creators(database, batch)
    await crawl(database, alice, video(1))
    [download] = await messages(database, "download_video")

    async with database.transaction() as session:
        await Flow(session).cancel(batch.id)

    task = await load(database, CrawlerTask, UUID(download["task_id"]))
    assert task.status == CrawlerTaskStatus.CANCELLED
    assert await messages(database) == []  # 还没投递的任务一并撤回
    assert (await upload(service, download["task_id"])).status_code == 409
    await crawler_says(database, download["task_id"], "download_failed", reason="晚到")
    assert (await works(database, alice))[0].status == WorkStatus.DOWNLOADING


async def test_cancel_does_not_deadlock_with_an_event_in_progress(database):
    """事件锁住达人、写入引用批次的历史后，取消锁住批次、等这位达人；事件提交时检查外键不能被批次锁挡住。"""
    batch = await new_batch(database, "alice")
    [alice] = await creators(database, batch)
    task_id = await crawl_task(database, alice)
    recorded = asyncio.Event()

    async def event() -> None:
        async with database.transaction() as session:
            await Flow(session).on_crawler_event(crawler_message(task_id, "accepted"))
            recorded.set()
            await asyncio.sleep(0.5)  # 等取消锁住批次后再提交

    async def cancel() -> None:
        await recorded.wait()
        async with database.transaction() as session:
            await Flow(session).cancel(batch.id)

    await asyncio.gather(event(), cancel())

    assert (await load(database, Batch, batch.id)).status == BatchStatus.CANCELLED
    task = await load(database, CrawlerTask, UUID(task_id))
    assert task.status == CrawlerTaskStatus.CANCELLED


def stage_started(job_id: str, stage: str) -> dict:
    return {
        "type": "stage",
        "job_id": job_id,
        "run": 1,
        "stage": stage,
        "status": "started",
        "occurred_at": at(2),
    }


async def _retried(database, job: AnalysisJob, seconds: float) -> None:
    """分析服务补跑接口的回执：按分析服务的时间，seconds 秒时重新开始。"""
    state = JobState(
        job_id=job.id, run=job.run, status=JobStatus.PROCESSING, occurred_at=at(seconds)
    )
    async with database.transaction() as session:
        await Flow(session).retried(job.batch_id, [state])


async def _analyzing(database, service, videos: int = 1) -> str:
    """一位达人的视频都已上传、提交分析；返回第一条的分析任务 ID。"""
    batch = await new_batch(database, "alice")
    [alice] = await creators(database, batch)
    await crawl(database, alice, *(video(n) for n in range(1, videos + 1)))
    for n, item in enumerate(await messages(database, "download_video")):
        assert (await upload(service, item["task_id"], text=f"video-{n}")).status_code == 204
    return (await messages(database, "analyze_video"))[0]["job_id"]

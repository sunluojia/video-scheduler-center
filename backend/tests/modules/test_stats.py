"""数据看板：按时间段统计各环节，导出分时数据。"""

import io
from datetime import timedelta

import httpx
from openpyxl import load_workbook

from scheduler_center.core.common.clock import utcnow
from scheduler_center.modules.executor.models import ExecutorKind
from tests.factories import (
    ANALYSIS_URL,
    analysis_says,
    crawl,
    crawl_task,
    crawler_says,
    creators,
    job_event,
    messages,
    new_batch,
    photo,
    recognized,
    register,
    upload,
    video,
)

PROFILE = {"handle": "dave", "url": "https://www.tiktok.com/@dave"}


async def run_batch(database, service):
    """alice 锁定两条视频：一条识别完成并汇总，一条下载失败；其余达人各得出一种结论。"""
    batch = await new_batch(database, "alice", "bob", "carol", "dave", "erin")
    alice, bob, carol, dave, erin = await creators(database, batch)
    await crawl(database, alice, video(1), photo(2), video(3))
    for creator, conclusion in ((bob, "private"), (carol, "login_required")):
        task = await crawl_task(database, creator)
        await crawler_says(database, task, "crawled", conclusion=conclusion)
    await crawler_says(
        database,
        await crawl_task(database, dave),
        "crawled",
        conclusion="failed",
        reason="第 1 次：主页没有页面数据；第 2 次：作品列表等待响应超时",
        profile=PROFILE,
    )
    await crawler_says(
        database,
        await crawl_task(database, erin),
        "crawled",
        conclusion="failed",
        reason="第 1 次：主页没有页面数据",
    )
    first, second = await messages(database, "download_video")
    assert (await upload(service, first["task_id"])).status_code == 204
    await crawler_says(database, second["task_id"], "download_failed", reason="链接失效")
    [analysis] = await messages(database, "analyze_video")
    await analysis_says(
        database, job_event(analysis["job_id"], "completed", result=recognized("美妆"))
    )
    [summary] = await messages(database, "summarize_creator")
    await analysis_says(
        database,
        job_event(summary["job_id"], "completed", kind="summary", result={"tags": ["美妆博主"]}),
    )
    return batch


def period(**fields) -> dict:
    """测试事件的时间是 2026-11-17，上传事件是现在。"""
    return {
        "since": "2026-11-16T00:00:00+08:00",
        "until": (utcnow() + timedelta(hours=1)).isoformat(),
        "bucket": "day",
        **fields,
    }


async def test_each_stage_is_counted_with_its_failures(client, database, service):
    batch = await run_batch(database, service)
    await new_batch(database, "zoe")  # 其他批次不计入

    stats = (await client.get("/api/stats", params=period(batch_id=str(batch.id)))).json()

    assert {key: value for key, value in stats["totals"].items() if value} == {
        "profile_ok": 4,
        "profile_failed": 1,
        "listing_ok": 1,
        "listing_failed": 1,
        "unreachable": 2,
        "videos_found": 2,
        "downloaded": 1,
        "downloaded_bytes": 5,
        "download_failed": 1,
        "analyzed": 1,
        "summarized": 1,
    }
    assert stats["conclusions"] == {"ok": 1, "private": 1, "login_required": 1, "failed": 2}
    assert [(row["stage"], row["reason"], row["count"]) for row in stats["failures"]] == [
        ("crawl", "主页没有页面数据", 1),
        ("crawl", "作品列表等待响应超时", 1),
        ("download", "链接失效", 1),
    ]
    assert stats["creators"] == {"completed": 1, "failed": 1, "recrawl": 3}
    assert stats["videos"] == {"completed": 1, "failed": 1}
    assert [row["stage"] for row in stats["durations"]][:2] == ["crawl", "download"]
    days = [row["at"][:10] for row in stats["timeline"]]
    assert days[0] == "2026-11-16" and len(days) == len(set(days))  # 没有数据的天也有一行
    assert sum(row["profile_ok"] for row in stats["timeline"]) == 4


async def test_period_must_be_valid(client):
    assert (await client.get("/api/stats", params=period(until="2026-11-15"))).status_code == 422
    too_long = period(since="2026-01-01", until="2026-12-01", bucket="hour")
    assert (await client.get("/api/stats", params=too_long)).status_code == 422


async def test_export_has_the_stage_sheets_and_ai_usage(client, database, service, executors):
    batch = await run_batch(database, service)
    await register(database, ExecutorKind.ANALYSIS, ANALYSIS_URL)
    usage = {
        "at": "2026-11-17T00:00:00+08:00",
        "kind": "recognize",
        "model_id": "m1",
        "model": "openai/qwen3.8-flash",
        "calls": 4,
        "succeeded": 3,
        "failed": 1,
        "input_tokens": 4000,
        "output_tokens": 300,
        "cached_tokens": 2400,
        "cache_input_tokens": 3000,
        "busy_seconds": 60.0,
    }
    executors.responses["GET", "/api/ai/usage"] = httpx.Response(200, json=[usage])
    models = [{"id": "m1", "max_concurrency": 4}]
    executors.responses["GET", "/api/ai/models"] = httpx.Response(200, json=models)

    response = await client.get("/api/stats/export", params=period(batch_id=str(batch.id)))

    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    book = load_workbook(io.BytesIO(response.content))
    assert book.sheetnames == ["概览", "分时数据", "分析各阶段", "用时", "失败原因", "AI 调用"]
    overview = {row[0]: row[1:] for row in book["概览"].iter_rows(values_only=True) if row[0]}
    assert overview["达人基础信息获取"] == (4, 1, 0.8)
    assert overview["视频下载"] == (1, 1, 0.5)
    assert overview["AI 输入 token 缓存命中率"][0] == 0.8
    assert overview["采集结论：需要登录"][0] == 1
    [ai] = book["AI 调用"].iter_rows(min_row=2, values_only=True)
    assert ai[:9] == ("视频识别", "openai/qwen3.8-flash", 4, 3, 1, 0.75, 4000, 2400, 0.8)
    assert book["AI 调用"]["I2"].number_format == "0.0%"
    assert executors.requests[-2].url.params["batch_id"] == str(batch.id)


async def test_export_still_works_when_analysis_is_offline(client, database, service):
    await run_batch(database, service)

    response = await client.get("/api/stats/export", params=period())

    book = load_workbook(io.BytesIO(response.content))
    assert book["AI 调用"]["A2"].value == "分析服务不在线"

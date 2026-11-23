"""数据看板的导出表：概览、分时数据、分析各阶段、用时、失败原因、AI 调用。

比例按 Excel 的百分比格式保存，分母为零时留空；时间为北京时间。
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.worksheet import Worksheet

from scheduler_center.core.common.clock import BEIJING, utcnow
from scheduler_center.modules.batch.models import Batch
from scheduler_center.modules.crawl.models import CrawlConclusion
from scheduler_center.modules.report.exporter import BORDER, FONT, HEADER_FILL
from scheduler_center.modules.stats.schemas import Bucket, Stats

STAGES = {
    "crawl": "达人采集",
    "download": "视频下载",
    "analysis": "视频分析",
    "summary": "作者汇总",
    "fetch": "获取视频",
    "extract_frames": "抽帧",
    "extract_audio": "提取音频",
    "listen": "听音",
    "recognize": "识别",
    "summarize": "汇总",
}
AI_KINDS = {"listen": "听音", "recognize": "视频识别", "summarize": "作者汇总"}
CONCLUSIONS = {
    CrawlConclusion.OK: "锁定作品",
    CrawlConclusion.PRIVATE: "私密账号",
    CrawlConclusion.LOGIN_REQUIRED: "需要登录",
    CrawlConclusion.NOT_FOUND: "找不到此达人",
    CrawlConclusion.FAILED: "采集失败",
}
FUNNEL = [
    ("达人基础信息获取", "profile_ok", "profile_failed"),
    ("达人视频列表获取", "listing_ok", "listing_failed"),
    ("视频下载", "downloaded", "download_failed"),
    ("视频 AI 识别", "analyzed", "analysis_failed"),
    ("作者汇总", "summarized", "summary_failed"),
]
PERCENT = "0.0%"
MB = 1024 * 1024


@dataclass(frozen=True)
class AiUsage:
    """分析服务 /api/ai/usage 的行，以及各模型的并发上限；分析服务不在线时写明原因。"""

    rows: list[dict]
    capacity: dict[str, int]
    error: str | None = None


def build_stats_workbook(stats: Stats, batch: Batch | None, ai: AiUsage) -> bytes:
    workbook = Workbook()
    _overview(workbook.active, stats, batch, ai)
    _timeline(workbook.create_sheet("分时数据"), stats, ai)
    _stages(workbook.create_sheet("分析各阶段"), stats)
    _durations(workbook.create_sheet("用时"), stats)
    _failures(workbook.create_sheet("失败原因"), stats)
    _ai(workbook.create_sheet("AI 调用"), stats, ai)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _overview(sheet: Worksheet, stats: Stats, batch: Batch | None, ai: AiUsage) -> None:
    sheet.title = "概览"
    totals = stats.totals
    sheet.append(["统计时间", f"{_time(stats.since)} 至 {_time(stats.until)}（北京时间）"])
    sheet.append(["批次", batch.name if batch else "全部批次"])
    sheet.append([])
    header = sheet.max_row + 1
    sheet.append(["环节", "成功", "失败", "成功率"])
    for label, ok, failed in FUNNEL:
        done, lost = getattr(totals, ok), getattr(totals, failed)
        sheet.append([label, done, lost, _rate(done, done + lost)])
    sheet.append([])
    detail = sheet.max_row + 1
    sheet.append(["其他指标", "数值"])
    for conclusion, label in CONCLUSIONS.items():
        sheet.append([f"采集结论：{label}", stats.conclusions.get(conclusion, 0)])
    cached, basis = _cache(ai.rows)
    sheet.append(["锁定的视频作品", totals.videos_found])
    sheet.append(["下载量（MB）", round(totals.downloaded_bytes / MB, 1)])
    sheet.append(["复用已有结果的视频", totals.reused])
    sheet.append(["结果复用率", _rate(totals.reused, totals.analyzed)])
    sheet.append(["AI 输入 token 缓存命中率", _rate(cached, basis) if not ai.error else ai.error])
    _style(sheet, [header, detail], {"A": 28, "B": 44, "C": 12, "D": 12})


def _timeline(sheet: Worksheet, stats: Stats, ai: AiUsage) -> None:
    sheet.append(
        [
            "时段开始",
            "基础信息成功",
            "基础信息失败",
            "视频列表成功",
            "视频列表失败",
            "私密/需登录/找不到",
            "锁定视频",
            "下载成功",
            "下载失败",
            "下载量（MB）",
            "识别成功",
            "其中复用",
            "识别失败",
            "汇总成功",
            "汇总失败",
            "AI 调用",
            "AI 缓存命中率",
        ]
    )
    calls: dict[datetime, list[dict]] = defaultdict(list)
    for row in ai.rows:
        calls[datetime.fromisoformat(row["at"])].append(row)
    for row in stats.timeline:
        usage = calls.get(row.at, [])
        sheet.append(
            [
                _time(row.at, stats.bucket),
                row.profile_ok,
                row.profile_failed,
                row.listing_ok,
                row.listing_failed,
                row.unreachable,
                row.videos_found,
                row.downloaded,
                row.download_failed,
                round(row.downloaded_bytes / MB, 1),
                row.analyzed,
                row.reused,
                row.analysis_failed,
                row.summarized,
                row.summary_failed,
                sum(item["calls"] for item in usage),
                _rate(*_cache(usage)),
            ]
        )
    _style(sheet, [1], {"A": 18}, default_width=13)


def _stages(sheet: Worksheet, stats: Stats) -> None:
    sheet.append(["阶段", "成功", "失败或待核查", "重试", "跳过", "成功率"])
    for row in stats.stages:
        sheet.append(
            [
                STAGES.get(row.stage, row.stage),
                row.succeeded,
                row.failed,
                row.retried,
                row.skipped,
                _rate(row.succeeded, row.succeeded + row.failed),
            ]
        )
    _style(sheet, [1], {"A": 16}, default_width=14)


def _durations(sheet: Worksheet, stats: Stats) -> None:
    sheet.append(["环节", "次数", "平均（秒）", "中位数（秒）", "90% 在（秒）以内"])
    for row in stats.durations:
        sheet.append(
            [
                STAGES.get(row.stage, row.stage),
                row.count,
                round(row.average, 1),
                round(row.median, 1),
                round(row.p90, 1),
            ]
        )
    sheet.append([])
    sheet.append(["达人采集、视频下载从发布任务算起，含排队；分析服务的阶段从开始处理算起。"])
    _style(sheet, [1], {"A": 16}, default_width=16)


def _failures(sheet: Worksheet, stats: Stats) -> None:
    sheet.append(["环节", "原因（多次尝试取最后一次）", "次数"])
    for row in stats.failures:
        sheet.append([STAGES[row.stage], row.reason, row.count])
    _style(sheet, [1], {"A": 14, "B": 90, "C": 10})


def _ai(sheet: Worksheet, stats: Stats, ai: AiUsage) -> None:
    sheet.append(
        [
            "任务",
            "模型",
            "调用",
            "成功",
            "失败或超时",
            "成功率",
            "输入 token",
            "命中缓存 token",
            "缓存命中率",
            "输出 token",
            "平均在途请求",
            "并发利用率",
        ]
    )
    if ai.error:
        sheet.append([ai.error])
    # 平均在途 = 调用占用时长 ÷ 已经过去的时长；时间段还没结束时只算到现在
    seconds = (min(stats.until, utcnow()) - stats.since).total_seconds()
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in ai.rows:
        groups[row["kind"], row["model_id"]].append(row)
    for (kind, model_id), rows in groups.items():
        succeeded, failed = _total(rows, "succeeded"), _total(rows, "failed")
        in_flight = _total(rows, "busy_seconds") / seconds
        capacity = ai.capacity.get(model_id)
        sheet.append(
            [
                AI_KINDS.get(kind, kind),
                rows[0]["model"],
                _total(rows, "calls"),
                succeeded,
                failed,
                _rate(succeeded, succeeded + failed),
                _total(rows, "input_tokens"),
                _total(rows, "cached_tokens"),
                _rate(*_cache(rows)),
                _total(rows, "output_tokens"),
                round(in_flight, 2),
                _rate(in_flight, capacity) if capacity else None,
            ]
        )
    _style(sheet, [1], {"A": 12, "B": 30}, default_width=14)


def _cache(rows: list[dict]) -> tuple[int, int]:
    """命中缓存的输入 token，和报告了命中数的调用的输入 token。"""
    return _total(rows, "cached_tokens"), _total(rows, "cache_input_tokens")


def _total(rows: list[dict], name: str):
    return sum(row[name] for row in rows)


def _rate(part: float, whole: float | None) -> float | None:
    return part / whole if whole else None


def _time(value: datetime, bucket: Bucket = Bucket.HOUR) -> str:
    local = value.astimezone(BEIJING)
    return local.strftime("%Y-%m-%d" if bucket == Bucket.DAY else "%Y-%m-%d %H:%M")


def _style(
    sheet: Worksheet, headers: list[int], widths: dict[str, int], default_width: int = 12
) -> None:
    sheet.sheet_view.showGridLines = False
    for column in range(1, sheet.max_column + 1):
        letter = sheet.cell(1, column).column_letter
        sheet.column_dimensions[letter].width = widths.get(letter, default_width)
    for row in sheet.iter_rows():
        for cell in row:
            if cell.value is None:
                continue
            header = cell.row in headers
            cell.border = BORDER
            cell.font = Font(
                name=FONT, size=10, bold=header, color="FFFFFF" if header else "000000"
            )
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if header:
                cell.fill = HEADER_FILL
            elif isinstance(cell.value, float) and _is_rate(sheet, cell.row, cell.column, headers):
                cell.number_format = PERCENT
    if headers == [1]:
        sheet.freeze_panes = "B2"


def _is_rate(sheet: Worksheet, row: int, column: int, headers: list[int]) -> bool:
    """比例：所在列的表头或所在行的名称里带“率”。"""
    titles = [sheet.cell(header, column).value for header in headers] + [sheet.cell(row, 1).value]
    return any("率" in str(title) for title in titles if title)

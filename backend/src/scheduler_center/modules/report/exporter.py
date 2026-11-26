"""交付表：成功达人、失败达人、待重跑达人三张工作表。

每位达人只出现在一张表中，按名单顺序排列；二维码图片放回原位置，ID 按文本保存，时间为北京时间。
"""

import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.drawing.image import Image
from openpyxl.styles import Alignment, Border, Font, NamedStyle, PatternFill, Side
from openpyxl.worksheet.worksheet import Worksheet

from scheduler_center.core.common.clock import BEIJING
from scheduler_center.modules.batch.models import (
    MAX_WORKS,
    Creator,
    CreatorStatus,
    Work,
    WorkStatus,
)
from scheduler_center.modules.batch.service import LOGIN_REQUIRED_NOTE, restricted_video_outcome

SUCCESS, FAILED, RETRY = "成功达人", "失败达人", "待重跑达人"
TOPIC_MARKS = ("#", "＃")
TAG_ALIASES = {
    "肢体出镜": "仅肢体出镜",
    "女性呈现": "女性",
    "男性呈现": "男性",
    "中性呈现": "中性",
}
OMITTED_TAG_PREFIXES = ("视频时长", "无明显", "不确定", "没有", "未见", "采样未见", "无法", "未知")
OMITTED_TAGS = {
    "不适用",
    "无",
    "无宠物",
    "无小孩",
    "无妆容",
    "无化妆",
    "无美甲",
    "无滤镜",
    "其它",
    "其它室内",
    "其它室外",
    "其它户外",
    "其他",
    "其他室内",
    "其他室外",
    "其他户外",
}
ON_CAMERA_TAGS = {"仅肢体出镜", "无人出镜", "人脸出镜"}
ON_CAMERA_COUNT = re.compile(r"(?:\d+|[一二两三四五六七八九十百千万]+|多)人出镜")
IDENTITY_TAG_SUFFIXES = ("裔", "族", "族群", "国人", "国籍", "公民", "人士")
IDENTITY_TAGS = {"白人", "黑人", "华人", "原住民", "印度人", "移民", "留学生"}
PENDING_REASONS = {
    CreatorStatus.PENDING: "批次尚未开始",
    CreatorStatus.CRAWLING: "尚未爬取完成",
    CreatorStatus.ANALYZING: "作品仍在处理中",
    CreatorStatus.SUMMARIZING: "作品仍在汇总中",
}
QR_PIXELS = 120
QR_ROW_HEIGHT = 96  # 磅，略高于图片
FONT = "微软雅黑"
RED_TAG = InlineFont(rFont=FONT, sz=10, color="FFFF0000")
BORDER = Border(*(Side(style="thin", color="AAB8C5"),) * 4)
HEADER_FILL = PatternFill("solid", fgColor="243B53")


@dataclass(frozen=True)
class CreatorResult:
    creator: Creator
    works: list[Work]
    qr: bytes | None
    crawl_finished_at: datetime | None = None

    def settlement(self) -> tuple[CreatorStatus, str | None]:
        creator = self.creator
        if creator.note == LOGIN_REQUIRED_NOTE:
            return CreatorStatus.RECRAWL, creator.note
        if creator.status not in (CreatorStatus.FAILED, CreatorStatus.INVALID) and (
            outcome := restricted_video_outcome(self.works)
        ):
            return outcome
        return creator.status, creator.note


def sheet_of(result: CreatorResult) -> str:
    """按达人和作品结论分表。需要登录的达人归待重跑。"""
    status, note = result.settlement()
    if note == LOGIN_REQUIRED_NOTE:
        return RETRY
    if status == CreatorStatus.FAILED:
        return FAILED
    if status == CreatorStatus.COMPLETED or any(
        work.status == WorkStatus.COMPLETED for work in result.works
    ):
        return SUCCESS
    return RETRY


def build_workbook(results: Sequence[CreatorResult]) -> bytes:
    workbook = Workbook()
    _register_styles(workbook)
    success = workbook.active
    success.title = SUCCESS
    work_headers = [label for n in range(1, MAX_WORKS + 1) for label in (f"作品{n}", None)]
    totals = ["标签合计（文字去重）", "标签合计（类型去重）", "识别标签成功的视频ID汇总"]
    success.append(
        ["creator_oecuid", "达人名称", "二维码", *work_headers, *totals, "采集/识别的最新时间"]
    )
    success.append([None, None, None, *["链接", "标签"] * MAX_WORKS, None, None, None, None])
    for column in range(1, success.max_column + 1):
        if column in range(4, 4 + 2 * MAX_WORKS, 2):
            success.merge_cells(start_row=1, start_column=column, end_row=1, end_column=column + 1)
        elif not 4 <= column < 4 + 2 * MAX_WORKS:
            success.merge_cells(start_row=1, start_column=column, end_row=2, end_column=column)
    failed = workbook.create_sheet(FAILED)
    failed.append(["creator_oecuid", "达人名称", "二维码", "标签/原因", "采集/识别的最新时间"])
    retry = workbook.create_sheet(RETRY)
    retry.append(["creator_oecuid", "达人名称", "二维码", "待重跑原因", "采集/识别的最新时间"])
    sheets = {SUCCESS: success, FAILED: failed, RETRY: retry}
    qr_rows: dict[str, set[int]] = {title: set() for title in sheets}
    last_rows = {title: sheet.max_row for title, sheet in sheets.items()}

    for result in results:
        creator, sheet = result.creator, sheets[sheet_of(result)]
        time = creator.updated_at.astimezone(BEIJING).replace(tzinfo=None)
        if sheet is success:
            details = _success_cells(result)
        elif sheet is failed:
            details = [creator.note]
        else:
            status, note = result.settlement()
            details = [note or PENDING_REASONS.get(status)]
        sheet.append([creator.oecuid, creator.name, None, *details, time])
        last_rows[sheet.title] += 1
        if result.qr:
            row_number = last_rows[sheet.title]
            try:
                image = Image(BytesIO(result.qr))
            except OSError:  # 名单里的图片格式读不出来：写明原因，其余照常导出
                sheet.cell(row_number, 3, "二维码无法读取")
            else:
                image.width = image.height = QR_PIXELS
                sheet.add_image(image, f"C{row_number}")
                qr_rows[sheet.title].add(row_number)

    _style(success, header_rows=2, widths={"A": 26, "B": 28, "C": 28, "N": 60, "O": 60, "P": 40})
    for column in "DFHJL":
        success.column_dimensions[column].width = 30
    for column in "EGIKM":
        success.column_dimensions[column].width = 40
    for sheet in (failed, retry):
        _style(sheet, header_rows=1, widths={"A": 26, "B": 28, "C": 28, "D": 60})
        sheet.auto_filter.ref = sheet.dimensions
    for title, sheet in sheets.items():
        _fit_rows(sheet, qr_rows[title])

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _success_cells(result: CreatorResult) -> list[str | CellRichText | None]:
    creator, works = result.creator, result.works
    by_position = {work.position: work for work in works}
    shortage = f"作品不足五条（实际 {len(works)} 条）" if 0 < len(works) < MAX_WORKS else None
    cells: list[str | CellRichText | None] = []
    for position in range(1, MAX_WORKS + 1):
        work = by_position.get(position)
        cells += [work.url, _work_tag(work)] if work else [None, shortage]
    succeeded = [work for work in works if work.status == WorkStatus.COMPLETED]
    text_tags = creator.labels + [tag for work in succeeded for tag in work.tags or []]
    typed_tags: list[str] = []
    status, note = result.settlement()
    if status == CreatorStatus.COMPLETED:
        summary_tags = creator.tags if creator.status == CreatorStatus.COMPLETED else None
        typed_tags = _summary_tags(creator.labels + (summary_tags or []))
        display_tags = typed_tags.copy()
        inactivity_tag = creator.work_inactivity_tag(works, result.crawl_finished_at)
        if inactivity_tag and inactivity_tag not in display_tags:
            display_tags.append(inactivity_tag)
        typed_text = "、".join(display_tags) or None
    else:
        typed_text = note or "汇总未完成"
    ids = "\n".join(work.platform_id for work in succeeded)
    return [*cells, _text_tag_cell(text_tags, typed_tags), typed_text, ids or None]


def _work_tag(work: Work) -> str:
    match work.status:
        case WorkStatus.SKIPPED:
            return "图文作者"
        case WorkStatus.COMPLETED:
            tags = dict.fromkeys(_export_tags(work.tags or []))
            if "人脸出镜" in tags:
                tags.pop("仅肢体出镜", None)
            return "、".join(tags) or "未识别出标签"
        case WorkStatus.DOWNLOADING | WorkStatus.ANALYZING:
            return "处理中"
        case WorkStatus.ABANDONED:
            return "已放弃"
    return work.error or "失败"


def _export_tags(tags: list[str]) -> list[str]:
    exported = []
    for raw_tag in tags:
        tag = raw_tag.strip()
        if not tag or tag in OMITTED_TAGS or tag.startswith(OMITTED_TAG_PREFIXES):
            continue
        tag = TAG_ALIASES.get(tag, tag)
        if (
            tag.endswith("出镜")
            and not tag.startswith(TOPIC_MARKS)
            and tag not in ON_CAMERA_TAGS
            and not ON_CAMERA_COUNT.fullmatch(tag)
        ):
            continue
        exported.append(tag)
    return exported


def _summary_tags(tags: list[str]) -> list[str]:
    unique = dict.fromkeys(_export_tags(tags))
    conflicting_negatives = {f"无{tag[1:]}" for tag in unique if tag.startswith("有")} - {
        "无人出镜",
        "无口播",
    }
    return sorted(
        (tag for tag in unique if tag not in conflicting_negatives),
        key=lambda tag: tag.startswith(TOPIC_MARKS),
    )


def _type_match_key(tag: str) -> str:
    """仅在匹配标红时，允许身份标签带话题前缀。"""
    if tag.startswith(TOPIC_MARKS):
        value = tag[1:].lstrip()
        if value in IDENTITY_TAGS or value.endswith(IDENTITY_TAG_SUFFIXES):
            return value
    return tag


def _text_tag_cell(tags: list[str], typed_tags: list[str]) -> str | CellRichText | None:
    ordered = _summary_tags(tags)
    typed_keys = {_type_match_key(tag) for tag in typed_tags}
    missing = {tag for tag in ordered if _type_match_key(tag) not in typed_keys}
    if not missing:
        return "、".join(ordered) or None

    runs: list[str | TextBlock] = []
    plain: list[str] = []
    for index, tag in enumerate(ordered):
        if index:
            plain.append("、")
        if tag not in missing:
            plain.append(tag)
        else:
            if plain:
                runs.append("".join(plain))
                plain.clear()
            runs.append(TextBlock(RED_TAG, tag))
    if plain:
        runs.append("".join(plain))
    return CellRichText(runs)


def _register_styles(workbook: Workbook) -> None:
    """同一工作簿只注册一次样式，避免逐单元格去重。"""
    body = dict(
        border=BORDER,
        font=Font(name=FONT, size=10, color="000000"),
        alignment=Alignment(horizontal="left", vertical="top", wrap_text=True),
    )
    styles = (
        NamedStyle(
            name="导出表头",
            border=BORDER,
            font=Font(name=FONT, size=10, bold=True, color="FFFFFF"),
            alignment=Alignment(horizontal="center", vertical="center", wrap_text=True),
            fill=HEADER_FILL,
        ),
        NamedStyle(name="导出正文", **body),
        NamedStyle(name="导出时间", number_format="yyyy-mm-dd hh:mm:ss", **body),
        NamedStyle(name="导出编号", number_format="@", **body),
        NamedStyle(
            name="导出链接",
            border=BORDER,
            font=Font(name=FONT, size=10, color="0563C1", underline="single"),
            alignment=body["alignment"],
        ),
    )
    for style in styles:
        workbook.add_named_style(style)


def _style(sheet: Worksheet, header_rows: int, widths: dict[str, int]) -> None:
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = f"D{header_rows + 1}"
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    last = sheet.max_column
    for row in sheet.iter_rows():
        for cell in row:
            if cell.row <= header_rows:
                cell.style = "导出表头"
            elif cell.column == last:
                cell.style = "导出时间"
            elif cell.column == 1 or (sheet.title == SUCCESS and cell.column == last - 1):
                cell.style = "导出编号"  # ID 按文本保存
            elif sheet.title == SUCCESS and isinstance(cell.value, str) and _is_link(cell.column):
                cell.hyperlink = cell.value
                cell.style = "导出链接"
            else:
                cell.style = "导出正文"


def _is_link(column: int) -> bool:
    return 4 <= column < 4 + 2 * MAX_WORKS and column % 2 == 0


def _fit_rows(sheet: Worksheet, qr_rows: set[int]) -> None:
    """按换行和列宽估算行高；有二维码的行留出图片高度。"""
    for row in sheet.iter_rows():
        number = row[0].row
        lines = 1
        for cell in row:
            if isinstance(cell.value, (str, CellRichText)):
                width = sheet.column_dimensions[cell.column_letter].width or 10
                lines = max(
                    lines,
                    sum(
                        math.ceil(_units(part) / width) or 1 for part in str(cell.value).split("\n")
                    ),
                )
        height = max(24, lines * 15 + 6)
        sheet.row_dimensions[number].height = min(
            409, max(height, QR_ROW_HEIGHT if number in qr_rows else 0)
        )


def _units(text: str) -> int:
    """全角字符占两个字符宽度。"""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)

"""交付表：成功识别、失败账号、重新抓取三张工作表。

每位达人只出现在一张表中，按名单顺序排列；二维码图片放回原位置，ID 按文本保存，时间为北京时间。
"""

import math
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO

from openpyxl import Workbook
from openpyxl.drawing.image import Image
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.worksheet import Worksheet

from scheduler_center.core.common.clock import BEIJING
from scheduler_center.modules.batch.models import (
    MAX_WORKS,
    Creator,
    CreatorStatus,
    Work,
    WorkStatus,
)

SUCCESS, FAILED, RETRY = "成功识别", "失败账号", "重新抓取"
PENDING_REASONS = {
    CreatorStatus.PENDING: "批次尚未开始",
    CreatorStatus.CRAWLING: "尚未爬取完成",
    CreatorStatus.ANALYZING: "作品仍在处理中",
    CreatorStatus.SUMMARIZING: "作品仍在汇总中",
}
QR_PIXELS = 120
QR_ROW_HEIGHT = 96  # 磅，略高于图片
FONT = "微软雅黑"
BORDER = Border(*(Side(style="thin", color="AAB8C5"),) * 4)
HEADER_FILL = PatternFill("solid", fgColor="243B53")


@dataclass(frozen=True)
class CreatorResult:
    creator: Creator
    works: list[Work]
    qr: bytes | None


def sheet_of(result: CreatorResult) -> str:
    """私密或找不到的进失败账号；有结论或至少一条视频成功的进成功识别；其余需要重新抓取。"""
    creator = result.creator
    if creator.status == CreatorStatus.FAILED:
        return FAILED
    if creator.status == CreatorStatus.COMPLETED or any(
        work.status == WorkStatus.COMPLETED for work in result.works
    ):
        return SUCCESS
    return RETRY


def build_workbook(results: Sequence[CreatorResult]) -> bytes:
    workbook = Workbook()
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
    retry.append(["creator_oecuid", "达人名称", "二维码", "重新抓取原因", "采集/识别的最新时间"])
    sheets = {SUCCESS: success, FAILED: failed, RETRY: retry}
    qr_rows: dict[str, set[int]] = {title: set() for title in sheets}

    for result in results:
        creator, sheet = result.creator, sheets[sheet_of(result)]
        time = creator.updated_at.astimezone(BEIJING).replace(tzinfo=None)
        if sheet is success:
            details = _success_cells(result)
        elif sheet is failed:
            details = [creator.note]
        else:
            details = [creator.note or PENDING_REASONS.get(creator.status)]
        sheet.append([creator.oecuid, creator.name, None, *details, time])
        if result.qr:
            image = Image(BytesIO(result.qr))
            image.width = image.height = QR_PIXELS
            sheet.add_image(image, f"C{sheet.max_row}")
            qr_rows[sheet.title].add(sheet.max_row)

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


def _success_cells(result: CreatorResult) -> list[str | None]:
    creator, works = result.creator, result.works
    by_position = {work.position: work for work in works}
    shortage = f"作品不足五条（实际 {len(works)} 条）" if 0 < len(works) < MAX_WORKS else None
    cells: list[str | None] = []
    for position in range(1, MAX_WORKS + 1):
        work = by_position.get(position)
        cells += [work.url, _work_tag(work)] if work else [None, shortage]
    succeeded = [work for work in works if work.status == WorkStatus.COMPLETED]
    text_tags = _unique(creator.labels + [tag for work in succeeded for tag in work.tags or []])
    if creator.status == CreatorStatus.COMPLETED:
        typed_tags = _unique(creator.labels + (creator.tags or []))
    else:
        typed_tags = creator.note or "汇总未完成"
    ids = "\n".join(work.platform_id for work in succeeded)
    return [*cells, text_tags, typed_tags, ids or None]


def _work_tag(work: Work) -> str:
    match work.status:
        case WorkStatus.SKIPPED:
            return "图文作者"
        case WorkStatus.COMPLETED:
            return "、".join(work.tags or []) or "未识别出标签"
        case WorkStatus.DOWNLOADING | WorkStatus.ANALYZING:
            return "处理中"
        case WorkStatus.ABANDONED:
            return "已放弃"
    return work.error or "失败"


def _unique(tags: list[str]) -> str | None:
    return "、".join(dict.fromkeys(tags)) or None


def _style(sheet: Worksheet, header_rows: int, widths: dict[str, int]) -> None:
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = f"D{header_rows + 1}"
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    last = sheet.max_column
    for row in sheet.iter_rows():
        for cell in row:
            header = cell.row <= header_rows
            cell.border = BORDER
            cell.font = Font(
                name=FONT, size=10, bold=header, color="FFFFFF" if header else "000000"
            )
            cell.alignment = Alignment(
                horizontal="center" if header else "left",
                vertical="center" if header else "top",
                wrap_text=True,
            )
            if header:
                cell.fill = HEADER_FILL
            elif cell.column == last:
                cell.number_format = "yyyy-mm-dd hh:mm:ss"
            elif cell.column == 1 or (sheet.title == SUCCESS and cell.column == last - 1):
                cell.number_format = "@"  # ID 按文本保存
            elif sheet.title == SUCCESS and isinstance(cell.value, str) and _is_link(cell.column):
                cell.hyperlink = cell.value
                cell.font = Font(name=FONT, size=10, color="0563C1", underline="single")


def _is_link(column: int) -> bool:
    return 4 <= column < 4 + 2 * MAX_WORKS and column % 2 == 0


def _fit_rows(sheet: Worksheet, qr_rows: set[int]) -> None:
    """按换行和列宽估算行高；有二维码的行留出图片高度。"""
    for row in sheet.iter_rows():
        number = row[0].row
        lines = 1
        for cell in row:
            if isinstance(cell.value, str):
                width = sheet.column_dimensions[cell.column_letter].width or 10
                lines = max(
                    lines,
                    sum(math.ceil(_units(part) / width) or 1 for part in cell.value.split("\n")),
                )
        height = max(24, lines * 15 + 6)
        sheet.row_dimensions[number].height = min(
            409, max(height, QR_ROW_HEIGHT if number in qr_rows else 0)
        )


def _units(text: str) -> int:
    """全角字符占两个字符宽度。"""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)

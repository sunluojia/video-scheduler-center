"""达人名单的读取：Excel 名单或粘贴的主页链接。

Excel 读取第一张表头含“达人名称”的工作表，取“达人名称”“creator_oecuid”“二维码”三列。
二维码图片有两种存法：WPS 的单元格内图片（公式 DISPIMG 指向 xl/cellimages.xml），
以及浮动在单元格上的图片，两种都读取。
"""

import posixpath
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import unquote
from xml.etree import ElementTree

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from scheduler_center.core.common.errors import Invalid

MAX_ROWS = 10_000
NAME, OECUID, QR = "达人名称", "creator_oecuid", "二维码"
PROFILE = re.compile(r"https?://(?:www\.|m\.)?tiktok\.com/@([^/?#\s]+)", re.IGNORECASE)
HANDLE = re.compile(r"[A-Za-z0-9_.]{1,64}")
DISPIMG = re.compile(r'DISPIMG\(\s*"([^"]+)"', re.IGNORECASE)
RELATIONSHIP = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif"}


class RosterError(Invalid):
    """名单无法读取。"""


@dataclass(frozen=True)
class Image:
    content: bytes
    media_type: str


@dataclass(frozen=True)
class ImportRow:
    row: int | None
    """原表格的行号；粘贴链接时为空。"""
    name: str
    handle: str | None
    oecuid: str | None = None
    qr: Image | None = None


def parse_handle(value: str) -> str | None:
    """账号名、@账号或 TikTok 主页链接；无法识别时返回 None。"""
    text = value.strip()
    if match := PROFILE.match(text):
        text = unquote(match.group(1))
    text = text.removeprefix("@")
    return text.casefold() if HANDLE.fullmatch(text) else None


def read_roster(
    filename: str | None, content: bytes | None, links: str | None
) -> tuple[list[ImportRow], str]:
    """返回名单和来源说明。"""
    if content is not None:
        if not (filename or "").lower().endswith(".xlsx"):
            raise RosterError("请上传 .xlsx 格式的 Excel 名单")
        return read_workbook(content), filename
    if links and links.strip():
        return read_links(links), "粘贴链接"
    raise RosterError("请上传名单文件或粘贴主页链接")


def read_links(text: str) -> list[ImportRow]:
    rows = [ImportRow(None, line, parse_handle(line)) for line in _lines(text)]
    return _checked(rows)


def read_workbook(content: bytes) -> list[ImportRow]:
    try:
        workbook = load_workbook(BytesIO(content))
    except (zipfile.BadZipFile, KeyError, ValueError) as error:
        raise RosterError("无法读取文件，请上传 .xlsx 格式的 Excel 名单") from error
    sheet = next((s for s in workbook.worksheets if NAME in _header(s)), None)
    if sheet is None:
        raise RosterError("没有找到表头含“达人名称”的工作表")
    header = _header(sheet)
    name_col, oecuid_col, qr_col = (header.get(label) for label in (NAME, OECUID, QR))
    cell_images = _cell_images(content)
    floating = {
        (image.anchor._from.row + 1, image.anchor._from.col + 1): _image(
            image._data(), image.format
        )
        for image in sheet._images
    }
    rows = []
    for cells in sheet.iter_rows(min_row=2):
        values = [cell.value for cell in cells]
        if all(value is None or str(value).strip() == "" for value in values):
            continue
        number = cells[0].row
        name = _text(values[name_col - 1]) if name_col <= len(values) else ""
        oecuid = _text(values[oecuid_col - 1]) if oecuid_col and oecuid_col <= len(values) else ""
        qr = None
        if qr_col:
            formula = values[qr_col - 1] if qr_col <= len(values) else None
            found = DISPIMG.search(formula) if isinstance(formula, str) else None
            qr = cell_images.get(found.group(1)) if found else floating.get((number, qr_col))
        rows.append(ImportRow(number, name, parse_handle(name), oecuid or None, qr))
    return _checked(rows)


def _header(sheet: Worksheet) -> dict[str, int]:
    first = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
    return {str(value).strip(): index for index, value in enumerate(first, 1) if value is not None}


def _cell_images(content: bytes) -> dict[str, Image]:
    """WPS 单元格内图片：cellimages.xml 中图片名 → 关系 ID → media 下的文件。"""
    with zipfile.ZipFile(BytesIO(content)) as archive:
        names = set(archive.namelist())
        if "xl/cellimages.xml" not in names:
            return {}
        targets = {
            relation.get("Id"): relation.get("Target")
            for relation in ElementTree.fromstring(archive.read("xl/_rels/cellimages.xml.rels"))
        }
        images = {}
        for picture in ElementTree.fromstring(archive.read("xl/cellimages.xml")).iter():
            if not picture.tag.endswith("}pic"):
                continue
            name = next(e.get("name") for e in picture.iter() if e.tag.endswith("}cNvPr"))
            embed = next(
                e.get(f"{RELATIONSHIP}embed") for e in picture.iter() if e.tag.endswith("}blip")
            )
            path = posixpath.normpath(posixpath.join("xl", targets[embed]))
            images[name] = _image(archive.read(path), path.rsplit(".", 1)[-1])
        return images


def _image(content: bytes, extension: str | None) -> Image:
    return Image(content, MEDIA_TYPES.get((extension or "").lower(), "image/png"))


def _text(value: object) -> str:
    """数字形式的 ID 按整数文本保存，避免变成 1.23E+18 或 123.0。"""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return "" if value is None else str(value).strip()


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _checked(rows: list[ImportRow]) -> list[ImportRow]:
    if not rows:
        raise RosterError("名单中没有达人")
    if len(rows) > MAX_ROWS:
        raise RosterError(f"单个批次最多 {MAX_ROWS} 位达人")
    return rows

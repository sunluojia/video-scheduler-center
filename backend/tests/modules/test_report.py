"""名单读取与交付表导出。"""

import io
import zipfile
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.drawing.image import Image as SheetImage
from PIL import Image

from scheduler_center.modules.batch.models import Creator, CreatorStatus, Work, WorkKind, WorkStatus
from scheduler_center.modules.report.exporter import CreatorResult, build_workbook
from scheduler_center.modules.report.importer import (
    RosterError,
    parse_handle,
    read_links,
    read_roster,
    read_workbook,
)

HEADER = ["备注", "达人名称", "creator_oecuid", "二维码"]


def png(color: str = "red") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(output, "PNG")
    return output.getvalue()


def workbook_bytes(*rows: list, floating: dict[str, bytes] | None = None) -> bytes:
    book = Workbook()
    book.active.title = "说明"
    sheet = book.create_sheet("名单")
    sheet.append(HEADER)
    for row in rows:
        sheet.append(row)
    for anchor, content in (floating or {}).items():
        sheet.add_image(SheetImage(io.BytesIO(content)), anchor)
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def with_cell_images(content: bytes, images: dict[str, bytes]) -> bytes:
    """按 WPS 的方式加入单元格内图片：cellimages.xml、关系文件和 media 下的图片。"""
    pictures, relations = [], []
    for index, name in enumerate(images, 1):
        pictures.append(
            f'<etc:cellImage><xdr:pic><xdr:nvPicPr><xdr:cNvPr id="{index}" name="{name}"/>'
            f'</xdr:nvPicPr><xdr:blipFill><a:blip r:embed="rId{index}"/></xdr:blipFill>'
            "</xdr:pic></etc:cellImage>"
        )
        relations.append(
            f'<Relationship Id="rId{index}" Target="media/cell{index}.png" Type='
            '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"/>'
        )
    cell_images = (
        '<etc:cellImages xmlns:etc="http://www.wps.cn/officeDocument/2017/etCustomData" '
        'xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"{''.join(pictures)}</etc:cellImages>"
    )
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{''.join(relations)}</Relationships>"
    )
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(content)) as source,
        zipfile.ZipFile(output, "w") as target,
    ):
        for item in source.infolist():
            target.writestr(item, source.read(item))
        target.writestr("xl/cellimages.xml", cell_images)
        target.writestr("xl/_rels/cellimages.xml.rels", rels)
        for index, image in enumerate(images.values(), 1):
            target.writestr(f"xl/media/cell{index}.png", image)
    return output.getvalue()


@pytest.mark.parametrize(
    ("value", "handle"),
    [
        ("Alice", "alice"),
        ("@bob.k", "bob.k"),
        ("https://www.tiktok.com/@Carol_1?lang=en", "carol_1"),
        ("https://m.tiktok.com/@dave/video/123", "dave"),
        ("https://example.com/@alice", None),
        ("两个 词", None),
    ],
)
def test_handles_are_parsed_from_names_and_links(value, handle):
    assert parse_handle(value) == handle


def test_workbook_reads_ids_as_text_and_both_kinds_of_qr_codes():
    cell_qr, floating_qr = png("red"), png("blue")
    content = with_cell_images(
        workbook_bytes(
            ["", "Alice", 1234567890.0, '=_xlfn.DISPIMG("ID_QR_1",1)'],
            [None, None, None, None],
            ["", "@bob", "00123", None],
            ["", "不是账号!", None, None],
            floating={"D4": floating_qr},
        ),
        {"ID_QR_1": cell_qr},
    )

    rows = read_workbook(content)

    assert [(r.row, r.name, r.handle, r.oecuid) for r in rows] == [
        (2, "Alice", "alice", "1234567890"),
        (4, "@bob", "bob", "00123"),
        (5, "不是账号!", None, None),
    ]
    assert [r.qr and r.qr.content for r in rows] == [cell_qr, floating_qr, None]


def test_roster_problems_are_reported():
    other = Workbook()
    other.active.append(["其他列"])
    output = io.BytesIO()
    other.save(output)
    with pytest.raises(RosterError, match="达人名称"):
        read_workbook(output.getvalue())
    with pytest.raises(RosterError, match="没有达人"):
        read_workbook(workbook_bytes())
    with pytest.raises(RosterError, match=r".xlsx"):
        read_roster("名单.xls", b"...", None)
    with pytest.raises(RosterError, match="没有达人"):
        read_links("\n  \n")
    assert [row.handle for row in read_links("alice\n\n@bob\n")] == ["alice", "bob"]


def test_export_puts_each_creator_in_one_sheet_in_roster_order():
    now = datetime(2026, 11, 17, 4, 0, tzinfo=UTC)
    done = creator(1, "done", CreatorStatus.COMPLETED, tags=["美妆博主"], labels=["图文作者"])
    private = creator(2, "private", CreatorStatus.FAILED, note="私密账号")
    broken = creator(3, "broken", CreatorStatus.RECRAWL, note="爬取失败：作品列表等待响应超时")
    partial = creator(4, "partial", CreatorStatus.ANALYZING)
    locked = creator(5, "locked", CreatorStatus.FAILED, note="需要登录")
    results = [
        CreatorResult(
            done,
            [
                work(done, 1, WorkStatus.COMPLETED, tags=["美妆", "#口红推荐", "口红"]),
                work(done, 2, WorkStatus.SKIPPED, kind=WorkKind.PHOTO),
                work(done, 3, WorkStatus.FAILED, error="下载失败：链接失效"),
            ],
            png(),
        ),
        CreatorResult(private, [], None),
        CreatorResult(broken, [], png()),
        CreatorResult(
            partial,
            [
                work(partial, 1, WorkStatus.COMPLETED, tags=["#穿搭", "穿搭"]),
                work(partial, 2, WorkStatus.COMPLETED, tags=["通勤", "#OOTD"]),
            ],
            None,
        ),
        CreatorResult(locked, [], b"not an image"),
    ]
    for result in results:
        result.creator.updated_at = now

    book = load_workbook(io.BytesIO(build_workbook(results)))

    assert book.sheetnames == ["成功达人", "失败达人", "待重跑达人"]
    success, failed, retry = book["成功达人"], book["失败达人"], book["待重跑达人"]
    rows = list(success.iter_rows(min_row=3, values_only=True))
    assert [row[1] for row in rows] == ["done", "partial"]
    first = rows[0]
    assert first[0] == "7000000000000000001"
    assert first[3:9] == (
        "https://www.tiktok.com/@done/video/1",
        "美妆、#口红推荐、口红",
        "https://www.tiktok.com/@done/video/2",
        "图文作者",
        "https://www.tiktok.com/@done/video/3",
        "下载失败：链接失效",
    )
    assert first[9:13] == (None, "作品不足五条（实际 3 条）", None, "作品不足五条（实际 3 条）")
    assert first[13:16] == ("图文作者、美妆、口红、#口红推荐", "图文作者、美妆博主", "1")
    assert first[16] == datetime(2026, 11, 17, 12, 0)  # 北京时间
    assert rows[1][13] == "穿搭、通勤、#穿搭、#OOTD"  # 文字去重：带 # 的话题放在最后
    assert rows[1][14] == "汇总未完成"
    assert [row[1:4:2] for row in failed.iter_rows(min_row=2, values_only=True)] == [
        ("private", "私密账号"),
        ("locked", "需要登录"),
    ]
    assert failed["C3"].value == "二维码无法读取"
    assert retry["D1"].value == "待重跑原因"
    assert retry["D2"].value == "爬取失败：作品列表等待响应超时"
    assert (len(success._images), len(retry._images)) == (1, 1)
    assert success["A3"].number_format == "@"


def test_export_sorts_topics_and_colors_only_tags_missing_from_type_summary():
    owner = creator(
        1,
        "done",
        CreatorStatus.COMPLETED,
        labels=["图文作者", " 视频时长1分钟"],
        tags=["＃测评", "健身", "视频时长15秒", "  视频时长15–59秒"],
    )
    entry = work(
        owner,
        1,
        WorkStatus.COMPLETED,
        tags=["#健身", "瑜伽", "健身", "＃测评", "视频时长15秒", "\t视频时长30秒", "口播"],
    )
    data = build_workbook([CreatorResult(owner, [entry], None)])
    sheet = load_workbook(io.BytesIO(data), rich_text=True)["成功达人"]

    assert sheet["O3"].value == "图文作者、健身、＃测评"
    assert sheet["E3"].value == "#健身、瑜伽、健身、＃测评、口播"
    text = sheet["N3"].value
    assert isinstance(text, CellRichText)
    assert str(text) == "图文作者、瑜伽、健身、口播、#健身、＃测评"
    assert [(run.text, run.font.color.rgb) for run in text if isinstance(run, TextBlock)] == [
        ("瑜伽", "FFFF0000"),
        ("口播", "FFFF0000"),
        ("#健身", "FFFF0000"),
    ]


def creator(position: int, name: str, status: CreatorStatus, **fields) -> Creator:
    value = Creator(uuid4(), position, position + 1, name, name, f"700000000000000000{position}")
    value.status = status
    for key, item in fields.items():
        setattr(value, key, item)
    return value


def work(owner: Creator, position: int, status: WorkStatus, **fields) -> Work:
    value = Work(
        owner.batch_id,
        owner.id,
        position,
        str(position),
        f"https://www.tiktok.com/@{owner.name}/video/{position}",
        fields.pop("kind", WorkKind.VIDEO),
    )
    value.status = status
    for key, item in fields.items():
        setattr(value, key, item)
    return value

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
    ]
    assert retry["D1"].value == "待重跑原因"
    assert [row[1:4:2] for row in retry.iter_rows(min_row=2, values_only=True)] == [
        ("broken", "爬取失败：作品列表等待响应超时"),
        ("locked", "需要登录"),
    ]
    assert retry["C3"].value == "二维码无法读取"
    assert (len(success._images), len(retry._images)) == (1, 1)
    assert success["A3"].number_format == "@"


def test_export_classifies_saved_restricted_results_by_their_works():
    reason = "下载失败：受限视频（服务端未返回播放地址）"
    with_photo = creator(1, "photos", CreatorStatus.RECRAWL, labels=["图文作者"])
    only_video = creator(2, "video", CreatorStatus.RECRAWL, note="视频全部失败，需要重新抓取")
    book = load_workbook(
        io.BytesIO(
            build_workbook(
                [
                    CreatorResult(
                        with_photo,
                        [
                            work(with_photo, 1, WorkStatus.SKIPPED, kind=WorkKind.PHOTO),
                            work(with_photo, 2, WorkStatus.FAILED, error=reason),
                        ],
                        None,
                    ),
                    CreatorResult(
                        only_video, [work(only_video, 1, WorkStatus.FAILED, error=reason)], None
                    ),
                ]
            )
        )
    )
    assert book["成功达人"]["B3"].value == "photos"
    assert book["成功达人"]["G3"].value == reason
    assert book["成功达人"]["O3"].value == "图文作者"
    assert book["待重跑达人"]["B2"].value == "video"
    assert "服务端未返回播放地址" in book["待重跑达人"]["D2"].value


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


def test_export_cleans_historical_tags_in_works_and_both_totals():
    hidden = [
        "  无明显美甲 ",
        "不确定场景",
        "没有美甲",
        "未见小孩",
        "采样未见健身",
        "无宠物",
        "无小孩",
        "无妆容",
        "无滤镜",
        "无法判断肤色",
        "动物出镜",
        "宠物出镜",
        "儿童出镜",
        "无人脸出镜",
        "青少年出镜",
        "真人出镜",
        "其它",
        "其它室内",
        "其它室外",
        "其它户外",
        "其他",
        "其他室内",
        "其他室外",
        "其他户外",
    ]
    tags = [*hidden, "女性呈现", "男性呈现", "中性呈现", "有宠物", "有小孩", "无袖上衣"]
    other_tags = ["无人出镜", "无口播"]
    owner = creator(1, "done", CreatorStatus.COMPLETED, tags=tags + other_tags)
    works = [
        work(owner, 1, WorkStatus.COMPLETED, tags=tags),
        work(owner, 2, WorkStatus.COMPLETED, tags=other_tags),
        work(owner, 3, WorkStatus.COMPLETED, tags=hidden),
    ]

    data = build_workbook([CreatorResult(owner, works, None)])
    sheet = load_workbook(io.BytesIO(data), rich_text=True)["成功达人"]

    assert sheet["E3"].value == "女性、男性、中性、有宠物、有小孩、无袖上衣"
    assert sheet["G3"].value == "无人出镜、无口播"
    assert sheet["I3"].value == "未识别出标签"
    expected_totals = "女性、男性、中性、有宠物、有小孩、无袖上衣、无人出镜、无口播"
    assert sheet["N3"].value == sheet["O3"].value == expected_totals
    assert "没有美甲" in (works[0].tags or [])
    assert "女性呈现" in (owner.tags or [])


def test_export_keeps_body_and_face_tags_in_type_summary_but_not_in_the_same_work():
    face_tags = ["肢体出镜", "人脸出镜", "仅肢体出镜", "2人出镜"]
    body_tags = ["肢体出镜", "仅肢体出镜", "多人出镜"]
    owner = creator(1, "done", CreatorStatus.COMPLETED, tags=face_tags + body_tags)
    works = [
        work(owner, 1, WorkStatus.COMPLETED, tags=face_tags),
        work(owner, 2, WorkStatus.COMPLETED, tags=body_tags),
    ]
    data = build_workbook([CreatorResult(owner, works, None)])
    sheet = load_workbook(io.BytesIO(data))["成功达人"]

    assert sheet["E3"].value == "人脸出镜、2人出镜"
    assert sheet["G3"].value == "仅肢体出镜、多人出镜"
    assert sheet["O3"].value == "仅肢体出镜、人脸出镜、2人出镜、多人出镜"


def test_export_totals_prefer_positive_existence_tags():
    negative = ["无明显美甲", "没有美甲", "无美甲", "无配饰"]
    positive = ["有美甲", "有配饰"]
    owner = creator(1, "done", CreatorStatus.COMPLETED, tags=negative + positive)
    works = [
        work(owner, 1, WorkStatus.COMPLETED, tags=negative),
        work(owner, 2, WorkStatus.COMPLETED, tags=positive),
    ]
    data = build_workbook([CreatorResult(owner, works, None)])
    sheet = load_workbook(io.BytesIO(data), rich_text=True)["成功达人"]

    assert sheet["N3"].value == sheet["O3"].value == "有美甲、有配饰"


@pytest.mark.parametrize(
    ("summary_tag", "work_tag", "expected_types"),
    [
        ("拉丁裔", "#拉丁裔", "拉丁裔、美妆"),
        ("#拉丁裔", "#拉丁裔", "美妆、#拉丁裔"),
        ("＃拉丁裔", "#拉丁裔", "美妆、＃拉丁裔"),
        ("玛雅族", "#玛雅族", "玛雅族、美妆"),
        ("美国籍", "＃美国籍", "美国籍、美妆"),
        ("留学生", "#留学生", "留学生、美妆"),
    ],
)
def test_export_matches_identity_topics_to_type_summary(summary_tag, work_tag, expected_types):
    owner = creator(1, "done", CreatorStatus.COMPLETED, tags=[summary_tag, "美妆"])
    entry = work(
        owner,
        1,
        WorkStatus.COMPLETED,
        tags=[work_tag, "#热门", "美妆", "#好物分享", "#TikTokShop"],
    )
    data = build_workbook([CreatorResult(owner, [entry], None)])
    sheet = load_workbook(io.BytesIO(data), rich_text=True)["成功达人"]

    assert sheet["O3"].value == expected_types
    assert sheet["E3"].value == f"{work_tag}、#热门、美妆、#好物分享、#TikTokShop"
    text = sheet["N3"].value
    assert isinstance(text, CellRichText)
    assert str(text) == f"美妆、{work_tag}、#热门、#好物分享、#TikTokShop"
    assert [(run.text, run.font.color.rgb) for run in text if isinstance(run, TextBlock)] == [
        ("#热门", "FFFF0000"),
        ("#好物分享", "FFFF0000"),
        ("#TikTokShop", "FFFF0000"),
    ]


def test_export_type_summary_uses_only_creator_tags_and_labels():
    owner = creator(1, "done", CreatorStatus.COMPLETED, labels=["图文作者"])
    entry = work(owner, 1, WorkStatus.COMPLETED, tags=["#拉丁裔", "#热门"])
    data = build_workbook([CreatorResult(owner, [entry], None)])
    sheet = load_workbook(io.BytesIO(data))["成功达人"]

    assert sheet["O3"].value == "图文作者"
    assert sheet["N3"].value == "图文作者、#拉丁裔、#热门"


@pytest.mark.parametrize("already_present", [False, True])
def test_export_adds_inactivity_only_to_completed_type_summary(already_present):
    tag = "3个月内未更新"
    tags = ["穿搭", tag] if already_present else ["穿搭"]
    done = creator(1, "done", CreatorStatus.COMPLETED, labels=["图文作者"], tags=tags)
    partial = creator(2, "partial", CreatorStatus.ANALYZING)
    crawl_finished_at = datetime(2024, 4, 30, 2, tzinfo=UTC)
    published_at = datetime(2024, 1, 31, 2, tzinfo=UTC)
    results = [
        CreatorResult(
            done,
            [
                work(done, 1, WorkStatus.COMPLETED, published_at=published_at, tags=["穿搭"]),
                work(done, 2, WorkStatus.SKIPPED, kind=WorkKind.PHOTO),
            ],
            None,
            crawl_finished_at=crawl_finished_at,
        ),
        CreatorResult(
            partial,
            [work(partial, 1, WorkStatus.COMPLETED, published_at=published_at, tags=["穿搭"])],
            None,
            crawl_finished_at=crawl_finished_at,
        ),
    ]

    sheet = load_workbook(io.BytesIO(build_workbook(results)), rich_text=True)["成功达人"]

    assert isinstance(sheet["N3"].value, str)
    assert sheet["N3"].value == "图文作者、穿搭"
    assert sheet["O3"].value == f"图文作者、穿搭、{tag}"
    assert sheet["O4"].value == "汇总未完成"
    assert tag not in str(sheet["N4"].value)


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

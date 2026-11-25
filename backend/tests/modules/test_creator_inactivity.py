from datetime import datetime
from uuid import uuid4

import pytest

from scheduler_center.modules.batch.models import Creator, Work, WorkKind


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("2023-12-31T10:00:00+08:00", None),
        ("2024-01-31T09:59:59+08:00", None),
        ("2024-02-29T09:59:59+08:00", None),
        ("2024-02-29T10:00:00+08:00", "1个月内未更新"),
        ("2024-04-30T09:59:59+08:00", "1个月内未更新"),
        ("2024-04-30T10:00:00+08:00", "3个月内未更新"),
        ("2024-07-31T09:59:59+08:00", "3个月内未更新"),
        ("2024-07-31T10:00:00+08:00", "半年内未更新"),
        ("2025-01-31T09:59:59+08:00", "半年内未更新"),
        ("2025-01-31T10:00:00+08:00", "一年以上未更新"),
    ],
)
def test_inactivity_uses_complete_calendar_months(reference, expected):
    creator = Creator(uuid4(), 1, None, "alice", "alice")
    works = [work(creator, 1, "2024-01-31T10:00:00+08:00")]

    assert creator.work_inactivity_tag(works, datetime.fromisoformat(reference)) == expected


@pytest.mark.parametrize(
    ("published", "reference", "expected"),
    [
        ("2023-01-30T16:30:00Z", "2023-02-27T16:29:59Z", None),
        ("2023-01-30T16:30:00Z", "2023-02-27T16:30:00Z", "1个月内未更新"),
        ("2024-02-29T10:00:00+08:00", "2025-02-28T09:59:59+08:00", "半年内未更新"),
        ("2024-02-29T10:00:00+08:00", "2025-02-28T10:00:00+08:00", "一年以上未更新"),
    ],
)
def test_inactivity_clamps_month_ends_in_beijing(published, reference, expected):
    creator = Creator(uuid4(), 1, None, "alice", "alice")

    assert (
        creator.work_inactivity_tag(
            [work(creator, 1, published)], datetime.fromisoformat(reference)
        )
        == expected
    )


@pytest.mark.parametrize("latest_kind", [WorkKind.VIDEO, WorkKind.PHOTO])
def test_inactivity_uses_latest_dated_work_regardless_of_kind_or_position(latest_kind):
    creator = Creator(
        uuid4(), 1, None, "alice", "alice", updated_at=datetime.fromisoformat("2024-04-30T02:00Z")
    )
    older_kind = WorkKind.PHOTO if latest_kind == WorkKind.VIDEO else WorkKind.VIDEO
    works = [
        work(creator, 1, None),
        work(creator, 2, "2023-10-30T02:00:00Z", older_kind),
        work(creator, 3, "2024-03-31T02:00:00Z", latest_kind),
    ]

    assert creator.work_inactivity_tag(works) == "1个月内未更新"


def test_inactivity_omits_creators_without_dated_works():
    creator = Creator(uuid4(), 1, None, "alice", "alice")

    assert creator.work_inactivity_tag([]) is None
    assert creator.work_inactivity_tag([work(creator, 1, None, WorkKind.PHOTO)]) is None


def work(
    creator: Creator, position: int, published_at: str | None, kind: WorkKind = WorkKind.VIDEO
) -> Work:
    return Work(
        creator.batch_id,
        creator.id,
        position,
        str(position),
        f"https://www.tiktok.com/@{creator.handle}/{kind}/{position}",
        kind,
        published_at=datetime.fromisoformat(published_at) if published_at else None,
    )

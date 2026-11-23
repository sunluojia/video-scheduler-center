"""不再统计时长：去掉已有识别标签、汇总标签里的“视频时长…”

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-23
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def _without_duration(tags: str) -> str:
    """标签数组去掉时长标签，其余保持先后。"""
    return f"""(
        SELECT coalesce(jsonb_agg(tag ORDER BY position), '[]'::jsonb)
        FROM jsonb_array_elements({tags}) WITH ORDINALITY AS item(tag, position)
        WHERE tag #>> '{{}}' NOT LIKE '视频时长%'
    )"""


def upgrade() -> None:
    for table in ("works", "creators"):
        op.execute(
            f"UPDATE {table} SET tags = {_without_duration('tags')}"
            " WHERE jsonb_typeof(tags) = 'array'"
        )
    # 视频分析结果的识别标签，以及汇总结果的标签；重新汇总时会读到。
    for path in ("{recognition,tags}", "{tags}"):
        tags = f"result #> '{path}'"
        kept = _without_duration(tags)
        op.execute(
            f"UPDATE analysis_jobs SET result = jsonb_set(result, '{path}', {kept})"
            f" WHERE jsonb_typeof({tags}) = 'array'"
        )


def downgrade() -> None:
    """去掉的标签不再恢复。"""

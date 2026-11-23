"""数据看板：事件和分析阶段按时间建索引；已上传的视频补记“已上传”事件

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-23
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(op.f("ix_events_occurred_at"), "events", ["occurred_at"])
    op.create_index(op.f("ix_analysis_stages_occurred_at"), "analysis_stages", ["occurred_at"])
    # 之前收下的视频没有记事件，按素材登记时间补上，统计下载量时不漏掉
    op.execute(
        """
        INSERT INTO events
            (source, event_id, task_id, run, type, batch_id, payload, occurred_at, received_at)
        SELECT 'crawler', m.id, t.id, t.run, 'uploaded', t.batch_id,
            jsonb_build_object(
                'event_id', m.id, 'task_id', t.id, 'run', t.run,
                'occurred_at', to_jsonb(m.created_at), 'type', 'uploaded', 'size', m.size
            ),
            m.created_at, m.created_at
        FROM materials m JOIN crawler_tasks t ON t.work_id = m.work_id AND t.kind = 'download'
        ON CONFLICT (source, event_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM events WHERE type = 'uploaded'")
    op.drop_index(op.f("ix_analysis_stages_occurred_at"), table_name="analysis_stages")
    op.drop_index(op.f("ix_events_occurred_at"), table_name="events")

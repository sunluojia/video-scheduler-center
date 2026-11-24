"""全部表定义的汇总，数据库迁移和测试据此得到完整的表结构。"""

from scheduler_center.core.database.mapping import metadata
from scheduler_center.core.messaging.outbox import outbox
from scheduler_center.modules.analysis.tables import (
    analysis_jobs,
    analysis_stage_attempts,
    analysis_stages,
)
from scheduler_center.modules.batch.tables import batches, creators, qr_codes, works
from scheduler_center.modules.crawl.tables import (
    crawler_task_attempts,
    crawler_task_runs,
    crawler_tasks,
)
from scheduler_center.modules.executor.tables import executors
from scheduler_center.modules.flow.history import events
from scheduler_center.modules.identity.tables import identity_pool
from scheduler_center.modules.material.tables import materials
from scheduler_center.modules.proxy.tables import (
    proxies,
    proxy_folders,
    proxy_leases,
    proxy_lines,
    proxy_rules,
)

__all__ = [
    "analysis_jobs",
    "analysis_stage_attempts",
    "analysis_stages",
    "batches",
    "crawler_task_attempts",
    "crawler_task_runs",
    "crawler_tasks",
    "creators",
    "events",
    "executors",
    "identity_pool",
    "materials",
    "metadata",
    "outbox",
    "proxies",
    "proxy_folders",
    "proxy_leases",
    "proxy_lines",
    "proxy_rules",
    "qr_codes",
    "works",
]

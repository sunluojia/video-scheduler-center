"""事件接收进程，由 Dramatiq 命令行启动，可以开多个进程：

dramatiq scheduler_center.entrypoints.consumer:broker
"""

from scheduler_center.core.config.settings import Settings
from scheduler_center.core.database.engine import Database
from scheduler_center.integrations.mq.broker import create_broker
from scheduler_center.integrations.mq.consumer import declare_actors
from scheduler_center.modules.tables import metadata  # noqa: F401  加载全部表映射

settings = Settings()
broker = create_broker(settings)
declare_actors(broker, Database(settings.database_url))

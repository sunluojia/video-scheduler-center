"""API：页面接口、前端静态文件、实时更新，以及执行程序的注册、视频上传和下载接口。

内部使用，页面接口不需要登录；执行程序调用的接口要求服务凭据。
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from importlib.metadata import version

import httpx
import uvicorn
from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from scheduler_center.core.config.logging_setup import configure_logging
from scheduler_center.core.config.settings import ApiSettings
from scheduler_center.core.database.engine import Database
from scheduler_center.core.web.authentication import ServiceToken
from scheduler_center.core.web.exception_handlers import register_exception_handlers
from scheduler_center.modules.batch.creator_router import router as creator_router
from scheduler_center.modules.batch.router import router as batch_router
from scheduler_center.modules.batch.work_router import router as work_router
from scheduler_center.modules.crawl.router import router as upload_router
from scheduler_center.modules.executor.gateway import Gateway
from scheduler_center.modules.executor.router import registration
from scheduler_center.modules.executor.router import router as executor_router
from scheduler_center.modules.identity.router import router as identity_router
from scheduler_center.modules.live.hub import LiveHub
from scheduler_center.modules.live.router import router as live_router
from scheduler_center.modules.material.router import router as material_router
from scheduler_center.modules.material.store import MaterialStore
from scheduler_center.modules.proxy.lease_router import router as lease_router
from scheduler_center.modules.proxy.router import router as proxy_router
from scheduler_center.modules.proxy.use_router import router as proxy_use_router
from scheduler_center.modules.report.router import router as import_router
from scheduler_center.modules.tables import metadata  # noqa: F401  加载全部表映射

VERSION = version("scheduler-center")
PAGE_ROUTERS = (
    batch_router,
    creator_router,
    work_router,
    import_router,
    executor_router,
    proxy_router,
    proxy_use_router,
    identity_router,
    live_router,
)
SERVICE_ROUTERS = (registration, upload_router, material_router, lease_router)


class SinglePageApp(StaticFiles):
    """前端路由由浏览器处理：找不到的页面路径返回 index.html，找不到的接口照样 404。"""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as error:
            # path 是按系统格式处理过的（Windows 上是反斜杠），接口前缀看请求原始路径
            if error.status_code != 404 or scope["path"].startswith("/api/"):
                raise
            return await super().get_response("index.html", scope)


def create_app(
    settings: ApiSettings, executor_transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    token = settings.service_token.get_secret_value()
    database = Database(settings.database_url)
    heartbeat_timeout = timedelta(seconds=settings.heartbeat_timeout_seconds)
    gateway = Gateway(database, token, heartbeat_timeout, executor_transport)
    live = LiveHub(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        listening = asyncio.create_task(live.run())
        yield
        listening.cancel()
        await gateway.aclose()
        await database.dispose()

    app = FastAPI(title="调度中心", version=VERSION, lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database
    app.state.gateway = gateway
    app.state.store = MaterialStore(settings.material_root)
    app.state.live = live
    for router in PAGE_ROUTERS:
        app.include_router(router, prefix="/api")
    for router in SERVICE_ROUTERS:
        app.include_router(router, prefix="/api", dependencies=[Depends(ServiceToken(token))])
    register_exception_handlers(app)
    if settings.static_root.is_dir():
        app.mount("/", SinglePageApp(directory=settings.static_root, html=True))
    return app


def main() -> None:
    # noinspection PyArgumentList
    settings = ApiSettings()  # 服务凭据从环境变量读取
    configure_logging(settings.log_level)
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()

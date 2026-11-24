import os
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from scheduler_center.core.database.engine import Database
from scheduler_center.entrypoints.api import create_app
from scheduler_center.modules.tables import metadata
from tests.factories import SERVICE_TOKEN, FakeExecutors, api_settings

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="session")
def database_url() -> str:
    """数据库测试使用独立的测试库，每次运行都会重建表结构。"""
    url = os.environ.get("SC_TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 SC_TEST_DATABASE_URL 后运行数据库测试")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    return url


@pytest.fixture
async def database(database_url: str):
    database = Database(database_url)
    tables = ", ".join(table.name for table in metadata.sorted_tables)
    async with database.engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {tables}"))
    yield database
    await database.dispose()


@pytest.fixture
def executors() -> FakeExecutors:
    return FakeExecutors()


@pytest.fixture
def legacy_crawler_dispatch() -> bool:
    return True


@pytest.fixture
async def app(
    database_url: str,
    database: Database,
    tmp_path,
    executors: FakeExecutors,
    legacy_crawler_dispatch: bool,
):
    """不运行 lifespan：测试不监听数据库通知。"""
    app = create_app(
        api_settings(
            database_url=database_url,
            material_root=tmp_path / "materials",
            legacy_crawler_dispatch=legacy_crawler_dispatch,
        ),
        httpx.MockTransport(executors.handle),
    )
    yield app
    await app.state.gateway.aclose()
    await app.state.database.dispose()


@pytest.fixture
async def client(app):
    """页面使用的客户端。"""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
async def service(app):
    """执行程序使用的客户端，带服务凭据。"""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
    ) as client:
        yield client

from typing import Annotated

from fastapi import Depends, Query, Request

from scheduler_center.core.config.settings import ApiSettings
from scheduler_center.core.database.engine import Database


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_settings(request: Request) -> ApiSettings:
    return request.app.state.settings


def legacy_crawler_dispatch(request: Request) -> bool:
    return get_settings(request).legacy_crawler_dispatch


class Paging:
    """分页参数，页码从 1 开始。"""

    def __init__(
        self,
        page: Annotated[int, Query(ge=1)] = 1,
        size: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> None:
        self.size = size
        self.offset = (page - 1) * size


ApiSettingsDep = Annotated[ApiSettings, Depends(get_settings)]
DatabaseDep = Annotated[Database, Depends(get_database)]
LegacyCrawlerDispatchDep = Annotated[bool, Depends(legacy_crawler_dispatch)]
PagingDep = Annotated[Paging, Depends()]

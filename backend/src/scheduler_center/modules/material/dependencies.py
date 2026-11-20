from typing import Annotated

from fastapi import Depends, Request

from scheduler_center.modules.material.store import MaterialStore


def get_store(request: Request) -> MaterialStore:
    return request.app.state.store


StoreDep = Annotated[MaterialStore, Depends(get_store)]

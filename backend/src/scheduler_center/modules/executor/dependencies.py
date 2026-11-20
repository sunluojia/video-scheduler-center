from typing import Annotated

from fastapi import Depends, Request

from scheduler_center.modules.executor.gateway import Gateway


def get_gateway(request: Request) -> Gateway:
    return request.app.state.gateway


GatewayDep = Annotated[Gateway, Depends(get_gateway)]

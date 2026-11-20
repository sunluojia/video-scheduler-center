"""服务凭据：爬取程序和分析服务调用本服务时以 Bearer 方式携带。"""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


class ServiceToken:
    def __init__(self, token: str) -> None:
        self._expected = token.encode()

    async def __call__(
        self, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
    ) -> None:
        supplied = credentials.credentials.encode() if credentials else b""
        if not secrets.compare_digest(supplied, self._expected):
            raise HTTPException(401, "服务凭据无效", headers={"WWW-Authenticate": "Bearer"})

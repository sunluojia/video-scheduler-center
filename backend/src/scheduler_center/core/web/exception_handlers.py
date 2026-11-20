from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from scheduler_center.core.common.errors import (
    Conflict,
    Invalid,
    NotFound,
    Rejected,
    Unavailable,
)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(NotFound, _respond(404))
    app.add_exception_handler(Conflict, _respond(409))
    app.add_exception_handler(Invalid, _respond(422))
    app.add_exception_handler(Rejected, _respond(502))
    app.add_exception_handler(Unavailable, _respond(503))


def _respond(status: int):
    async def handler(_: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=status)

    return handler

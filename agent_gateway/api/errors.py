"""统一错误格式：{"code", "message"}。"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class GatewayError(Exception):
    status_code = 500
    code = "INTERNAL_ERROR"

    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code


class ValidationError(GatewayError):
    status_code = 400
    code = "VALIDATION_ERROR"


class NotFound(GatewayError):
    status_code = 404
    code = "NOT_FOUND"


class SessionBusy(GatewayError):
    status_code = 409
    code = "SESSION_BUSY"


class BadGateway(GatewayError):
    status_code = 502
    code = "BAD_GATEWAY"


def _json(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message})


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(GatewayError)
    async def _gateway_error(_: Request, exc: GatewayError) -> JSONResponse:
        return _json(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", ()) if x != "body")
        msg = first.get("msg", "invalid request")
        return _json(400, "VALIDATION_ERROR", f"{loc}: {msg}" if loc else msg)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "NOT_FOUND"}.get(exc.status_code, "INTERNAL_ERROR")
        return _json(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return _json(500, "INTERNAL_ERROR", str(exc) or exc.__class__.__name__)

from __future__ import annotations

import json

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)


def _error_text(detail: object) -> str:
    if detail is None:
        return "An unexpected error occurred."
    if isinstance(detail, str):
        return detail
    try:
        return json.dumps(detail, default=str)
    except TypeError:
        return str(detail)


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    message = _error_text(exc.detail)
    logger.warning(
        "http_exception",
        path=request.url.path,
        status_code=exc.status_code,
        detail=message,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "data": None, "error": message},
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    message = _error_text(exc.errors())
    logger.warning(
        "validation_exception",
        path=request.url.path,
        detail=message,
    )
    return JSONResponse(
        status_code=422,
        content={"success": False, "data": None, "error": message},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception", path=request.url.path)
    return JSONResponse(
        status_code=500,
        content={"success": False, "data": None, "error": "Internal server error"},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register JSON error handlers for all application errors."""

    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

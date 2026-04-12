from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.core.config import Settings, get_settings
from api.core.exceptions import register_exception_handlers
from api.routers.media import router as media_router
from api.routers.bootstrap import router as bootstrap_router
from api.routers.origins import router as origins_router
from api.routers.ik_compat import router as ik_compat_router
from api.routers.workspaces import router as workspaces_router
from api.routers.analytics import router as analytics_router
from api.routers.presets import router as presets_router
from api.routers.webhooks import router as webhooks_router

from api.routers.transform import router as transform_router
from api.routers.upload import router as upload_router
from api.services.queue import QueuePublisher
from api.services.r2 import R2Service
from api.services.supabase_client import SupabaseRepository


def configure_logging() -> None:
    """Configure structured JSON logging for the application."""

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout, force=True)
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = app.state.settings
    if getattr(app.state, "r2_service", None) is None:
        app.state.r2_service = R2Service(settings)
        close_r2 = True
    else:
        close_r2 = False
    if getattr(app.state, "supabase_service", None) is None:
        app.state.supabase_service = await SupabaseRepository.create(settings)
        close_supabase = True
    else:
        close_supabase = False
    if getattr(app.state, "queue_publisher", None) is None:
        app.state.queue_publisher = await QueuePublisher.create(settings)
        close_queue = True
    else:
        close_queue = False

    logger = structlog.get_logger(__name__)
    logger.info("streamkit_startup_complete", environment=settings.app_env)
    try:
        yield
    finally:
        if close_queue:
            await app.state.queue_publisher.aclose()
        if close_supabase:
            await app.state.supabase_service.aclose()
        if close_r2 and hasattr(app.state.r2_service, "close"):
            close_result = app.state.r2_service.close()
            if hasattr(close_result, "__await__"):
                await close_result
        logger.info("streamkit_shutdown_complete")


def create_app(
    *,
    settings: Settings | None = None,
    r2_service: R2Service | None = None,
    supabase_service: SupabaseRepository | None = None,
    queue_publisher: QueuePublisher | None = None,
) -> FastAPI:
    """Build the FastAPI application."""

    configure_logging()
    resolved_settings = settings or get_settings()
    app = FastAPI(title="StreamKit", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.r2_service = r2_service
    app.state.supabase_service = supabase_service
    app.state.queue_publisher = queue_publisher

    register_exception_handlers(app)

    cors_origins = resolved_settings.allowed_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(upload_router)
    app.include_router(bootstrap_router)
    app.include_router(transform_router)
    app.include_router(media_router)
    app.include_router(origins_router)
    app.include_router(ik_compat_router)
    app.include_router(workspaces_router)
    app.include_router(analytics_router)
    app.include_router(presets_router)
    app.include_router(webhooks_router)


    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"success": True, "data": {"status": "ok"}, "error": None}

    return app

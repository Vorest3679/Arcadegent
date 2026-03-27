"""FastAPI entrypoint: wires config, container, routes, and lifecycle hooks."""

from __future__ import annotations

from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.http.arcades import router as arcades_router
from app.api.http.chat import router as chat_router
from app.api.http.health import router as health_router
from app.api.http.location import router as location_router
from app.api.http.regions import router as regions_router
from app.api.stream.sse import router as sse_router
from app.core.config import Settings
from app.core.container import build_container
from app.core.lifecycle import on_shutdown, on_startup
from app.infra.observability.logger import get_logger, setup_logging

access_logger = get_logger("uvicorn.access")

# 装配应用：配置、日志、依赖容器、生命周期、路由
def create_app() -> FastAPI:
    settings = Settings.from_env()
    setup_logging(settings.log_level)
    container = build_container(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await on_startup(container)
        try:
            yield
        finally:
            on_shutdown()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.container = container

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in settings.cors_allow_origins.split(",") if origin.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        start = perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_ms = (perf_counter() - start) * 1000
            query = f"?{request.url.query}" if request.url.query else ""
            path = f"{request.url.path}{query}"
            client_ip = request.client.host if request.client else "-"
            access_logger.info(
                '%s "%s %s" %s %.2fms',
                client_ip,
                request.method,
                path,
                status_code,
                duration_ms,
            )

    app.include_router(health_router)
    app.include_router(arcades_router)
    app.include_router(location_router)
    app.include_router(regions_router)
    app.include_router(chat_router)
    app.include_router(sse_router)

    return app


app = create_app()

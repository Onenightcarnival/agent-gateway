"""FastAPI 应用工厂。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.errors import install_error_handlers
from .api.routes import router
from .config import Settings
from .engines.base import AgentEngine
from .gateway import Gateway


def create_app(settings: Settings, engine: AgentEngine) -> FastAPI:
    gateway = Gateway(settings, engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await gateway.startup()
        try:
            yield
        finally:
            await gateway.shutdown()

    app = FastAPI(title="Agent Gateway", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.gateway = gateway
    install_error_handlers(app)
    app.include_router(router)
    return app

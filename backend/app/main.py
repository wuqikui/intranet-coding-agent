"""FastAPI 应用入口。"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.core.logging import setup_logging
from app.core.exceptions import (
    AppError,
    app_error_handler,
    validation_error_handler,
    generic_error_handler,
)
from app.api.health import router as health_router
from app.api.auth import router as auth_router
from app.api.gateway import router as gateway_router
from app.api.knowledge import router as knowledge_router
from app.api.admin import router as admin_router
from app.api.tools import router as tools_router
from app.api.wizard import router as wizard_router
from app.agent.api import router as agent_router
from app.models.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化。"""
    logger = setup_logging()
    logger.info("启动 FastAPI 应用…")
    settings = get_settings()
    logger.info("推理后端: %s | 模型: %s", settings.INFERENCE_BACKEND, settings.MODEL_NAME)
    await init_db()
    logger.info("数据库初始化完成")
    logger.info("应用就绪")
    yield
    logger.info("应用关闭")


def create_app() -> FastAPI:
    app = FastAPI(
        title="内网离线 AI Coding Agent",
        description="纯内网、多用户、类 Trae 的 AI Coding Agent 系统",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS — 内网浏览器访问
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 异常处理
    app.add_exception_handler(AppError, app_error_handler)
    from fastapi.exceptions import RequestValidationError
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, generic_error_handler)

    # 路由（health_router 无内置 prefix，其余 router 内置 /api/xxx prefix）
    app.include_router(health_router, prefix="/api")
    app.include_router(auth_router)
    app.include_router(gateway_router)
    app.include_router(knowledge_router)
    app.include_router(admin_router)
    app.include_router(tools_router)
    app.include_router(wizard_router)
    app.include_router(agent_router)

    return app


app = create_app()

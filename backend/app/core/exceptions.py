"""统一异常处理。"""
import logging
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

logger = logging.getLogger("agent.exceptions")


class AppError(Exception):
    """业务异常基类。"""

    def __init__(self, message: str, status_code: int = 400, detail: dict = None):
        self.message = message
        self.status_code = status_code
        self.detail = detail or {}
        super().__init__(message)


class AuthError(AppError):
    def __init__(self, message: str = "未授权"):
        super().__init__(message, status_code=401)


class ForbiddenError(AppError):
    def __init__(self, message: str = "禁止访问"):
        super().__init__(message, status_code=403)


class NotFoundError(AppError):
    def __init__(self, message: str = "资源不存在"):
        super().__init__(message, status_code=404)


async def app_error_handler(request: Request, exc: AppError):
    logger.warning("AppError: %s (path=%s)", exc.message, request.url.path)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.message, "detail": exc.detail},
    )


async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": "参数校验失败", "detail": exc.errors()},
    )


async def generic_error_handler(request: Request, exc: Exception):
    logger.exception("未处理异常: %s (path=%s)", str(exc), request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "内部服务器错误"},
    )

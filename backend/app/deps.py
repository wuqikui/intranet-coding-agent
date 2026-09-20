"""全局依赖注入 - 供所有路由使用。"""
from functools import lru_cache
from app.config import get_settings
from app.audit.logger import AuditLogger


@lru_cache
def get_audit_logger() -> AuditLogger:
    """审计日志单例（保证哈希链连续）。"""
    settings = get_settings()
    return AuditLogger(settings.AUDIT_LOG_PATH, settings.AUDIT_HASH_SEED)


# ---- 鉴权依赖（Task 3 追加，不改动以上内容）----
from typing import Optional  # noqa: E402

from fastapi import Depends, Request  # noqa: E402

from app.auth.jwt import verify_token  # noqa: E402
from app.auth.models import User  # noqa: E402
from app.core.exceptions import AuthError, ForbiddenError  # noqa: E402
from app.models.db import async_session  # noqa: E402


async def get_current_user(request: Request) -> User:
    """从 Authorization 头解析 Bearer 令牌，验证后查 DB 返回 User。

    无 token 或无效返回 401。函数内部用 async_session 直接查 DB，
    而非 get_session 依赖注入（适用于非路由直接注入的场景）。
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise AuthError("缺少或无效的认证头")
    token = auth_header[len("Bearer "):].strip()
    payload = verify_token(token)
    if payload is None:
        raise AuthError("无效或过期的令牌")
    user_id = payload.get("sub")
    try:
        pk = int(user_id)
    except (TypeError, ValueError):
        raise AuthError("无效的令牌主体")
    async with async_session() as session:
        user = await session.get(User, pk)
        if user is None or not user.is_active:
            raise AuthError("用户不存在或已停用")
        return user


def require_role(*roles: str):
    """返回一个依赖函数：校验当前用户角色是否在允许列表中，否则 403。"""

    allowed = set(roles)

    async def _checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise ForbiddenError(f"需要角色: {', '.join(roles)}")
        return user

    return _checker


async def get_current_user_optional(request: Request) -> Optional[User]:
    """可选鉴权：有合法令牌返回 User，否则 None（登录页等公开接口可用）。"""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[len("Bearer "):].strip()
    payload = verify_token(token)
    if payload is None:
        return None
    user_id = payload.get("sub")
    try:
        pk = int(user_id)
    except (TypeError, ValueError):
        return None
    async with async_session() as session:
        user = await session.get(User, pk)
        if user is None or not user.is_active:
            return None
        return user

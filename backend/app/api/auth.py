"""鉴权 API 路由：登录、当前用户、用户管理。"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.auth.local import authenticate_local, hash_password
from app.auth.models import ALL_ROLES, ROLE_DEV, User
from app.config import get_settings
from app.core.exceptions import AppError, AuthError, NotFoundError
from app.deps import get_audit_logger, get_current_user, require_role
from app.models.db import get_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---- 请求/响应模型 ----


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)
    role: str = Field(..., description="admin | dev | readonly")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class UserInfoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    role: str
    is_active: bool


# ---- 路由 ----


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)):
    """登录：按 settings.AUTH_MODE 选择 local 或 ldap。"""
    settings = get_settings()
    audit = get_audit_logger()
    mode = (settings.AUTH_MODE or "local").lower()

    user: Optional[User] = None

    if mode in ("ldap", "ad"):
        # 延迟导入：ldap3 可能未安装
        from app.auth.ldap import LDAPNotConfiguredError, authenticate_ldap

        try:
            ok = authenticate_ldap(body.username, body.password)
        except LDAPNotConfiguredError as e:
            audit.log(
                user_id=body.username,
                role="unknown",
                action="login_fail",
                extra={"mode": mode, "reason": str(e)},
            )
            raise AuthError(f"LDAP 认证不可用：{e}")
        except Exception as e:  # 其它异常也视为认证失败
            audit.log(
                user_id=body.username,
                role="unknown",
                action="login_fail",
                extra={"mode": mode, "error": str(e)},
            )
            raise AuthError("LDAP 认证失败")
        if not ok:
            audit.log(
                user_id=body.username,
                role="unknown",
                action="login_fail",
                extra={"mode": mode},
            )
            raise AuthError("用户名或密码错误")
        # LDAP 通过后 upsert 用户（默认 dev 角色）
        result = await session.execute(
            select(User).where(User.username == body.username)
        )
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                username=body.username,
                role=ROLE_DEV,
                password_hash="",
                is_active=True,
            )
            session.add(user)
            await session.flush()  # 获取自增 id
            await session.commit()
        elif not user.is_active:
            audit.log(
                user_id=str(user.id),
                role=user.role,
                action="login_fail",
                extra={"mode": mode, "reason": "disabled"},
            )
            raise AuthError("账号已停用")
    else:
        # 本地模式
        user = await authenticate_local(body.username, body.password, session)
        if user is None:
            audit.log(
                user_id=body.username,
                role="unknown",
                action="login_fail",
                extra={"mode": "local"},
            )
            raise AuthError("用户名或密码错误")

    token = create_access_token(str(user.id), user.role)
    audit.log(
        user_id=str(user.id),
        role=user.role,
        action="login_success",
        extra={"mode": mode},
    )
    return TokenResponse(access_token=token, role=user.role)


@router.get("/me", response_model=UserInfoResponse)
async def me(user: User = Depends(get_current_user)):
    """返回当前登录用户信息（需 Bearer token）。"""
    return UserInfoResponse.model_validate(user)


@router.post(
    "/users",
    response_model=UserInfoResponse,
    dependencies=[Depends(require_role("admin"))],
)
async def create_user(
    body: UserCreateRequest,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """创建用户（仅 admin）。"""
    if body.role not in ALL_ROLES:
        raise AppError(f"无效角色: {body.role}")
    existing = await session.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none() is not None:
        raise AppError("用户名已存在")
    user = User(
        username=body.username,
        role=body.role,
        password_hash=hash_password(body.password),
        is_active=True,
    )
    session.add(user)
    await session.flush()
    await session.commit()
    audit = get_audit_logger()
    audit.log(
        user_id=str(actor.id),
        role=actor.role,
        action="user_create",
        extra={"target_id": str(user.id), "target": user.username, "role": user.role},
    )
    return UserInfoResponse.model_validate(user)


@router.get(
    "/users",
    response_model=list[UserInfoResponse],
    dependencies=[Depends(require_role("admin"))],
)
async def list_users(session: AsyncSession = Depends(get_session)):
    """列出所有用户（仅 admin）。"""
    result = await session.execute(select(User).order_by(User.id))
    users = result.scalars().all()
    return [UserInfoResponse.model_validate(u) for u in users]


@router.delete("/users/{user_id}", dependencies=[Depends(require_role("admin"))])
async def deactivate_user(
    user_id: int,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """停用用户（仅 admin）。"""
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError("用户不存在")
    user.is_active = False
    await session.commit()
    audit = get_audit_logger()
    audit.log(
        user_id=str(actor.id),
        role=actor.role,
        action="user_deactivate",
        extra={"target_id": str(user.id), "target": user.username},
    )
    return {"ok": True, "username": user.username, "is_active": False}

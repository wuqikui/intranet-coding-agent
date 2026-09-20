"""管理员 API 路由 - 维护模式、离线导入、系统状态、审计与用户管理。

路由前缀 /api/admin，全部需要 admin 角色。
- POST   /maintenance     开启/关闭出网维护模式
- GET    /maintenance      查询当前维护模式状态
- POST   /import-model     触发离线模型/包导入
- GET    /stats            系统状态概览
- GET    /audit            审计记录列表（只读）
- GET    /users            列出所有用户
- POST   /users            创建用户（后台管理面板版本，与 /api/auth/users 并存）

注意：main.py 暂未 include 该 router，留给后续统一挂载：
    from app.api.admin import router as admin_router
    app.include_router(admin_router)
"""
from __future__ import annotations

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.local import hash_password
from app.auth.models import ALL_ROLES, User
from app.config import get_settings
from app.core.exceptions import AppError
from app.deps import get_audit_logger, get_current_user, require_role
from app.models.db import get_session
from app.maintenance.guard import get_egress_guard
from app.maintenance.importer import get_model_importer

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---- 请求/响应模型 ----


class MaintenanceRequest(BaseModel):
    enable: bool = Field(..., description="true=开启出网维护模式，false=关闭")


class ImportModelRequest(BaseModel):
    url: str = Field(..., min_length=1, description="资源下载 URL")
    sha256: str = Field(..., min_length=1, description="期望的 sha256 hex")
    kind: Literal["model", "package"] = Field(
        ..., description="导入目标：model 或 package"
    )
    filename: Optional[str] = Field(
        None, description="可选目标文件名；未提供时从 URL 路径推导"
    )


class UserCreateAdminRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)
    role: str = Field(..., description="admin | dev | readonly")


class UserInfoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    role: str
    is_active: bool


# ---- 路由 ----


@router.post(
    "/maintenance",
    dependencies=[Depends(require_role("admin"))],
)
async def set_maintenance(
    body: MaintenanceRequest,
    user: User = Depends(get_current_user),
):
    """开启/关闭出网维护模式（仅 admin）。"""
    guard = get_egress_guard()
    if body.enable:
        guard.enable_maintenance(str(user.id), user.role)
    else:
        guard.disable_maintenance(str(user.id), user.role)
    return {"maintenance_mode": guard.is_maintenance_active()}


@router.get(
    "/maintenance",
    dependencies=[Depends(require_role("admin"))],
)
async def get_maintenance(
    user: User = Depends(get_current_user),  # noqa: ARG001 触发鉴权
):
    """查询当前出网维护模式状态（仅 admin）。"""
    guard = get_egress_guard()
    return {"maintenance_mode": guard.is_maintenance_active()}


@router.post(
    "/import-model",
    dependencies=[Depends(require_role("admin"))],
)
async def import_model(
    body: ImportModelRequest,
    user: User = Depends(get_current_user),
):
    """触发离线模型/包导入（仅 admin，必须先开启维护模式）。"""
    importer = get_model_importer()
    result = await importer.download_and_import(
        url=body.url,
        expected_sha256=body.sha256,
        dest_kind=body.kind,
        user_id=str(user.id),
        role=user.role,
        filename=body.filename,
    )
    return result


@router.get(
    "/stats",
    dependencies=[Depends(require_role("admin"))],
)
async def stats(
    user: User = Depends(get_current_user),  # noqa: ARG001
):
    """系统状态概览（仅 admin）。"""
    settings = get_settings()
    guard = get_egress_guard()
    return {
        "maintenance_mode": guard.is_maintenance_active(),
        "inference_backend": settings.INFERENCE_BACKEND,
        "model_name": settings.MODEL_NAME,
        "model_path": settings.MODEL_PATH,
        "quantization": settings.QUANTIZATION,
        "auth_mode": settings.AUTH_MODE,
    }


@router.get(
    "/audit",
    dependencies=[Depends(require_role("admin"))],
)
async def audit_records(
    limit: int = Query(100, ge=1, le=10000, description="返回最近 N 条"),
    user: User = Depends(get_current_user),  # noqa: ARG001
):
    """审计记录列表（只读，按时间倒序返回最近 limit 条）。"""
    audit = get_audit_logger()
    records = audit.read_all()
    # 倒序取最近 limit 条
    sliced = list(reversed(records))[:limit]
    return {"records": sliced, "count": len(sliced), "total": len(records)}


@router.get(
    "/users",
    response_model=List[UserInfoResponse],
    dependencies=[Depends(require_role("admin"))],
)
async def list_users(
    user: User = Depends(get_current_user),  # noqa: ARG001
    session: AsyncSession = Depends(get_session),
):
    """列出所有用户（仅 admin）。"""
    result = await session.execute(select(User).order_by(User.id))
    users = result.scalars().all()
    return [UserInfoResponse.model_validate(u) for u in users]


@router.post(
    "/users",
    response_model=UserInfoResponse,
    dependencies=[Depends(require_role("admin"))],
)
async def create_user(
    body: UserCreateAdminRequest,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """创建用户（仅 admin，后台管理面板版本）。"""
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
        extra={
            "target_id": str(user.id),
            "target": user.username,
            "role": user.role,
            "via": "admin_api",
        },
    )
    return UserInfoResponse.model_validate(user)


# ---- 路由挂载说明 ----
# main.py 暂未 include 该 router，请在 main.py 中追加：
#     from app.api.admin import router as admin_router
#     app.include_router(admin_router)
# 即可生效。当前约束要求不修改 main.py，故留给后续统一挂载。

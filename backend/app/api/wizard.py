"""首次运行向导 API 路由。

路由前缀 /api/wizard：
- GET  /state        查询当前向导状态（公开，首次运行时管理员账号尚未创建）
- POST /run          执行步骤（全跑或单步；首次运行无管理员时允许）
- POST /reset        重置向导（需 admin，避免误操作）

接入：main.py 末尾加 `app.include_router(wizard_router)`。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.models import User
from app.deps import get_current_user_optional, require_role
from app.init_wizard import STEP_ORDER, get_wizard

logger = logging.getLogger("api.wizard")

router = APIRouter(prefix="/api/wizard", tags=["wizard"])


class RunRequest(BaseModel):
    step: Optional[str] = Field(
        None, description="单步执行；为空则跑全部未完成步骤"
    )
    resume_from: Optional[str] = Field(
        None, description="从指定步骤继续（清掉该步及之后的完成标记）"
    )


@router.get("/state")
async def state():
    """查询向导状态（公开）。"""
    wizard = get_wizard()
    s = wizard.get_state()
    s["steps"] = STEP_ORDER
    return s


@router.post("/run")
async def run(body: RunRequest, user: Optional[User] = Depends(get_current_user_optional)):
    """执行步骤。

    首次运行（系统无管理员）：允许匿名跑（首次必须创建管理员）。
    已有管理员：要求 admin/dev 角色。
    """
    # 若已有管理员账号，则必须有 admin/dev 角色
    if user is not None and user.role not in ("admin", "dev"):
        from app.core.exceptions import ForbiddenError
        raise ForbiddenError("非 admin/dev 角色不能执行向导")
    wizard = get_wizard()
    if body.resume_from:
        result = await wizard.resume_from(body.resume_from)
        return {"stopped_at": result.get("stopped_at"), "state": wizard.get_state()}
    if body.step:
        result = await wizard.run_step(body.step)
        return {
            "step": result.step,
            "status": result.status,
            "message": result.message,
            "data": result.data,
            "duration_ms": result.duration_ms,
            "state": wizard.get_state(),
        }
    # 默认 run_all
    result = await wizard.run_all()
    return {
        "stopped_at": result.get("stopped_at"),
        "state": wizard.get_state(),
    }


@router.post("/reset", dependencies=[Depends(require_role("admin"))])
async def reset():
    """重置向导（仅 admin）。"""
    wizard = get_wizard()
    wizard.reset()
    return {"ok": True, "state": wizard.get_state()}


# ============================================================================
# 接入说明（不修改 main.py）：
# 在 backend/app/main.py 的 create_app() 中加入：
#
#     from app.api.wizard import router as wizard_router
#     ...
#     app.include_router(wizard_router)
#
# ============================================================================

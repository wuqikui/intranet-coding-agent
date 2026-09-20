"""Agent API 路由 - 完整项目生成管线对外接口。

路由前缀 /api/agent：
- POST /api/agent/generate              生成项目（非流式 / SSE 流式）
- GET  /api/agent/projects/{id}/files   列出项目文件树
- GET  /api/agent/projects/{id}/file    读取项目文件内容

鉴权：admin / dev 角色可调用生成接口（readonly 禁用）。

注意：本文件不修改 main.py。接入方式见文末注释。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.graph import run_pipeline, stream_pipeline
from app.agent.workspace import get_workspace
from app.auth.models import User
from app.core.exceptions import AppError, NotFoundError
from app.deps import require_role

logger = logging.getLogger("agent.api")

router = APIRouter(prefix="/api/agent", tags=["agent"])


# ---- 请求/响应模型 ----


class AgentMessage(BaseModel):
    role: str
    content: str


class GenerateRequest(BaseModel):
    messages: List[AgentMessage] = Field(default_factory=list)
    stream: bool = False
    project_id: Optional[str] = None
    model: Optional[str] = None  # 透传，兼容前端 streamChat opts


# ---- 工具 ----


def _extract_query(messages: List[AgentMessage]) -> str:
    """从 messages 中提取用户需求（拼接所有 user 消息）。"""
    parts = [m.content for m in messages if m.role == "user" and m.content]
    if not parts:
        # 兜底：取最后一条任意角色消息
        if messages:
            return messages[-1].content or ""
        return ""
    return "\n".join(parts).strip()


def _ensure_project_id(req: GenerateRequest, user: User, query: str) -> str:
    """获取或创建 project_id。"""
    if req.project_id:
        return req.project_id
    ws = get_workspace()
    name = query[:40].replace("\n", " ").strip() or "generated_project"
    return ws.create_project(str(user.id), name)


# ---- 路由 ----


@router.post("/generate")
async def generate(
    req: GenerateRequest,
    user: User = Depends(require_role("admin", "dev")),
):
    """生成完整项目。

    - stream=false：跑完整图，返回最终结果。
    - stream=true ：SSE，每个节点完成时 yield 一个 chunk（content + meta），最后 yield [DONE]。

    前端 src/api/client.ts 的 streamChat 调用本接口（POST，body 含 messages + stream:true）。
    """
    if not req.messages:
        raise AppError("messages 不能为空", status_code=400)

    query = _extract_query(req.messages)
    if not query:
        raise AppError("未能在 messages 中找到用户需求", status_code=400)

    project_id = _ensure_project_id(req, user, query)
    user_id = str(user.id)
    role = user.role

    if not req.stream:
        # 非流式：跑完整管线
        final_state = await run_pipeline(
            user_query=query,
            user_id=user_id,
            role=role,
            project_id=project_id,
        )
        return _build_response(final_state, project_id)

    # 流式：SSE
    async def _sse():
        try:
            async for chunk in stream_pipeline(
                user_query=query,
                user_id=user_id,
                role=role,
                project_id=project_id,
            ):
                payload = {
                    "object": "agent.chunk",
                    "project_id": project_id,
                    "node": chunk.get("node"),
                    "role": chunk.get("role", "assistant"),
                    "content": chunk.get("content", ""),
                    "meta": chunk.get("meta", {}),
                }
                yield "data: %s\n\n" % json.dumps(payload, ensure_ascii=False)
        except Exception as e:
            logger.exception("流式生成失败: %s", e)
            err_payload = {
                "object": "agent.chunk",
                "project_id": project_id,
                "node": "error",
                "role": "assistant",
                "content": "生成失败：%s" % e,
                "meta": {"error": str(e)},
            }
            yield "data: %s\n\n" % json.dumps(err_payload, ensure_ascii=False)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _build_response(state: Dict[str, Any], project_id: str) -> Dict[str, Any]:
    """构造非流式最终响应。"""
    generated = state.get("generated_files") or {}
    return {
        "object": "agent.completion",
        "project_id": project_id,
        "status": state.get("status", "done"),
        "intent": state.get("intent", ""),
        "primary_language": state.get("primary_language", ""),
        "project_name": state.get("project_name", ""),
        "plan": state.get("plan", []),
        "file_tree": state.get("file_tree", []),
        "generated_files": generated,
        "file_count": len(generated),
        "run_result": state.get("run_result", {}),
        "build_log": state.get("build_log", ""),
        "fix_rounds": state.get("fix_rounds", 0),
        "messages": state.get("messages", []),
        "error": state.get("error"),
    }


@router.get("/projects/{project_id}/files")
async def list_project_files(
    project_id: str,
    user: User = Depends(require_role("admin", "dev")),
):
    """列出项目文件树。"""
    ws = get_workspace()
    if not ws.exists(project_id):
        raise NotFoundError("项目不存在: %s" % project_id)
    entries = ws.list_files(project_id)
    return {"project_id": project_id, "entries": entries, "count": len(entries)}


@router.get("/projects/{project_id}/file")
async def read_project_file(
    project_id: str,
    path: str = Query(..., description="相对项目根的文件路径"),
    user: User = Depends(require_role("admin", "dev")),
):
    """读取项目文件内容。"""
    ws = get_workspace()
    if not ws.exists(project_id):
        raise NotFoundError("项目不存在: %s" % project_id)
    try:
        content = ws.read_file(project_id, path)
    except FileNotFoundError:
        raise NotFoundError("文件不存在: %s" % path)
    except ValueError as e:
        raise AppError(str(e), status_code=400)
    return {"project_id": project_id, "path": path, "content": content}


# ============================================================================
# 接入说明（不修改 main.py）：
# 在 backend/app/main.py 的 create_app() 中加入：
#
#     from app.agent.api import router as agent_router
#     ...
#     app.include_router(agent_router)
#
# 即可使本路由生效。当前为避免与并行子代理冲突 main.py，未自动 include。
# ============================================================================

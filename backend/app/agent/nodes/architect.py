"""Architect 节点 - 架构设计（模块划分/依赖/接口契约/文件树）。"""
from __future__ import annotations

import logging
from typing import Any, Dict

from app.agent.state import AgentState, append_message
from app.gateway.router import get_gateway_router
from . import _common

logger = logging.getLogger("agent.nodes.architect")

_SYSTEM = (
    "你是架构师。基于用户需求与检索上下文，输出【严格 JSON】：\n"
    '{"architecture": {"modules": [...], "dependencies": [...], "interfaces": [...]}, '
    '"file_tree": ["相对路径1", "相对路径2", ...]}\n'
    "file_tree 必须覆盖全部模块的源文件 + 构建文件 + README。\n"
    "只输出 JSON，不要解释。"
)


async def architect_node(state: AgentState) -> Dict[str, Any]:
    """生成架构设计与文件树。返回 {architecture, file_tree, status, messages}。"""
    user_query = state.get("user_query", "")
    user_id = state.get("user_id", "")
    role = state.get("role", "")
    primary_language = state.get("primary_language", "")
    project_name = state.get("project_name", "")

    router = get_gateway_router()
    kb_hint = state.get("knowledge_context", {}) or {}
    exploration = state.get("exploration", {}) or {}
    exp_summary = str(exploration.get("summary") or "").strip()
    exp_files = list(exploration.get("files_read") or [])
    exp_part = ""
    if exp_summary or exp_files:
        exp_part = (
            "探索发现（agentic grep/glob/read）：\n"
            "摘要：%s\n已精读文件：%s\n"
        ) % (exp_summary or "(无)", ", ".join(exp_files[:10]) or "(无)")
    user_prompt = (
        "用户需求：%s\n"
        "语言：%s\n项目名：%s\n"
        "知识库命中(摘要)：code=%d buildops=%d dataapi=%d\n"
        "%s"
        "请输出架构 JSON。"
    ) % (
        user_query,
        primary_language or "python",
        project_name or "generated_project",
        len(kb_hint.get("code", []) or []),
        len(kb_hint.get("buildops", []) or []),
        len(kb_hint.get("dataapi", []) or []),
        exp_part,
    )
    raw = await _common.call_llm(router, user_id, role, _SYSTEM, user_prompt, max_tokens=1024)

    architecture: Dict[str, Any] = {}
    file_tree: list = []

    parsed = _common.parse_json_safely(raw)
    if isinstance(parsed, dict):
        architecture = parsed.get("architecture") if isinstance(parsed.get("architecture"), dict) else {}
        ft = parsed.get("file_tree")
        if isinstance(ft, list):
            file_tree = [str(x) for x in ft]

    # 回退 1：从骨架响应里解析文件路径
    if not file_tree:
        skeleton = _common.parse_skeleton(raw)
        if skeleton:
            file_tree = sorted(skeleton.keys())
            if not architecture:
                architecture = {"modules": list(skeleton.keys()), "dependencies": [], "interfaces": []}

    # 回退 2：默认文件树
    if not file_tree:
        file_tree = _common.default_file_tree(primary_language or "python", project_name or "generated_project")
    if not architecture:
        architecture = {
            "modules": file_tree,
            "dependencies": [],
            "interfaces": [],
            "language": primary_language or "python",
        }

    msg = "🏗 架构设计完成：%d 个文件，模块数=%d。" % (
        len(file_tree),
        len(architecture.get("modules", []) or []),
    )
    return {
        "architecture": architecture,
        "file_tree": file_tree,
        "status": "generating",
        "messages": append_message(
            state, msg, {"node": "architect", "file_tree": file_tree}
        ),
    }

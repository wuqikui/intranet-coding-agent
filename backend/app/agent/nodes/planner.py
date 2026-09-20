"""Planner 节点 - 意图分类 + 任务规划 + 语言/项目名推断。"""
from __future__ import annotations

import logging
from typing import Any, Dict

from app.agent.state import AgentState, append_message
from app.gateway.router import get_gateway_router
from . import _common

logger = logging.getLogger("agent.nodes.planner")

_SYSTEM = (
    "你是项目规划助手。阅读用户需求，输出【严格 JSON】：\n"
    '{"intent": "generate_project|fix_bug|explain|other", '
    '"project_name": "小写英文短名", '
    '"primary_language": "cpp|python|ts|javascript|go|rust|java", '
    '"plan": ["步骤1", "步骤2", ...]}\n'
    "只输出 JSON，不要解释。"
)


async def plan_node(state: AgentState) -> Dict[str, Any]:
    """意图分类 + 任务规划。返回 {intent, plan, primary_language, project_name, status, messages}。"""
    user_query = state.get("user_query", "")
    user_id = state.get("user_id", "")
    role = state.get("role", "")

    router = get_gateway_router()
    user_prompt = "用户需求：%s\n\n请输出规划 JSON。" % user_query
    raw = await _common.call_llm(router, user_id, role, _SYSTEM, user_prompt, max_tokens=512)

    intent = ""
    plan: list = []
    project_name = ""
    primary_language = ""

    parsed = _common.parse_json_safely(raw)
    if isinstance(parsed, dict):
        intent = str(parsed.get("intent") or "").strip()
        plan = list(parsed.get("plan") or [])
        project_name = str(parsed.get("project_name") or "").strip()
        primary_language = str(parsed.get("primary_language") or "").strip()
        if isinstance(plan, list):
            plan = [str(x) for x in plan]
        else:
            plan = []

    # 启发式兜底
    if not primary_language:
        primary_language = _common.detect_language(user_query)
    if not project_name:
        project_name = _common.extract_project_name(user_query, primary_language)
    if not intent:
        q = user_query.lower()
        if any(k in q for k in ("生成", "创建", "create", "build", "develop", "实现")):
            intent = "generate_project"
        elif any(k in q for k in ("修复", "fix", "bug")):
            intent = "fix_bug"
        else:
            intent = "generate_project"
    if not plan:
        plan = ["架构设计", "检索知识库", "代码生成", "写入工作区", "构建运行", "汇总结果"]

    msg = "📋 规划完成：意图=%s，语言=%s，项目=%s，%d 个步骤。" % (
        intent, primary_language, project_name, len(plan),
    )
    return {
        "intent": intent,
        "plan": plan,
        "primary_language": primary_language,
        "project_name": project_name,
        "status": "planning",
        "messages": append_message(state, msg, {"node": "planner", "intent": intent, "language": primary_language}),
    }

"""Agent 跨节点状态 Schema。

显式声明所有跨节点字段，防止 LangGraph 在未声明字段上静默失效。
每个节点返回部分状态更新 dict；为兼容 LangGraph（无 reducer 的字段会被整体替换），
列表/字典类累积字段（messages / generated_files / build_log）由各节点
读取当前 state 后返回"完整值"，从而在 LangGraph 与降级运行器中行为一致。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:  # Python 3.8+ typing.TypedDict
    from typing import TypedDict
except ImportError:  # pragma: no cover
    from typing_extensions import TypedDict  # type: ignore


class AgentState(TypedDict):
    """编码 Agent 管线跨节点共享状态。"""

    # --- 输入 ---
    user_query: str            # 用户原始需求
    user_id: str
    role: str
    project_id: str            # 工作区项目 ID
    project_name: str          # 推导出的项目名
    primary_language: str      # 主工程语言（cpp/python/ts/...）

    # --- 规划 / 架构 ---
    intent: str                # 意图分类
    plan: List[str]            # 任务规划步骤
    architecture: Dict[str, Any]   # 架构设计（模块/依赖/接口契约）
    file_tree: List[str]       # 要生成的文件路径列表

    # --- 检索 ---
    knowledge_context: Dict[str, Any]   # 前置检索结果
    code_style: Dict[str, Any]          # 代码风格规范

    # --- 生成 ---
    generated_files: Dict[str, str]     # path -> content

    # --- 构建 / 运行 ---
    build_log: str             # 工程循环日志（编译/修复）
    fix_rounds: int            # 修复轮次
    run_result: Dict[str, Any]   # install→build→run 结果

    # --- 流转 ---
    status: str                # planning|generating|building|done|failed
    error: Optional[str]
    messages: List[Dict[str, Any]]   # 对外消息流（供前端 SSE）


def initial_state(
    user_query: str,
    user_id: str = "",
    role: str = "",
    project_id: str = "",
    project_name: str = "",
) -> Dict[str, Any]:
    """构造一个包含全部字段的初始 state（避免缺字段导致 LangGraph 静默跳过）。"""
    return {
        "user_query": user_query,
        "user_id": user_id,
        "role": role,
        "project_id": project_id,
        "project_name": project_name,
        "primary_language": "",
        "intent": "",
        "plan": [],
        "architecture": {},
        "file_tree": [],
        "knowledge_context": {},
        "code_style": {},
        "generated_files": {},
        "build_log": "",
        "fix_rounds": 0,
        "run_result": {},
        "status": "planning",
        "error": None,
        "messages": [],
    }


def append_message(state: Dict[str, Any], content: str, meta: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """返回追加了新消息后的完整 messages 列表（节点直接作为返回值）。"""
    msgs = list(state.get("messages") or [])
    msgs.append({"role": "assistant", "content": content, "meta": meta or {}})
    return msgs

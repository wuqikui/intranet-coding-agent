"""Explorer 节点 - agentic grep/glob/read 迭代探索（retriever 之后、architect 之前）。

语料 = KB code 通道命中文档（retriever 结果）+ 工作区既有文件（增量场景）。
LLM 逐轮发起 grep/glob/read 工具调用，摸清既有代码结构后把关键发现写入
state["exploration"]，供 architect/generator 生成时参考。

降级语义（与 FallbackGraph 一致）：
- 语料为空 → skipped（不阻断）
- mock/异常模型连续输出无效 JSON → degraded（不阻断）
- 任何异常 → degraded（不阻断）
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from app.agent.explorer import build_corpus, run_exploration
from app.agent.state import AgentState, append_message
from app.agent.workspace import get_workspace
from app.gateway.router import get_gateway_router

logger = logging.getLogger("agent.nodes.explorer")


async def explorer_node(state: AgentState) -> Dict[str, Any]:
    """agentic 代码探索。返回 {exploration, messages}。"""
    user_query = state.get("user_query", "")
    user_id = state.get("user_id", "")
    role = state.get("role", "")
    project_id = state.get("project_id", "")

    kb_code_hits = list((state.get("knowledge_context") or {}).get("code") or [])

    # 工作区既有文件（存在且非空时才纳入）
    ws_root = None
    if project_id:
        try:
            root = get_workspace().project_root(project_id)
            if root.exists() and any(root.iterdir()):
                ws_root = root
        except Exception as e:
            logger.info("工作区不可访问，探索仅用 KB 命中: %s", e)

    try:
        corpus = build_corpus(kb_code_hits, ws_root)
        if len(corpus) == 0:
            msg = "🔎 代码探索跳过：无 KB 命中且工作区为空。"
            return {
                "exploration": {"status": "skipped", "error": "empty corpus", "rounds": 0,
                                "tool_calls": [], "files_read": [], "summary": ""},
                "messages": append_message(state, msg, {"node": "explorer", "status": "skipped"}),
            }

        router = get_gateway_router()
        exploration = await run_exploration(
            router, user_id, role, user_query, corpus, max_rounds=4
        )
    except Exception as e:  # 双保险：探索绝不阻断管线
        logger.warning("探索循环异常（降级跳过）: %s", e)
        exploration = {"status": "degraded", "error": str(e), "rounds": 0,
                       "tool_calls": [], "files_read": [], "summary": ""}

    if exploration.get("status") == "ok":
        msg = "🔎 代码探索完成：%d 轮工具调用（grep/glob/read），精读 %d 个文件。关键发现：%s" % (
            len(exploration.get("tool_calls", [])),
            len(exploration.get("files_read", [])),
            exploration.get("summary") or "(无)",
        )
    else:
        msg = "🔎 代码探索降级（%s）：%s。管线继续。" % (
            exploration.get("status", "unknown"),
            exploration.get("error") or "无有效探索",
        )

    return {
        "exploration": exploration,
        "messages": append_message(
            state, msg,
            {
                "node": "explorer",
                "status": exploration.get("status"),
                "rounds": exploration.get("rounds", 0),
                "tool_calls": len(exploration.get("tool_calls", [])),
                "files_read": exploration.get("files_read", []),
            },
        ),
    }

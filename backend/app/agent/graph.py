"""LangGraph 编排 - 完整项目生成管线。

优先使用 langgraph（如已安装）；未安装时降级到内置的顺序运行器
（FallbackGraph），保证管线在任何环境都能跑通。

图结构：
    planner → retriever → architect → generator → writer → runner
        └ runner 失败且 fix_rounds<1 → 回 generator（LLM 修复重试 1 轮）
        └ 否则 → finalizer → END

修复轮次上限（Task 8）：
- 规则修复：BuildLoop 内部最多 N=settings.MAX_FIX_ROUNDS（≥5）轮，
  对编译错误做规则化自动修复（include/std::/分号/CMake 可见性等）。
- LLM 修复：graph 条件边控制，最多 1 轮（fix_rounds<1），由 generator 节点
  根据 build_log 错误重新生成代码，再回 runner 再跑 N 轮规则修复。
- 总上限：N 规则 + 1 LLM + N 规则 = 2N+1 轮（N≥5 时 ≥11 轮）。
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, Optional

from app.agent.nodes import NODES, NODE_ORDER
from app.agent.state import AgentState, initial_state

logger = logging.getLogger("agent.graph")

try:  # langgraph 可选依赖
    from langgraph.graph import END, StateGraph  # type: ignore

    _HAS_LANGGRAPH = True
except Exception:  # pragma: no cover - 环境差异
    _HAS_LANGGRAPH = False
    StateGraph = None  # type: ignore
    END = "END"  # type: ignore


def _runner_route(state: Dict[str, Any]) -> str:
    """runner 条件边：失败且 LLM 修复轮次<1 → 回 generator；否则 → finalizer。

    注：BuildLoop 内部已跑最多 N=settings.MAX_FIX_ROUNDS（≥5）轮规则修复；
    此处只控制 LLM 修复重试上限（保守 1 轮，避免循环放大成本）。
    """
    if state.get("status") == "failed" and int(state.get("fix_rounds", 0) or 0) < 1:
        return "generator"
    return "finalizer"


# ---- langgraph 实现 ----


def _build_langgraph():
    """用 langgraph 构建编译图。"""
    g = StateGraph(AgentState)
    g.add_node("planner", NODES["planner"])
    g.add_node("retriever", NODES["retriever"])
    g.add_node("architect", NODES["architect"])
    g.add_node("generator", NODES["generator"])
    g.add_node("writer", NODES["writer"])
    g.add_node("runner", NODES["runner"])
    g.add_node("finalizer", NODES["finalizer"])
    g.set_entry_point("planner")
    g.add_edge("planner", "retriever")
    g.add_edge("retriever", "architect")
    g.add_edge("architect", "generator")
    g.add_edge("generator", "writer")
    g.add_edge("writer", "runner")
    # 失败重试：runner 失败 → generator（Task 8 接真实编译+修复循环，此处先单次重试）
    g.add_conditional_edges("runner", _runner_route)
    g.add_edge("finalizer", END)
    return g.compile()


# ---- 降级运行器 ----


class FallbackGraph:
    """langgraph 未安装时的顺序运行器，提供与编译图一致的 ainvoke / astream。

    支持 runner→generator 的单次修复重试（与条件边语义一致）。
    """

    name = "FallbackGraph"

    async def ainvoke(self, input_state: Dict[str, Any], config: Optional[Dict] = None) -> Dict[str, Any]:
        """顺序执行全部节点，返回最终 state。"""
        state: Dict[str, Any] = dict(input_state)
        # 固定前序
        for name in ("planner", "retriever", "architect"):
            state.update(await NODES[name](state))
        # generator → writer → runner，带单次修复重试
        for _ in range(2):  # 至多 2 轮（首次 + 1 次重试）
            state.update(await NODES["generator"](state))
            state.update(await NODES["writer"](state))
            state.update(await NODES["runner"](state))
            if _runner_route(state) != "generator":
                break
        state.update(await NODES["finalizer"](state))
        return state

    async def astream(self, input_state: Dict[str, Any], config: Optional[Dict] = None) -> AsyncIterator[Dict[str, Any]]:
        """顺序执行，每个节点完成后 yield 其新追加的消息。"""
        state: Dict[str, Any] = dict(input_state)
        prev_msg_count = len(state.get("messages") or [])

        async def _run_node(name: str) -> AsyncIterator[Dict[str, Any]]:
            nonlocal prev_msg_count
            state.update(await NODES[name](state))
            msgs = state.get("messages") or []
            for m in msgs[prev_msg_count:]:
                yield {"node": name, **m}
            prev_msg_count = len(msgs)

        for name in ("planner", "retriever", "architect"):
            async for chunk in _run_node(name):
                yield chunk

        for attempt in range(2):
            for name in ("generator", "writer", "runner"):
                async for chunk in _run_node(name):
                    yield chunk
            if _runner_route(state) != "generator":
                break

        async for chunk in _run_node("finalizer"):
            yield chunk


# ---- 统一构建入口 ----


def build_agent_graph():
    """构建 Agent 图：langgraph 可用则用之，否则返回 FallbackGraph。"""
    if _HAS_LANGGRAPH:
        try:
            return _build_langgraph()
        except Exception as e:
            logger.warning("langgraph 构建失败，降级到 FallbackGraph: %s", e)
    return FallbackGraph()


# ---- 便捷运行函数（供 API 直接调用，避免关心图类型）----


async def run_pipeline(
    user_query: str,
    user_id: str = "",
    role: str = "",
    project_id: str = "",
    project_name: str = "",
) -> Dict[str, Any]:
    """非流式跑完整管线，返回最终 state。"""
    graph = build_agent_graph()
    state = initial_state(
        user_query=user_query,
        user_id=user_id,
        role=role,
        project_id=project_id,
        project_name=project_name,
    )
    if hasattr(graph, "ainvoke"):
        return await graph.ainvoke(state)
    # 兜底
    return await FallbackGraph().ainvoke(state)


async def stream_pipeline(
    user_query: str,
    user_id: str = "",
    role: str = "",
    project_id: str = "",
    project_name: str = "",
) -> AsyncIterator[Dict[str, Any]]:
    """流式跑管线，逐节点 yield 消息 chunk。"""
    graph = build_agent_graph()
    state = initial_state(
        user_query=user_query,
        user_id=user_id,
        role=role,
        project_id=project_id,
        project_name=project_name,
    )
    if hasattr(graph, "astream"):
        async for chunk in graph.astream(state):
            yield chunk
    else:
        async for chunk in FallbackGraph().astream(state):
            yield chunk

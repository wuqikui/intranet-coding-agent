"""Agent 管线节点集合。

每个节点是 async 函数 `async def node_xxx(state: AgentState) -> dict`，
返回部分状态更新。NODE_ORDER 定义了默认执行顺序（与 graph.py 的边一致）。
"""
from __future__ import annotations

from typing import Awaitable, Callable, Dict, List

from app.agent.state import AgentState

from .architect import architect_node
from .finalizer import finalizer_node
from .generator import generator_node
from .planner import plan_node
from .retriever import retriever_node
from .runner import runner_node
from .writer import writer_node

__all__ = [
    "plan_node",
    "retriever_node",
    "architect_node",
    "generator_node",
    "writer_node",
    "runner_node",
    "finalizer_node",
    "NODE_ORDER",
    "NODES",
]

# 节点执行顺序（与 graph.py 的边一致）
NODE_ORDER: List[str] = [
    "planner",
    "retriever",
    "architect",
    "generator",
    "writer",
    "runner",
    "finalizer",
]

# 名称 → 节点函数
NODES: Dict[str, Callable[[AgentState], Awaitable[Dict]]] = {
    "planner": plan_node,
    "retriever": retriever_node,
    "architect": architect_node,
    "generator": generator_node,
    "writer": writer_node,
    "runner": runner_node,
    "finalizer": finalizer_node,
}

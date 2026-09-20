"""Finalizer 节点 - 汇总，状态置 done，返回最终消息。"""
from __future__ import annotations

import logging
from typing import Any, Dict

from app.agent.state import AgentState, append_message

logger = logging.getLogger("agent.nodes.finalizer")


async def finalizer_node(state: AgentState) -> Dict[str, Any]:
    """汇总最终结果。返回 {status, run_result, messages}。"""
    generated = state.get("generated_files") or {}
    run_result = state.get("run_result") or {}
    project_id = state.get("project_id", "")
    status = state.get("status", "")

    final_status = "done" if status in ("built_skeleton", "done") else (status or "done")

    summary = (
        "✅ 项目生成完成！\n"
        "项目 ID：%s\n"
        "生成文件：%d 个\n"
        "项目类型：%s\n"
        "一键运行：bash run.sh"
    ) % (
        project_id or "(未创建)",
        len(generated),
        run_result.get("project_type", "unknown"),
    )

    return {
        "status": final_status,
        "run_result": run_result,
        "messages": append_message(
            state,
            summary,
            {
                "node": "finalizer",
                "project_id": project_id,
                "file_count": len(generated),
                "files": list(generated.keys()),
                "status": final_status,
            },
        ),
    }

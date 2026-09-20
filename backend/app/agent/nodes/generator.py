"""Generator 节点 - 按 file_tree 调用 GatewayRouter 生成代码。

策略：
1. 一次 LLM 调用生成全部文件（要求 JSON {path: content}）。
2. 解析优先级：JSON > 骨架解析(mock 模板) > 默认骨架(按语言)。
3. 真实 LLM 失败/mock 兜底均能产出非空 generated_files。
4. 修复重入：state.status=="failed" 时 fix_rounds +1。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from app.agent.state import AgentState, append_message
from app.audit.logger import AuditLogger  # noqa: F401  (类型提示)
from app.deps import get_audit_logger
from app.gateway.router import get_gateway_router
from . import _common

logger = logging.getLogger("agent.nodes.generator")

_SYSTEM = (
    "你是资深工程师。按文件树生成完整可运行的项目代码。\n"
    "输出【严格 JSON】：{\"<相对路径>\": \"<文件完整内容>\", ...}\n"
    "要求：覆盖文件树中的所有文件；内容为纯代码，不要 markdown 围栏；不要解释。\n"
    "若收到修复指令，请针对 build_log 中的错误最小化修复。"
)


async def generator_node(state: AgentState) -> Dict[str, Any]:
    """按 file_tree 生成代码。返回 {generated_files, file_tree, fix_rounds, status, messages}。"""
    user_query = state.get("user_query", "")
    user_id = state.get("user_id", "")
    role = state.get("role", "")
    primary_language = state.get("primary_language", "")
    project_name = state.get("project_name", "")
    file_tree = list(state.get("file_tree") or [])
    build_log = state.get("build_log", "")
    prev_status = state.get("status", "")

    # 修复重入计数
    is_fix = prev_status == "failed"
    fix_rounds = int(state.get("fix_rounds", 0)) + (1 if is_fix else 0)

    router = get_gateway_router()
    user_prompt = (
        "用户需求：%s\n语言：%s\n项目名：%s\n"
        "文件树：\n%s\n"
        "代码风格：%s\n"
        % (
            user_query,
            primary_language or "python",
            project_name or "generated_project",
            "\n".join("- " + p for p in file_tree) or "- (无，请自行规划)",
            str(state.get("code_style", {})),
        )
    )
    if is_fix and build_log:
        user_prompt += "\n上一轮构建日志（请据此修复）：\n%s\n" % build_log[:2000]

    raw = await _common.call_llm(router, user_id, role, _SYSTEM, user_prompt, max_tokens=4096)

    generated = _build_generated(raw, file_tree, primary_language or "python", project_name or "generated_project")

    # 合并既有生成结果（修复时保留未变文件）
    if is_fix:
        prev_files = dict(state.get("generated_files") or {})
        prev_files.update(generated)
        generated = prev_files

    # 审计
    try:
        audit = get_audit_logger()
        audit.log(
            user_id=user_id,
            role=role,
            action="agent_generate",
            model=getattr(router, "_model", ""),
            prompt=user_prompt[:2000],
            extra={
                "files": list(generated.keys()),
                "file_count": len(generated),
                "fix_rounds": fix_rounds,
                "is_fix": is_fix,
            },
        )
    except Exception as e:
        logger.warning("agent_generate 审计失败: %s", e)

    # 同步 file_tree（以实际生成文件为准，保证后续 writer 一致）
    final_tree = sorted(generated.keys()) if generated else file_tree

    msg = "✍ 代码生成完成：%d 个文件%s。" % (
        len(generated),
        "（修复第 %d 轮）" % fix_rounds if is_fix else "",
    )
    return {
        "generated_files": generated,
        "file_tree": final_tree,
        "fix_rounds": fix_rounds,
        "status": "generating",
        "messages": append_message(
            state, msg, {"node": "generator", "files": list(generated.keys()), "fix_rounds": fix_rounds}
        ),
    }


def _build_generated(
    raw: str,
    file_tree: list,
    language: str,
    project_name: str,
) -> Dict[str, str]:
    """从 LLM 响应中构造 {path: content}。"""
    # 1. JSON
    parsed = _common.parse_json_safely(raw)
    if isinstance(parsed, dict) and parsed:
        out = {}
        for k, v in parsed.items():
            if isinstance(k, str) and isinstance(v, str):
                out[k.strip()] = v
        if out:
            return out

    # 2. 骨架解析（mock 模板）
    skeleton = _common.parse_skeleton(raw)
    if skeleton:
        return skeleton

    # 3. file_tree 单文件 → 整段响应作为该文件内容
    if len(file_tree) == 1 and raw.strip():
        return {file_tree[0]: raw.strip() + "\n"}

    # 4. 默认骨架
    default = _common.default_skeleton(language, project_name)
    if default:
        return default

    # 5. 终极兜底
    return {"README.md": "# 生成失败\n\nLLM 未返回有效内容。\n\n```\n%s\n```" % raw[:2000]}

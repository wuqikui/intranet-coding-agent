"""Writer 节点 - 写入工作区前做风格一致性检查 + 最小化重写（Task 9）。

Task 9 升级：
- 写入前对每个生成文件调 check_consistency 与 state.code_style 比对
- 不符则调 auto_rewrite 做最小化重写（缩进/命名转换；行长仅记录）
- 重写结果同步回 generated_files，供后续 runner/finalizer 使用
- 检查与重写记录到 messages + 审计
- 知识库无 code 通道命中时，code_style 为语言默认（不擅自套用预设风格）
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.agent.state import AgentState, append_message
from app.agent.style import (
    CodeStyleProfile,
    auto_rewrite,
    check_consistency,
)
from app.agent.workspace import get_workspace

logger = logging.getLogger("agent.nodes.writer")


async def writer_node(state: AgentState) -> Dict[str, Any]:
    """写入工作区，附风格检查 + 重写。返回 {project_id, status, generated_files, messages}。"""
    project_id = state.get("project_id", "")
    generated = dict(state.get("generated_files") or {})
    code_style_dict = dict(state.get("code_style") or {})
    primary_language = state.get("primary_language", "") or "python"

    if not project_id:
        ws = get_workspace()
        project_id = ws.create_project(
            state.get("user_id", ""),
            state.get("project_name", "") or "generated_project",
        )
    else:
        ws = get_workspace()

    profile = CodeStyleProfile.from_dict(code_style_dict)
    # 若 code_style 为空（KB 无命中且无默认），回退到语言默认
    if not code_style_dict:
        from app.agent.style import get_language_default
        profile = get_language_default(primary_language)

    rewritten: List[Dict[str, Any]] = []
    violations_total = 0
    written: List[str] = []
    failed: List[Dict[str, Any]] = []

    for rel_path, content in list(generated.items()):
        # 跳过非代码文件（README.md / run.sh / docker-compose.yml 等）
        if not _is_code_file(rel_path, primary_language):
            try:
                ws.write_file(project_id, rel_path, content)
                written.append(rel_path)
            except Exception as e:
                logger.warning("写入文件失败 %s: %s", rel_path, e)
                failed.append({"path": rel_path, "error": str(e)})
            continue

        # 风格一致性检查
        try:
            violations = check_consistency(content, profile, primary_language, rel_path)
            violations_total += len(violations)
        except Exception as e:
            logger.warning("风格检查失败 %s: %s", rel_path, e)
            violations = []

        # 自动重写（最小化）
        try:
            rewrite_result = auto_rewrite(content, profile, primary_language)
            if rewrite_result.applied:
                rewritten.append({
                    "file": rel_path,
                    "applied": rewrite_result.applied,
                    "violations_before": len(violations),
                })
                content = rewrite_result.content
                # 同步回 generated_files
                generated[rel_path] = content
        except Exception as e:
            logger.warning("风格重写失败 %s: %s", rel_path, e)

        # 写入（重写后）
        try:
            ws.write_file(project_id, rel_path, content)
            written.append(rel_path)
        except Exception as e:
            logger.warning("写入文件失败 %s: %s", rel_path, e)
            failed.append({"path": rel_path, "error": str(e)})

    msg = "💾 写入完成：%d 个文件；风格检查 %d 项不一致，自动重写 %d 个文件。" % (
        len(written), violations_total, len(rewritten),
    )
    return {
        "project_id": project_id,
        "generated_files": generated,
        "status": "building",
        "messages": append_message(
            state,
            msg,
            {
                "node": "writer",
                "written": written,
                "failed": failed,
                "violations": violations_total,
                "rewritten": rewritten,
                "code_style_source": code_style_dict.get("source", "language_default"),
            },
        ),
    }


def _is_code_file(path: str, language: str) -> bool:
    """是否为代码文件（值得做风格检查/重写）。"""
    p = path.lower()
    # 排除文档/配置/脚本（除非是项目主语言文件）
    if p.endswith((
        ".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml",
        ".sh", ".bat", ".ps1", ".dockerfile", ".gitignore",
        ".lock", ".cfg", ".ini", ".conf",
    )):
        return False
    if p.endswith((
        ".py", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh", ".hxx",
        ".ts", ".tsx", ".js", ".jsx",
        ".go", ".rs", ".java", ".kt",
    )):
        return True
    # CMakeLists.txt 当作代码（CMake 风格）
    if p.endswith("cmakelists.txt"):
        return True
    return False

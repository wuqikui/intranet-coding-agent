"""Runner 节点 - install→build→run 真实编译 + 自动修复（Task 8）。

Task 8 升级（替换 Task 7 骨架）：
- 调用 BuildLoop 跑最多 N=settings.MAX_FIX_ROUNDS（≥5）轮真实编译
- 编译器不可用（tooling_missing）→ 标记 tooling_missing，不视为失败
- 生成 run.sh / docker-compose.yml 保证一键运行（部署机有 docker 时可执行）
- 状态：passed | tooling_missing | failed
  - passed/tooling_missing 不触发 graph 修复重试
  - failed 触发 graph 条件边 → generator LLM 修复 1 轮 → 回 runner
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from app.agent.build_loop import get_build_loop
from app.agent.state import AgentState, append_message
from app.agent.workspace import get_workspace
from app.deps import get_audit_logger
from app.gateway.router import get_gateway_router  # noqa: F401  (保留导入位置)

logger = logging.getLogger("agent.nodes.runner")


def _detect_project_type(generated: Dict[str, str]) -> str:
    """根据生成的文件检测项目类型。"""
    paths = {p.lower() for p in generated.keys()}
    if "cmakelists.txt" in paths:
        return "cmake"
    if "makefile" in paths:
        return "make"
    if "package.json" in paths:
        return "node"
    if "cargo.toml" in paths:
        return "rust"
    if "go.mod" in paths:
        return "go"
    if "pom.xml" in paths or any(p.endswith(".java") for p in paths):
        return "java"
    if "pyproject.toml" in paths or "setup.py" in paths or "requirements.txt" in paths:
        return "python"
    if any(p.endswith(".py") for p in paths):
        return "python"
    if any(p.endswith((".cpp", ".cc", ".cxx", ".c")) for p in paths):
        return "cpp_source"
    return ""


def _build_scripts(project_type: str) -> Dict[str, str]:
    """按项目类型生成一键运行脚本。"""
    scripts: Dict[str, str] = {}
    if project_type == "cmake" or project_type == "cpp_source":
        scripts["run.sh"] = (
            "#!/usr/bin/env bash\nset -e\ncmake -B build\ncmake --build build\n"
            + ("./build/*_app 2>/dev/null || ls build/\n")
        )
        scripts["docker-compose.yml"] = (
            "version: '3'\nservices:\n  build:\n"
            "    image: gcc:12\n    volumes:\n      - ./:/work\n    working_dir: /work\n"
            "    command: bash run.sh\n"
        )
    elif project_type == "make":
        scripts["run.sh"] = "#!/usr/bin/env bash\nset -e\nmake\nmake run\n"
    elif project_type == "node":
        scripts["run.sh"] = "#!/usr/bin/env bash\nset -e\nnpm install\nnpm start\n"
    elif project_type == "rust":
        scripts["run.sh"] = "#!/usr/bin/env bash\nset -e\ncargo build\ncargo run\n"
    elif project_type == "go":
        scripts["run.sh"] = "#!/usr/bin/env bash\nset -e\ngo mod download\ngo run .\n"
    elif project_type == "java":
        scripts["run.sh"] = "#!/usr/bin/env bash\nset -e\nmvn package\njava -jar target/*.jar\n"
    else:  # python 默认
        scripts["run.sh"] = (
            "#!/usr/bin/env bash\nset -e\n"
            "pip install -r requirements.txt 2>/dev/null || true\npython main.py\n"
        )
    return scripts


async def runner_node(state: AgentState) -> Dict[str, Any]:
    """install→build→run 真实编译 + 自动修复。返回 {run_result, build_log, status, messages}。"""
    project_id = state.get("project_id", "")
    generated = dict(state.get("generated_files") or {})
    user_id = state.get("user_id", "")
    role = state.get("role", "")

    project_type = _detect_project_type(generated) or "python"
    scripts = _build_scripts(project_type)

    # 写入运行脚本到工作区
    ws = get_workspace()
    written_scripts: List[str] = []
    if project_id:
        for name, content in scripts.items():
            try:
                ws.write_file(project_id, name, content)
                written_scripts.append(name)
                # 同步回 generated_files，便于前端查看
                generated[name] = content
            except Exception as e:
                logger.warning("写入运行脚本失败 %s: %s", name, e)

    # 调 BuildLoop 跑真实编译 + 自动修复（最多 N=settings.MAX_FIX_ROUNDS 轮）
    build_log_lines: List[str] = [
        "==== 工程循环日志（Task 8 真实编译） ====",
        "项目类型: %s" % project_type,
        "生成运行脚本: %s" % (", ".join(written_scripts) or "(无 project_id)"),
        "沙箱模式: %s" % _sandbox_mode(),
    ]

    prev_status = state.get("status", "")
    is_llm_fix = prev_status == "failed"
    prev_fix_rounds = int(state.get("fix_rounds", 0) or 0)

    build_result_dict: Dict[str, Any] = {}
    if project_id:
        project_root = ws.project_root(project_id)
        files_for_build = [k for k in generated.keys() if k not in ("run.sh", "docker-compose.yml")]
        try:
            loop = get_build_loop()
            result = await loop.run(Path(project_root), files_for_build)
            build_result_dict = result.to_dict()
            build_log_lines.append("工程循环状态: %s" % result.status)
            build_log_lines.append("实际轮次: %d / %d (max)" % (result.rounds, loop.max_rounds))
            build_log_lines.append("错误数: %d" % len(result.errors))
            build_log_lines.append("修复动作: %d" % len(result.fixes))
            build_log_lines.append("Lint 发现: %d" % len(result.linter_findings))
            build_log_lines.append("产物: %s" % (", ".join(result.artifacts[:10]) or "(无)"))
            # 追加 BuildLoop 自己的日志
            build_log_lines.append("")
            build_log_lines.append("--- BuildLoop 内部日志 ---")
            build_log_lines.append(result.build_log)
        except Exception as e:
            logger.exception("BuildLoop 执行异常: %s", e)
            build_log_lines.append("BuildLoop 异常: %s" % e)
            build_result_dict = {
                "status": "failed",
                "error": str(e),
                "rounds": 0,
            }
    else:
        build_log_lines.append("无 project_id，跳过真实编译")
        build_result_dict = {"status": "no_project", "note": "无 project_id"}

    # 状态映射：
    #   BuildLoop passed / tooling_missing / no_project → 视为构建骨架完成，不触发重试
    #   BuildLoop failed → 触发 graph 条件边（fix_rounds<1 时回 generator LLM 修复）
    loop_status = build_result_dict.get("status", "unknown")
    if loop_status in ("passed", "tooling_missing", "no_project"):
        graph_status = "built"
    else:
        graph_status = "failed"

    # fix_rounds 计数：只在 LLM 修复重入时 +1（BuildLoop 内部的规则修复轮次单独记录在 build_result.rounds）
    fix_rounds = prev_fix_rounds + (1 if is_llm_fix else 0)

    run_result: Dict[str, Any] = {
        "project_type": project_type,
        "scripts": written_scripts,
        "build_loop": build_result_dict,
        "install": {
            "status": "skipped" if loop_status != "passed" else "done",
            "note": "由 BuildLoop 统一处理（含 cmake/npm install 等）",
        },
        "build": {
            "status": loop_status,
            "rounds": build_result_dict.get("rounds", 0),
            "max_rounds": _max_fix_rounds(),
        },
        "run": {
            "status": "skeleton" if loop_status != "passed" else "ready",
            "note": "运行脚本 run.sh 已生成；部署机执行 bash run.sh 一键运行",
        },
        "sandbox_implemented": True,
        "errors": build_result_dict.get("errors", []),
        "fixes": build_result_dict.get("fixes", []),
        "linter_findings": build_result_dict.get("linter_findings", []),
        "artifacts": build_result_dict.get("artifacts", []),
    }

    # 审计
    try:
        audit = get_audit_logger()
        audit.log(
            user_id=user_id,
            role=role,
            action="agent_build",
            extra={
                "project_id": project_id,
                "project_type": project_type,
                "scripts": written_scripts,
                "build_status": loop_status,
                "rounds": build_result_dict.get("rounds", 0),
                "errors": len(build_result_dict.get("errors", [])),
                "fixes": len(build_result_dict.get("fixes", [])),
                "sandbox_implemented": True,
            },
        )
    except Exception as e:
        logger.warning("agent_build 审计失败: %s", e)

    # 对外消息
    if loop_status == "passed":
        msg = "✅ 真实编译通过：%s，%d 轮，产物 %d 个。" % (
            project_type, build_result_dict.get("rounds", 0),
            len(build_result_dict.get("artifacts", [])),
        )
    elif loop_status == "tooling_missing":
        msg = "⚠ 沙箱工具链不可用（%s）：跳过真实编译，等待部署机具备 cmake/g++ 等工具后自动启用。规则扫描发现 %d 条。" % (
            project_type,
            len(build_result_dict.get("linter_findings", [])),
        )
    elif loop_status == "no_project":
        msg = "🔧 构建骨架完成：类型=%s，已生成 %s。" % (
            project_type, ", ".join(written_scripts) or "(无脚本)",
        )
    else:
        msg = "❌ 真实编译失败：%s，%d 轮修复后仍有 %d 个错误。将由 LLM 尝试 1 轮修复。" % (
            project_type,
            build_result_dict.get("rounds", 0),
            len(build_result_dict.get("errors", [])),
        )

    return {
        "run_result": run_result,
        "build_log": (state.get("build_log") or "") + "\n".join(build_log_lines) + "\n",
        "generated_files": generated,
        "fix_rounds": fix_rounds,
        "status": graph_status,
        "messages": append_message(
            state,
            msg,
            {
                "node": "runner",
                "project_type": project_type,
                "scripts": written_scripts,
                "build_status": loop_status,
                "rounds": build_result_dict.get("rounds", 0),
            },
        ),
    }


def _sandbox_mode() -> str:
    try:
        from app.config import get_settings
        return get_settings().SANDBOX_MODE
    except Exception:
        return "unknown"


def _max_fix_rounds() -> int:
    try:
        from app.config import get_settings
        n = get_settings().MAX_FIX_ROUNDS
        return n if n >= 5 else 5
    except Exception:
        return 5

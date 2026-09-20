"""工具 API 路由 - 文件/搜索/Shell/git diff/单测/lint。

路由前缀 /api/tools（全部 require_role("admin","dev")）：
- POST /file/read     读项目文件
- POST /file/write    写项目文件（含危险路径检测）
- POST /file/list     列目录
- POST /search        全局搜索（文件名 + 内容 grep）
- POST /shell         执行 shell（危险命令检测 + 二次确认）
- POST /git-diff      生成 git diff 建议
- POST /test          跑单测
- POST /lint          跑 lint（pylint/flake8/cppcheck/clang-tidy）

TR-10.1: 危险命令（rm/git push/删库等）返回 449 needs_confirm，前端弹确认；
        confirm=true 才执行；block 级（不可逆，如 rm -rf /、mkfs、DROP）即便
        confirm=true 也拒绝。
TR-10.2: readonly 角色调用任何工具返回 403。

注意：本文件不修改 main.py。接入方式见文末注释。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.agent.workspace import get_workspace
from app.auth.models import User
from app.core.exceptions import AppError, ForbiddenError, NotFoundError
from app.deps import get_audit_logger, get_current_user, require_role
from app.sandbox.dangerous import get_danger_detector
from app.sandbox.runner import get_sandbox_runner

logger = logging.getLogger("api.tools")

router = APIRouter(prefix="/api/tools", tags=["tools"])


# 鉴权：全部需要 admin/dev 角色；readonly 会在 require_role 层 403 (TR-10.2)
_READ_WRITE_DEP = Depends(require_role("admin", "dev"))


# ============================================================================
# 请求/响应模型
# ============================================================================


class FileReadRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)


class FileWriteRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)
    content: str
    overwrite: bool = True


class FileListRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    subpath: str = ""


class SearchRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1, description="搜索关键词")
    mode: str = Field("content", description="content | filename | both")
    max_results: int = Field(50, ge=1, le=500)


class ShellRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    command: List[str] = Field(..., min_length=1)
    confirm: bool = False
    timeout: int = Field(60, ge=1, le=600)


class GitDiffRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    cached: bool = False
    ref: Optional[str] = None


class TestRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    target: str = ""  # 空=全部
    extra_args: List[str] = Field(default_factory=list)


class LintRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    files: List[str] = Field(..., min_length=1)
    linter: str = "auto"  # auto | pylint | flake8 | cppcheck | clang-tidy


# ============================================================================
# 内部工具
# ============================================================================


def _project_root(project_id: str, user: User) -> Path:
    """获取项目根目录，不存在则抛 NotFoundError。"""
    ws = get_workspace()
    if not ws.exists(project_id):
        raise NotFoundError("项目不存在: %s" % project_id)
    return ws.project_root(project_id)


def _audit_tool(user: User, action: str, project_id: str, **extra) -> None:
    """记录工具调用审计。"""
    try:
        audit = get_audit_logger()
        audit.log(
            user_id=str(user.id),
            role=user.role,
            action=action,
            extra={"project_id": project_id, **extra},
        )
    except Exception as e:
        logger.warning("工具审计失败 %s: %s", action, e)


# ============================================================================
# 文件读写
# ============================================================================


@router.post("/file/read", dependencies=[_READ_WRITE_DEP])
async def file_read(body: FileReadRequest, user: User = Depends(get_current_user)):
    """读项目文件（dev/admin）。"""
    ws = get_workspace()
    if not ws.exists(body.project_id):
        raise NotFoundError("项目不存在: %s" % body.project_id)
    try:
        content = ws.read_file(body.project_id, body.path)
    except FileNotFoundError:
        raise NotFoundError("文件不存在: %s" % body.path)
    except ValueError as e:
        raise AppError(str(e), status_code=400)
    _audit_tool(user, "tool_file_read", body.project_id, path=body.path)
    return {"project_id": body.project_id, "path": body.path, "content": content}


@router.post("/file/write", dependencies=[_READ_WRITE_DEP])
async def file_write(body: FileWriteRequest, user: User = Depends(get_current_user)):
    """写项目文件（dev/admin）。"""
    ws = get_workspace()
    if not ws.exists(body.project_id):
        raise NotFoundError("项目不存在: %s" % body.project_id)
    # 危险路径检测：禁止写 .git/、CI 配置等敏感路径
    _check_dangerous_path(body.path, user, body.project_id)
    try:
        ws.write_file(body.project_id, body.path, body.content)
    except ValueError as e:
        raise AppError(str(e), status_code=400)
    _audit_tool(
        user, "tool_file_write", body.project_id,
        path=body.path, size=len(body.content), overwrite=body.overwrite,
    )
    return {"project_id": body.project_id, "path": body.path, "size": len(body.content)}


@router.post("/file/list", dependencies=[_READ_WRITE_DEP])
async def file_list(body: FileListRequest, user: User = Depends(get_current_user)):
    """列项目文件树。"""
    ws = get_workspace()
    if not ws.exists(body.project_id):
        raise NotFoundError("项目不存在: %s" % body.project_id)
    entries = ws.list_files(body.project_id)
    if body.subpath:
        # 过滤到指定子路径下
        sub = body.subpath.strip("/").strip("\\")
        entries = [e for e in entries if e["path"].startswith(sub)]
    _audit_tool(user, "tool_file_list", body.project_id, subpath=body.subpath, count=len(entries))
    return {"project_id": body.project_id, "entries": entries, "count": len(entries)}


def _check_dangerous_path(path: str, user: User, project_id: str) -> None:
    """禁止写入敏感路径（.git/hooks/、.github/workflows/、CI/CD 配置等）。"""
    # 只去掉开头的 "/"，保留开头的 "."（如 ".github/"）
    p = path.replace("\\", "/").lower().lstrip("/")
    danger_patterns = (
        ".git/hooks/",
        ".git/config",
        ".git/refs/",
        ".github/workflows/",
        ".gitlab-ci",
        "jenkinsfile",
        ".docker/",
        "docker-compose.",
        ".circleci/",
    )
    for pat in danger_patterns:
        if pat in p:
            raise ForbiddenError(
                "禁止写入 CI/CD 或 git 内部路径: %s（需管理员手动操作）" % path
            )


# ============================================================================
# 全局搜索
# ============================================================================


@router.post("/search", dependencies=[_READ_WRITE_DEP])
async def search(body: SearchRequest, user: User = Depends(get_current_user)):
    """全局搜索：文件名匹配 + 内容 grep。"""
    root = _project_root(body.project_id, user)
    results: List[Dict] = []
    query_lower = body.query.lower()

    try:
        for p in root.rglob("*"):
            if len(results) >= body.max_results:
                break
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            # 跳过 build/、.git/、node_modules/、__pycache__/
            if any(seg in {"build", ".git", "node_modules", "__pycache__", ".venv"}
                   for seg in p.parts):
                continue
            # 文件名匹配
            if body.mode in ("filename", "both") and query_lower in rel.lower():
                results.append({
                    "path": rel, "match": "filename", "line": 0, "preview": "",
                })
                continue
            # 内容搜索
            if body.mode in ("content", "both"):
                try:
                    with open(p, "r", encoding="utf-8", errors="ignore") as f:
                        for ln, line in enumerate(f, 1):
                            if query_lower in line.lower():
                                results.append({
                                    "path": rel,
                                    "match": "content",
                                    "line": ln,
                                    "preview": line.strip()[:200],
                                })
                                if len(results) >= body.max_results:
                                    break
                except (OSError, UnicodeDecodeError):
                    continue
    except OSError as e:
        raise AppError("搜索失败: %s" % e, status_code=500)

    _audit_tool(
        user, "tool_search", body.project_id,
        query=body.query, mode=body.mode, results=len(results),
    )
    return {"project_id": body.project_id, "results": results, "count": len(results)}


# ============================================================================
# Shell（含危险命令检测 + 二次确认）
# ============================================================================


@router.post("/shell", dependencies=[_READ_WRITE_DEP])
async def shell(body: ShellRequest, user: User = Depends(get_current_user)):
    """执行 shell（dev/admin）。

    - readonly 角色在依赖层被 403 拦截（TR-10.2）
    - 危险命令（rm/git push/删库等）：
      - severity=warn → 返回 449 needs_confirm，前端弹确认
      - severity=block → 即便 confirm=true 也 403 拒绝（TR-10.1）
    """
    root = _project_root(body.project_id, user)
    detector = get_danger_detector()
    block, verdict = detector.should_block(body.command, body.confirm)

    # block 级：拒绝
    if block and verdict.dangerous and verdict.severity == "block":
        _audit_tool(
            user, "tool_shell_blocked", body.project_id,
            command=body.command, reason=verdict.reason, severity=verdict.severity,
        )
        raise ForbiddenError(
            "禁止执行的不可逆命令: %s（%s）" % (" ".join(body.command), verdict.reason)
        )

    # warn 级：未确认 → 返回 449 needs_confirm
    if block and verdict.dangerous and verdict.severity == "warn":
        _audit_tool(
            user, "tool_shell_needs_confirm", body.project_id,
            command=body.command, reason=verdict.reason,
        )
        return JSONResponse(
            status_code=449,
            content={
                "needs_confirm": True,
                "command": body.command,
                "reason": verdict.reason,
                "severity": verdict.severity,
                "hint": "请用户确认后用 confirm=true 重新调用",
            },
        )

    # 放行：执行
    sandbox = get_sandbox_runner()
    result = sandbox.run(str(root), body.command, timeout=body.timeout)
    _audit_tool(
        user, "tool_shell_exec", body.project_id,
        command=body.command, returncode=result.returncode,
        duration_ms=result.duration_ms,
        confirmed=body.confirm,
        dangerous=verdict.dangerous,
    )
    return {
        "project_id": body.project_id,
        "command": body.command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "duration_ms": result.duration_ms,
        "ok": result.ok,
        "dangerous": verdict.dangerous,
        "danger_reason": verdict.reason if verdict.dangerous else "",
    }


# ============================================================================
# Git diff
# ============================================================================


@router.post("/git-diff", dependencies=[_READ_WRITE_DEP])
async def git_diff(body: GitDiffRequest, user: User = Depends(get_current_user)):
    """生成 git diff 建议。"""
    root = _project_root(body.project_id, user)
    sandbox = get_sandbox_runner()
    cmd = ["git", "diff"]
    if body.cached:
        cmd.append("--cached")
    if body.ref:
        cmd.append(body.ref)
    result = sandbox.run(str(root), cmd, timeout=30)
    _audit_tool(
        user, "tool_git_diff", body.project_id,
        cached=body.cached, ref=body.ref, returncode=result.returncode,
    )
    return {
        "project_id": body.project_id,
        "returncode": result.returncode,
        "diff": result.stdout,
        "stderr": result.stderr,
        "has_changes": bool(result.stdout.strip()),
    }


# ============================================================================
# 单测
# ============================================================================


@router.post("/test", dependencies=[_READ_WRITE_DEP])
async def run_tests(body: TestRequest, user: User = Depends(get_current_user)):
    """跑单测（pytest / go test / cargo test / npm test 自动识别）。"""
    root = _project_root(body.project_id, user)
    # 简单：按文件存在性决定测试框架
    if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists() or (root / "setup.py").exists():
        cmd = ["python", "-m", "pytest", "-v"] + body.extra_args
        if body.target:
            cmd.append(body.target)
    elif (root / "package.json").exists():
        cmd = ["npm", "test"] + body.extra_args
    elif (root / "Cargo.toml").exists():
        cmd = ["cargo", "test"] + body.extra_args
    elif (root / "go.mod").exists():
        cmd = ["go", "test", "./..."]
    else:
        # 兜底：python -m pytest
        cmd = ["python", "-m", "pytest", "-v"] + body.extra_args
        if body.target:
            cmd.append(body.target)

    sandbox = get_sandbox_runner()
    result = sandbox.run(str(root), cmd, timeout=300)
    _audit_tool(
        user, "tool_test", body.project_id,
        command=cmd, returncode=result.returncode, duration_ms=result.duration_ms,
    )
    return {
        "project_id": body.project_id,
        "command": cmd,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "duration_ms": result.duration_ms,
        "passed": result.ok,
    }


# ============================================================================
# Lint
# ============================================================================


@router.post("/lint", dependencies=[_READ_WRITE_DEP])
async def run_lint(body: LintRequest, user: User = Depends(get_current_user)):
    """跑 lint（pylint / flake8 / cppcheck / clang-tidy）。"""
    root = _project_root(body.project_id, user)
    linter = body.linter
    if linter == "auto":
        # 按 file 扩展名选择
        exts = {Path(f).suffix.lower() for f in body.files}
        if exts & {".cpp", ".cc", ".cxx", ".c", ".h", ".hpp"}:
            linter = "cppcheck"
        elif exts & {".py"}:
            linter = "flake8"
        else:
            linter = "flake8"  # 兜底

    if linter == "pylint":
        cmd = ["python", "-m", "pylint"] + body.files
    elif linter == "flake8":
        cmd = ["python", "-m", "flake8"] + body.files
    elif linter == "cppcheck":
        cmd = ["cppcheck", "--enable=warning,style"] + body.files
    elif linter in ("clang-tidy", "clang_tidy"):
        cmd = ["clang-tidy"] + body.files
    else:
        raise AppError("未知 linter: %s" % linter, status_code=400)

    sandbox = get_sandbox_runner()
    result = sandbox.run(str(root), cmd, timeout=120)
    _audit_tool(
        user, "tool_lint", body.project_id,
        linter=linter, files=body.files, returncode=result.returncode,
    )
    return {
        "project_id": body.project_id,
        "linter": linter,
        "command": cmd,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "ok": result.ok,
    }


# ============================================================================
# 接入说明（不修改 main.py）：
# 在 backend/app/main.py 的 create_app() 中加入：
#
#     from app.api.tools import router as tools_router
#     ...
#     app.include_router(tools_router)
#
# 即可使本路由生效。当前为避免与并行子代理冲突 main.py，未自动 include。
# ============================================================================

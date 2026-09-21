"""Agentic 代码探索器 - grep/glob/read 迭代探索循环。

让 LLM 在生成前用工具迭代探索代码上下文（业界 coding agent 主流做法）：
- 语料 = KB code 通道命中文档 + 工作区既有项目文件（增量场景）
- 工具：grep(正则搜内容) / glob(通配符匹配路径) / read(读文件) / finish(结束并总结)
- 每轮 LLM 输出一个 JSON 动作，执行后把观察喂回下一轮；最多 max_rounds 轮
- 任何异常/解析失败 → 优雅降级（返回已收集结果，绝不阻断管线）

设计原则（与 FallbackGraph 一致）：
- 不引入新基础设施：语料在内存中，工具是纯 Python 实现
- LLM 调用统一走 _common.call_llm（mock 兜底）；mock 返回非 JSON 时视为无效轮，
  连续 2 次无效即结束，保证 mock 模式下管线可跑通
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.agent.nodes import _common  # 共享 LLM 调用/JSON 解析（避免 app.agent 循环导入）

logger = logging.getLogger("agent.explorer")

# 语料规模上限（防止超大工作区拖垮内存/提示词）
_MAX_FILES = 300
_MAX_FILE_BYTES = 200 * 1024          # 单文件 200KB 上限
_MAX_TOTAL_BYTES = 2 * 1024 * 1024    # 语料总量 2MB 上限
# 工具输出上限（喂回 LLM 的观察截断）
_GREP_MAX_HITS = 30
_READ_MAX_CHARS = 4000
_OBS_MAX_CHARS = 2500

# 探索时跳过的目录/文件
_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", "build", "dist",
    ".venv", "venv", ".pytest_cache", ".idea", ".vscode", "htmlcov",
}
_SKIP_EXTS = {
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".png", ".jpg", ".jpeg",
    ".gif", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".zip", ".tar", ".gz",
    ".db", ".sqlite", ".parquet", ".pt", ".onnx", ".safetensors",
}

_SYSTEM = (
    "你是代码探索器。在生成代码前，用工具迭代探索代码库，弄清：已有模块划分、"
    "关键函数/类签名、命名与结构约定、可复用的实现。\n"
    "每轮只输出【一个 JSON 动作】，格式（四选一）：\n"
    '{"tool": "grep", "args": {"pattern": "正则"}}\n'
    '{"tool": "glob", "args": {"pattern": "如 src/*.py 或 **/*.cpp"}}\n'
    '{"tool": "read", "args": {"path": "文件路径"}}\n'
    '{"tool": "finish", "args": {"summary": "50字以内的关键发现总结"}}\n'
    "策略：先 glob 看结构，再 grep 定位符号，read 精读 1-3 个关键文件后 finish。"
    "只输出 JSON，不要解释。"
)


# ============================================================================
# 语料
# ============================================================================


class ExploreCorpus:
    """探索语料：path -> content 的内存文档集，提供 grep/glob/read 三个工具。"""

    def __init__(self, docs: Optional[Dict[str, str]] = None) -> None:
        self._docs: Dict[str, str] = dict(docs or {})

    def __len__(self) -> int:
        return len(self._docs)

    def add(self, path: str, content: str) -> None:
        if path and content and path not in self._docs:
            self._docs[path] = content

    @property
    def paths(self) -> List[str]:
        return sorted(self._docs.keys())

    # ---- 工具实现 ----

    @staticmethod
    def _path_regex(pattern: str):
        """把 glob 通配符转为路径感知正则（`*` 不跨目录，`**` 跨目录）。"""
        out = []
        i = 0
        while i < len(pattern):
            c = pattern[i]
            if c == "*":
                if pattern[i : i + 2] == "**":
                    out.append(".*")
                    i += 2
                    continue
                out.append("[^/]*")
            elif c == "?":
                out.append("[^/]")
            else:
                out.append(re.escape(c))
            i += 1
        return re.compile("^" + "".join(out) + "$")

    def glob(self, pattern: str) -> List[str]:
        """通配符匹配路径（`*` 不跨 `/`，`**` 跨层级；无目录前缀时按 basename 兜底）。"""
        pat = (pattern or "").strip()
        if not pat:
            return []
        rx = self._path_regex(pat)
        hits = [p for p in self._docs if rx.match(p)]
        if not hits and "/" not in pat:
            base_rx = self._path_regex(pat)
            hits = [p for p in self._docs if base_rx.match(p.rsplit("/", 1)[-1])]
        return sorted(hits)[:50]

    def grep(self, pattern: str) -> List[str]:
        """正则搜内容，返回 "path:line: text" 命中列表（截断防炸提示词）。"""
        try:
            rx = re.compile(pattern or "")
        except re.error:
            return []
        hits: List[str] = []
        for path in sorted(self._docs.keys()):
            for i, line in enumerate(self._docs[path].split("\n"), start=1):
                if rx.search(line):
                    text = line.strip()[:200]
                    hits.append("%s:%d: %s" % (path, i, text))
                    if len(hits) >= _GREP_MAX_HITS:
                        hits.append("…（命中过多，已截断到 %d 条）" % _GREP_MAX_HITS)
                        return hits
        return hits

    def read(self, path: str) -> str:
        """读单个文件（截断）。返回文本；不存在返回空串。"""
        content = self._docs.get(path or "", "")
        if not content:
            return ""
        if len(content) > _READ_MAX_CHARS:
            return content[:_READ_MAX_CHARS] + "\n…（截断）"
        return content

    def has(self, path: str) -> bool:
        return path in self._docs


def build_corpus(
    kb_code_hits: List[Dict[str, Any]],
    workspace_root: Optional[Any] = None,
) -> ExploreCorpus:
    """组装探索语料：KB code 命中 + 工作区既有文件。

    Args:
        kb_code_hits: retriever 检索结果（含 filename/content）。
        workspace_root: 项目工作区根 Path（None 或不存在则跳过）。
    """
    corpus = ExploreCorpus()
    total_bytes = 0

    # 1. KB code 通道命中文档
    for i, hit in enumerate(kb_code_hits or []):
        content = hit.get("content") or ""
        if not content:
            continue
        name = hit.get("filename") or (hit.get("meta") or {}).get("filename") or ""
        path = ("kb://%s" % name) if name else ("kb://code/%d" % i)
        corpus.add(path, content)
        total_bytes += len(content)

    # 2. 工作区既有文件（增量生成/修复场景）
    if workspace_root is not None:
        root: Any = workspace_root
        try:
            if root.exists():
                for p in sorted(root.rglob("*")):
                    if len(corpus) >= _MAX_FILES or total_bytes >= _MAX_TOTAL_BYTES:
                        break
                    if not p.is_file():
                        continue
                    rel = p.relative_to(root).as_posix()
                    parts = rel.split("/")
                    if any(seg in _SKIP_DIRS or (seg.startswith(".") and seg != ".project.json") for seg in parts[:-1]):
                        continue
                    name = parts[-1]
                    if name.startswith(".") or p.suffix.lower() in _SKIP_EXTS:
                        continue
                    try:
                        if p.stat().st_size > _MAX_FILE_BYTES:
                            continue
                        content = p.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        continue
                    corpus.add(rel, content)
                    total_bytes += len(content)
        except Exception as e:  # 语料构建失败不阻断
            logger.warning("工作区语料构建失败（跳过）: %s", e)

    return corpus


# ============================================================================
# 迭代探索循环
# ============================================================================


async def run_exploration(
    router: Any,
    user_id: str,
    role: str,
    user_query: str,
    corpus: ExploreCorpus,
    max_rounds: int = 4,
) -> Dict[str, Any]:
    """执行 agentic 探索循环。

    Returns:
        {status: ok|degraded|skipped, rounds, tool_calls, files_read, summary, error}
        绝不抛异常。
    """
    result: Dict[str, Any] = {
        "status": "ok",
        "rounds": 0,
        "tool_calls": [],
        "files_read": [],
        "summary": "",
        "error": "",
    }
    if len(corpus) == 0:
        result["status"] = "skipped"
        result["error"] = "语料为空（无 KB 命中且工作区为空）"
        return result

    history: List[str] = []   # 已发生的动作+观察（截断后）
    invalid_rounds = 0

    for round_i in range(1, max_rounds + 1):
        result["rounds"] = round_i
        user_prompt = (
            "用户需求：%s\n"
            "语料文件列表（共 %d 个）：\n%s\n"
            "历史动作与观察：\n%s\n"
            "请输出下一个 JSON 动作。"
        ) % (
            user_query[:1500],
            len(corpus),
            "\n".join(corpus.paths[:80]) or "(空)",
            "\n".join(history[-6:]) or "(无，这是第一轮)",
        )
        try:
            raw = await _common.call_llm(
                router, user_id, role, _SYSTEM, user_prompt, max_tokens=512
            )
        except Exception as e:
            result["status"] = "degraded"
            result["error"] = "LLM 调用失败: %s" % e
            return result

        action = _common.parse_json_safely(raw or "")
        if not isinstance(action, dict) or "tool" not in action:
            invalid_rounds += 1
            history.append("[第%d轮] 无效输出（忽略）" % round_i)
            if invalid_rounds >= 2:
                result["status"] = "degraded"
                result["error"] = "连续无效输出（mock 或异常模型）"
                return result
            continue

        tool = str(action.get("tool", "")).lower()
        args = action.get("args") or {}
        if not isinstance(args, dict):
            args = {}

        if tool == "finish":
            result["summary"] = str(args.get("summary", ""))[:300]
            history.append("[第%d轮] finish: %s" % (round_i, result["summary"]))
            break

        observation, ok = _execute_tool(corpus, tool, args)
        result["tool_calls"].append({"round": round_i, "tool": tool, "args": args, "ok": ok})
        if tool == "read" and ok:
            path = str(args.get("path", ""))
            if path and path not in result["files_read"]:
                result["files_read"].append(path)
        history.append("[第%d轮] %s %s => %s" % (round_i, tool, args, observation[:_OBS_MAX_CHARS]))
    else:
        # 轮次耗尽，用已有信息收尾
        result["summary"] = "（轮次耗尽）已探索 %d 个文件" % len(result["files_read"])

    return result


def _execute_tool(corpus: ExploreCorpus, tool: str, args: Dict[str, Any]) -> Tuple[str, bool]:
    """执行单个工具，返回 (观察文本, 是否有效)。"""
    if tool == "grep":
        pattern = str(args.get("pattern", ""))
        hits = corpus.grep(pattern)
        if not hits:
            return "(grep 无命中) pattern=%s" % pattern, False
        return "\n".join(hits), True
    if tool == "glob":
        pattern = str(args.get("pattern", ""))
        hits = corpus.glob(pattern)
        if not hits:
            return "(glob 无命中) pattern=%s" % pattern, False
        return "\n".join(hits), True
    if tool == "read":
        path = str(args.get("path", ""))
        content = corpus.read(path)
        if not content:
            return "(read 失败，文件不存在或为空) path=%s；可用文件：%s" % (
                path, ", ".join(corpus.paths[:20])), False
        return content, True
    return "(未知工具 %s；可用：grep/glob/read/finish)" % tool, False

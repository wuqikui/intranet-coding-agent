"""工程循环 - 真实编译 + 错误解析 + 自动修复（Task 8）。

核心组件：
- CompileError: 编译错误数据类
- CompileErrorParser: 解析 gcc/clang/cmake/linker 错误输出
- AutoFixer: 基于错误模式的规则化修复（include 缺失、std:: 前缀、分号缺失、
  现代 CMake 缺 PUBLIC/PRIVATE 等）+ 知识库"C/C++ 缺陷范式"规则扫描兜底
- BuildLoop: 跑 N 轮 cmake -B build → cmake --build build → 解析 → 修复 → 重编译
  N 由 settings.MAX_FIX_ROUNDS 控制（≥5）

设计原则：
- 编译器不可用（tooling_missing）→ 不视为代码失败，返回 tooling_missing 状态
- 修复只针对常见且安全的模式；无法修复的累积到 remaining_errors
- C/C++ 现代 CMake（PUBLIC/PRIVATE）+ RAII + 禁 UB 规则扫描
- clang-tidy / cppcheck 可用时优先启用；不可用时走规则扫描
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import get_settings
from app.sandbox.runner import CommandResult, SandboxRunner

logger = logging.getLogger("agent.build_loop")


# ============================================================================
# 数据结构
# ============================================================================


@dataclass
class CompileError:
    """单个编译/链接/lint 错误。"""

    file: str = ""
    line: int = 0
    column: int = 0
    severity: str = "error"   # error | warning | note | linker | cmake | lint
    message: str = ""
    code: str = ""            # 错误码（如 [-Wformat] / undefined reference）
    source: str = "gcc"       # gcc | clang | cmake | linker | lint | rule

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "severity": self.severity,
            "message": self.message,
            "code": self.code,
            "source": self.source,
        }


@dataclass
class FixAction:
    """单次修复动作。"""

    file: str
    description: str
    applied: bool = False
    diff: str = ""


@dataclass
class BuildResult:
    """BuildLoop 最终结果。"""

    status: str = "unknown"     # passed | failed | tooling_missing | no_project
    rounds: int = 0
    build_log: str = ""
    errors: List[CompileError] = field(default_factory=list)
    fixes: List[FixAction] = field(default_factory=list)
    project_type: str = ""
    artifacts: List[str] = field(default_factory=list)
    linter_findings: List[CompileError] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "rounds": self.rounds,
            "errors": [e.to_dict() for e in self.errors],
            "fixes": [
                {"file": f.file, "description": f.description, "applied": f.applied}
                for f in self.fixes
            ],
            "project_type": self.project_type,
            "artifacts": self.artifacts,
            "linter_findings": [e.to_dict() for e in self.linter_findings],
            "build_log_excerpt": self.build_log[-2000:],
        }


# ============================================================================
# 错误解析器
# ============================================================================


# gcc/clang: file:line:col: severity: message [code]
_GCC_ERR_RE = re.compile(
    r"^(?P<file>[^\s:]+):(?P<line>\d+)(?::(?P<col>\d+))?:\s*"
    r"(?P<severity>error|warning|note|fatal error):\s*"
    r"(?P<message>.*?)(?:\s+\[(?P<code>[^\]]+)\])?\s*$"
)

# cmake: CMake Error at <file> (<line>):  message
_CMAKE_ERR_RE = re.compile(
    r"^CMake Error at (?P<file>[^\s]+)\s*\((?P<line>\d+)\):\s*(?P<message>.*)$"
)
_CMAKE_WARN_RE = re.compile(
    r"^CMake Warning at (?P<file>[^\s]+)\s*\((?P<line>\d+)\):\s*(?P<message>.*)$"
)

# linker: undefined reference to `symbol'
_LINKER_UNDEF_RE = re.compile(
    r"(?P<type>undefined reference|multiple definition|cannot find)[^\n]*"
    r"(?:to\s+)['\"]?(?P<symbol>[^\s'\"]+)['\"]?"
)

# Microsoft MSVC: file(line): error Cxxxx: message
_MSVC_RE = re.compile(
    r"^(?P<file>[^(]+)\((?P<line>\d+)\):\s*(?P<severity>error|warning)\s+"
    r"(?P<code>C\d+):\s*(?P<message>.*)$"
)


class CompileErrorParser:
    """解析编译器/链接器/cmake 输出为结构化错误列表。"""

    def parse(self, output: str) -> List[CompileError]:
        if not output:
            return []
        errors: List[CompileError] = []
        # 处理 \r\n / \r
        text = output.replace("\r\n", "\n").replace("\r", "\n")
        for raw_line in text.split("\n"):
            line = raw_line.rstrip()
            if not line:
                continue
            err = self._parse_line(line)
            if err:
                errors.append(err)
        return errors

    def _parse_line(self, line: str) -> Optional[CompileError]:
        # gcc/clang 优先（最常见）
        m = _GCC_ERR_RE.match(line)
        if m:
            return CompileError(
                file=m.group("file"),
                line=int(m.group("line") or 0),
                column=int(m.group("col") or 0),
                severity=m.group("severity"),
                message=m.group("message"),
                code=m.group("code") or "",
                source="gcc",
            )
        # cmake
        m = _CMAKE_ERR_RE.match(line)
        if m:
            return CompileError(
                file=m.group("file"),
                line=int(m.group("line") or 0),
                severity="cmake",
                message=m.group("message"),
                source="cmake",
            )
        m = _CMAKE_WARN_RE.match(line)
        if m:
            return CompileError(
                file=m.group("file"),
                line=int(m.group("line") or 0),
                severity="warning",
                message=m.group("message"),
                source="cmake",
            )
        # MSVC
        m = _MSVC_RE.match(line)
        if m:
            return CompileError(
                file=m.group("file"),
                line=int(m.group("line") or 0),
                severity=m.group("severity"),
                message=m.group("message"),
                code=m.group("code"),
                source="msvc",
            )
        # linker
        m = _LINKER_UNDEF_RE.search(line)
        if m:
            return CompileError(
                file="",
                line=0,
                severity="linker",
                message=line.strip(),
                code="undefined:%s" % m.group("symbol"),
                source="linker",
            )
        # 兜底：错误关键词
        low = line.lower()
        if "error:" in low or "fatal:" in low or "undefined" in low:
            return CompileError(
                severity="error",
                message=line.strip(),
                source="generic",
            )
        return None


# ============================================================================
# 自动修复器
# ============================================================================


# 知名 std 符号 → 推荐 include（保守映射，覆盖最常见的）
_STD_SYMBOL_INCLUDES: Dict[str, str] = {
    "std::cout": "<iostream>",
    "std::cin": "<iostream>",
    "std::endl": "<iostream>",
    "std::string": "<string>",
    "std::vector": "<vector>",
    "std::map": "<map>",
    "std::unordered_map": "<unordered_map>",
    "std::set": "<set>",
    "std::unordered_set": "<unordered_set>",
    "std::shared_ptr": "<memory>",
    "std::unique_ptr": "<memory>",
    "std::make_shared": "<memory>",
    "std::make_unique": "<memory>",
    "std::function": "<functional>",
    "std::runtime_error": "<stdexcept>",
    "std::invalid_argument": "<stdexcept>",
    "std::out_of_range": "<stdexcept>",
    "std::to_string": "<string>",
    "std::stoi": "<string>",
    "std::sort": "<algorithm>",
    "std::find": "<algorithm>",
    "std::begin": "<iterator>",
    "std::end": "<iterator>",
    "std::thread": "<thread>",
    "std::mutex": "<mutex>",
    "std::lock_guard": "<mutex>",
    "std::size_t": "<cstddef>",
    "std::int32_t": "<cstdint>",
    "std::uint32_t": "<cstdint>",
    "std::int64_t": "<cstdint>",
    "std::uint64_t": "<cstdint>",
}


class AutoFixer:
    """基于错误模式的规则化自动修复。"""

    def __init__(self, project_root: Path) -> None:
        self.root = project_root

    def fix_errors(self, errors: List[CompileError]) -> List[FixAction]:
        """对一组错误做修复，返回已应用的 FixAction 列表。"""
        actions: List[FixAction] = []
        # 按 file 分组
        by_file: Dict[str, List[CompileError]] = {}
        for e in errors:
            if e.severity not in ("error", "fatal error", "linker", "cmake"):
                continue
            if not e.file:
                continue
            by_file.setdefault(e.file, []).append(e)

        for rel_path, file_errors in by_file.items():
            try:
                actions.extend(self._fix_file(rel_path, file_errors))
            except Exception as ex:
                logger.warning("修复文件 %s 失败: %s", rel_path, ex)
                actions.append(
                    FixAction(
                        file=rel_path,
                        description="修复失败（异常）: %s" % ex,
                        applied=False,
                    )
                )
        return actions

    def _fix_file(self, rel_path: str, errors: List[CompileError]) -> List[FixAction]:
        """修复单个文件，返回 FixAction 列表。"""
        abs_path = self._resolve_path(rel_path)
        if abs_path is None or not abs_path.exists():
            return [FixAction(file=rel_path, description="文件未找到，跳过", applied=False)]
        try:
            original = abs_path.read_text(encoding="utf-8")
        except Exception as e:
            return [FixAction(file=rel_path, description="读取失败: %s" % e, applied=False)]

        actions: List[FixAction] = []
        new_content = original
        for err in errors:
            new_content, applied, desc = self._apply_one(new_content, err, rel_path)
            if applied:
                actions.append(FixAction(file=rel_path, description=desc, applied=True))

        if new_content != original:
            try:
                abs_path.write_text(new_content, encoding="utf-8")
            except Exception as e:
                logger.warning("写回 %s 失败: %s", rel_path, e)
                # 把已应用改为未应用
                for a in actions:
                    a.applied = False
        return actions

    def _apply_one(self, content: str, err: CompileError, rel_path: str) -> Tuple[str, bool, str]:
        """尝试单个错误的修复规则。返回 (新内容, 是否应用, 描述)。"""
        msg = err.message or ""

        # 规则 1：缺 include（"was not declared in this scope" + std 符号）
        m = re.search(r"'(std::\w+)' was not declared", msg)
        if m:
            sym = m.group(1)
            inc = _STD_SYMBOL_INCLUDES.get(sym)
            if inc and inc not in content:
                new_content = self._ensure_include(content, inc)
                return new_content, True, "加 #include %s（缺 %s 声明）" % (inc, sym)

        # 规则 2：未加 std:: 前缀（"did not appear" / "was not declared"）
        # 模式：'vector' was not declared in this scope → 期望 std::vector
        m = re.search(r"'(\w+)' was not declared in this scope", msg)
        if m:
            sym = m.group(1)
            full = "std::" + sym
            inc = _STD_SYMBOL_INCLUDES.get(full)
            # 只有在已知 std 符号时才加前缀
            if inc:
                # 给内容里出现的裸 sym 加 std:: 前缀（保守：仅在 word boundary）
                new_content = re.sub(
                    r"(?<!std::|[\w:])\b%s\b(?!\s*:)" % re.escape(sym),
                    "std::" + sym,
                    content,
                )
                if new_content != content:
                    if inc not in new_content:
                        new_content = self._ensure_include(new_content, inc)
                    return new_content, True, "为 %s 加 std:: 前缀 + #include %s" % (sym, inc)

        # 规则 3：分号缺失（"expected ';' before"）
        m = re.search(r"expected ';' before '([^']+)'", msg)
        if m and err.line > 0:
            lines = content.split("\n")
            # err.line 是 1-based
            idx = err.line - 1
            if 0 <= idx < len(lines):
                # 简单：在行尾加 ;
                if not lines[idx].rstrip().endswith(";"):
                    lines[idx] = lines[idx].rstrip() + ";"
                    new_content = "\n".join(lines)
                    return new_content, True, "在第 %d 行末尾补分号" % err.line

        # 规则 4：未声明 assert / assert.h
        if "assert" in msg.lower() and "was not declared" in msg:
            if "<cassert>" not in content and "<assert.h>" not in content:
                new_content = self._ensure_include(content, "<cassert>")
                return new_content, True, "加 #include <cassert>"

        # 规则 5：未声明 printf / scanf
        if ("printf" in msg or "scanf" in msg) and "was not declared" in msg:
            if "<cstdio>" not in content and "<stdio.h>" not in content:
                new_content = self._ensure_include(content, "<cstdio>")
                return new_content, True, "加 #include <cstdio>"

        # 规则 6：未声明 memcpy / memset
        if ("memcpy" in msg or "memset" in msg) and "was not declared" in msg:
            if "<cstring>" not in content and "<string.h>" not in content:
                new_content = self._ensure_include(content, "<cstring>")
                return new_content, True, "加 #include <cstring>"

        # 规则 7：未声明 strlen
        if "strlen" in msg and "was not declared" in msg:
            if "<cstring>" not in content and "<string.h>" not in content:
                new_content = self._ensure_include(content, "<cstring>")
                return new_content, True, "加 #include <cstring>"

        return content, False, ""

    @staticmethod
    def _ensure_include(content: str, header: str) -> str:
        """在已有 #include 区域追加 header（避免重复）。"""
        if header in content:
            return content
        # 找最后一个 #include 行
        lines = content.split("\n")
        last_inc = -1
        for i, ln in enumerate(lines):
            if ln.strip().startswith("#include"):
                last_inc = i
        # 找 #pragma once / 头部守卫
        if last_inc >= 0:
            lines.insert(last_inc + 1, "#include %s" % header)
        else:
            # 插到文件开头
            insert_at = 0
            # 跳过文件头注释
            while insert_at < len(lines) and (
                lines[insert_at].strip().startswith(("#", "//", "/*", "*"))
                or not lines[insert_at].strip()
            ):
                if lines[insert_at].strip().startswith("#include"):
                    break
                insert_at += 1
            lines.insert(insert_at, "#include %s" % header)
            lines.insert(insert_at + 1, "")
        return "\n".join(lines)

    def _resolve_path(self, rel_path: str) -> Optional[Path]:
        """把错误里的 file 路径解析到项目根下。"""
        try:
            # 可能是绝对路径 / 相对路径 / 带 build/ 前缀
            p = Path(rel_path)
            if p.is_absolute():
                # 看是否在项目根下
                try:
                    rel = p.relative_to(self.root.resolve())
                    return self.root / rel
                except ValueError:
                    pass
            # 去掉 build/ 前缀
            rel_norm = rel_path.replace("\\", "/").lstrip("./")
            for prefix in ("build/", "../build/", "./build/"):
                if rel_norm.startswith(prefix):
                    rel_norm = rel_norm[len(prefix):]
                    break
            return (self.root / rel_norm).resolve()
        except Exception:
            return None


# ============================================================================
# 现代 CMake / RAII / UB 规则扫描器
# ============================================================================


class CCPlusRuleScanner:
    """C/C++ 现代 CMake + RAII + 禁 UB 规则扫描（无 clang-tidy/cppcheck 时启用）。

    返回 CompileError 列表（severity=lint），不直接修改文件，由 AutoFixer 决定是否可修复。
    """

    # 模式：target_link_libraries(foo bar) → 缺 PUBLIC/PRIVATE
    _MISSING_VIS_RE = re.compile(
        r"target_link_libraries\s*\(\s*(\w+)\s+([^)\s]+)\s*\)",
        re.MULTILINE,
    )

    # 模式：裸 new / delete
    _RAW_NEW_RE = re.compile(r"\bnew\s+([A-Za-z_]\w*)\s*\(")
    _RAW_DELETE_RE = re.compile(r"\bdelete\s+([^;{]+);")

    # 模式：未初始化的裸指针（简化：Type* name; 单独成行不初始化）
    _RAW_PTR_DECL_RE = re.compile(
        r"^\s*(?:const\s+)?([A-Za-z_]\w*)\s*\*\s*(\w+)\s*;\s*$",
        re.MULTILINE,
    )

    def scan(self, root: Path, files: List[str]) -> List[CompileError]:
        findings: List[CompileError] = []
        for rel in files:
            p = (root / rel).resolve()
            if not p.exists():
                continue
            rel_lower = rel.lower()
            is_cmake = rel_lower == "cmakelists.txt" or rel_lower.endswith("/cmakelists.txt")
            is_cpp = p.suffix.lower() in (".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh")
            if not (is_cmake or is_cpp):
                continue
            try:
                content = p.read_text(encoding="utf-8")
            except Exception:
                continue
            if is_cmake:
                findings.extend(self._scan_cmake(rel, content))
            else:
                findings.extend(self._scan_cpp(rel, content))
        return findings

    def _scan_cmake(self, rel: str, content: str) -> List[CompileError]:
        out: List[CompileError] = []
        for m in self._MISSING_VIS_RE.finditer(content):
            out.append(CompileError(
                file=rel,
                line=content.count("\n", 0, m.start()) + 1,
                severity="lint",
                message="target_link_libraries 缺少 PUBLIC/PRIVATE 可见性说明符",
                code="cmake-missing-visibility",
                source="rule",
            ))
        return out

    def _scan_cpp(self, rel: str, content: str) -> List[CompileError]:
        out: List[CompileError] = []
        # 裸 new / delete
        for m in self._RAW_NEW_RE.finditer(content):
            out.append(CompileError(
                file=rel,
                line=content.count("\n", 0, m.start()) + 1,
                severity="warning",
                message="使用裸 new %s(...)，建议 std::make_unique / std::make_shared（RAII）" % m.group(1),
                code="raw-new",
                source="rule",
            ))
        for m in self._RAW_DELETE_RE.finditer(content):
            out.append(CompileError(
                file=rel,
                line=content.count("\n", 0, m.start()) + 1,
                severity="warning",
                message="使用裸 delete，建议智能指针自动管理",
                code="raw-delete",
                source="rule",
            ))
        # 检查未定义行为模式：未初始化变量（保守，仅"T* p;"形式）
        for m in self._RAW_PTR_DECL_RE.finditer(content):
            var = m.group(2)
            out.append(CompileError(
                file=rel,
                line=content.count("\n", 0, m.start()) + 1,
                severity="warning",
                message="裸指针 %s 未初始化（建议 = nullptr 或智能指针）" % var,
                code="raw-ptr-uninitialized",
                source="rule",
            ))
        return out


# ============================================================================
# BuildLoop 主循环
# ============================================================================


class BuildLoop:
    """工程循环：每批调用真实 cmake/make/g++ 编译 + 错误解析 + 自动修复。

    最多 N 轮（settings.MAX_FIX_ROUNDS，要求 ≥5）。
    """

    def __init__(
        self,
        sandbox: Optional[SandboxRunner] = None,
        max_rounds: Optional[int] = None,
    ) -> None:
        self.sandbox = sandbox or SandboxRunner()
        settings = get_settings()
        self.max_rounds = max_rounds if max_rounds is not None else settings.MAX_FIX_ROUNDS
        if self.max_rounds < 5:
            # 强制下限：规格要求 N≥5
            self.max_rounds = 5
        self.parser = CompileErrorParser()
        self.scanner = CCPlusRuleScanner()

    async def run(self, project_root: Path, files: List[str]) -> BuildResult:
        """对项目根下的一组文件跑工程循环。"""
        result = BuildResult()
        log_lines: List[str] = []

        project_type = self._detect_project_type(project_root, files)
        result.project_type = project_type
        log_lines.append("==== 工程循环开始 ====")
        log_lines.append("项目根: %s" % project_root)
        log_lines.append("项目类型: %s" % project_type)
        log_lines.append("最大修复轮次 N=%d" % self.max_rounds)

        if project_type == "unknown":
            log_lines.append("无法识别项目类型，跳过编译")
            result.status = "no_project"
            result.build_log = "\n".join(log_lines) + "\n"
            return result

        # 检测编译器是否可用
        tool_check = self._check_toolchain(project_root, project_type)
        log_lines.append("工具链检查: %s" % tool_check.get("summary", ""))
        if not tool_check["available"]:
            log_lines.append("编译器不可用，标记 tooling_missing（部署机具备工具链时自动启用真实编译）")
            result.status = "tooling_missing"
            result.build_log = "\n".join(log_lines) + "\n"
            # 仍跑规则扫描，给出 lint 结果
            lint = self.scanner.scan(project_root, files)
            result.linter_findings = lint
            log_lines.append("规则扫描结果: %d 条 lint" % len(lint))
            return result

        # 主循环
        for round_i in range(1, self.max_rounds + 1):
            result.rounds = round_i
            log_lines.append("\n---- Round %d ----" % round_i)
            compile_out = self._compile(project_root, project_type)
            log_lines.append("$ %s" % " ".join(compile_out.command))
            log_lines.append(compile_out.stdout or "")
            if compile_out.stderr:
                log_lines.append("[stderr] " + compile_out.stderr)

            if compile_out.tool_missing:
                log_lines.append("工具突然不可用，停止")
                result.status = "tooling_missing"
                break

            if compile_out.ok:
                log_lines.append("编译通过 ✓")
                result.status = "passed"
                # 跑 linter（clang-tidy / cppcheck 或规则扫描）
                lint = self._run_linters(project_root, files)
                result.linter_findings = lint
                log_lines.append("Lint 检查: %d 条发现" % len(lint))
                # 收集产物
                result.artifacts = self._collect_artifacts(project_root, project_type)
                break

            # 解析错误
            errors = self.parser.parse(compile_out.combined_output())
            result.errors = errors
            log_lines.append("解析到 %d 条错误" % len(errors))

            if not errors:
                log_lines.append("无可解析错误，停止修复")
                result.status = "failed"
                break

            # 自动修复
            fixer = AutoFixer(project_root)
            actions = fixer.fix_errors(errors)
            result.fixes.extend(actions)
            applied_count = sum(1 for a in actions if a.applied)
            log_lines.append("修复尝试: %d 个，应用 %d 个" % (len(actions), applied_count))
            for a in actions:
                log_lines.append("  - %s: %s" % (a.file, a.description))

            if applied_count == 0:
                log_lines.append("无可应用修复，停止（剩余错误需 LLM 修复或人工介入）")
                result.status = "failed"
                break

            # 下一轮重编译
            log_lines.append("进入下一轮编译…")

        if result.status == "unknown":
            result.status = "failed"

        result.build_log = "\n".join(log_lines) + "\n"
        return result

    # ---- 工具链检测 ----

    def _check_toolchain(self, project_root: Path, project_type: str) -> Dict[str, Any]:
        """检测项目类型所需的编译器是否可用。"""
        summary_lines: List[str] = []
        all_available = True
        # C/C++ 必需 cmake + g++/clang++
        if project_type in ("cmake", "cpp", "cpp_source"):
            r_cmake = self.sandbox.run(str(project_root), ["cmake", "--version"], timeout=15)
            r_cpp = self.sandbox.run(
                str(project_root),
                ["g++", "--version"] if os_name_nt() else ["g++", "--version"],
                timeout=15,
            )
            cmake_ok = r_cmake.ok or "cmake version" in (r_cmake.stdout + r_cmake.stderr).lower()
            cpp_ok = r_cpp.ok or "g++" in (r_cpp.stdout + r_cpp.stderr).lower()
            summary_lines.append(
                "cmake=%s g++=%s" % ("ok" if cmake_ok else "missing", "ok" if cpp_ok else "missing")
            )
            all_available = cmake_ok and cpp_ok
        elif project_type == "make":
            r_make = self.sandbox.run(str(project_root), ["make", "--version"], timeout=15)
            make_ok = r_make.ok or "gnu make" in (r_make.stdout + r_make.stderr).lower()
            summary_lines.append("make=%s" % ("ok" if make_ok else "missing"))
            all_available = make_ok
        elif project_type == "python":
            r_py = self.sandbox.run(str(project_root), ["python", "--version"], timeout=15)
            py_ok = r_py.ok or "python" in (r_py.stdout + r_py.stderr).lower()
            summary_lines.append("python=%s" % ("ok" if py_ok else "missing"))
            all_available = py_ok
        elif project_type == "node":
            r_npm = self.sandbox.run(str(project_root), ["npm", "--version"], timeout=15)
            npm_ok = r_npm.ok
            summary_lines.append("npm=%s" % ("ok" if npm_ok else "missing"))
            all_available = npm_ok
        else:
            summary_lines.append("未实现工具链检测（默认可用）")

        return {
            "available": all_available,
            "summary": "; ".join(summary_lines),
        }

    # ---- 编译 ----

    def _compile(self, project_root: Path, project_type: str) -> CommandResult:
        """按项目类型执行编译命令。"""
        if project_type in ("cmake", "cpp", "cpp_source"):
            # 先 cmake -B build，再 cmake --build build
            r1 = self.sandbox.run(
                str(project_root),
                ["cmake", "-B", "build", "-S", "."],
                timeout=60,
            )
            if not r1.ok:
                return r1
            return self.sandbox.run(
                str(project_root),
                ["cmake", "--build", "build"],
                timeout=120,
            )
        if project_type == "make":
            return self.sandbox.run(str(project_root), ["make"], timeout=120)
        if project_type == "python":
            # Python：跑 python -m py_compile 编译所有 .py
            # 找出所有 .py
            py_files = [str(p.relative_to(project_root)) for p in project_root.rglob("*.py")]
            if not py_files:
                return CommandResult(
                    command=["python", "-m", "py_compile"],
                    returncode=0,
                    stdout="(no .py files)",
                    stderr="",
                    duration_ms=0,
                    mode=self.sandbox.mode,
                )
            return self.sandbox.run(
                str(project_root),
                ["python", "-m", "py_compile"] + py_files,
                timeout=60,
            )
        if project_type == "node":
            return self.sandbox.run(str(project_root), ["npm", "install"], timeout=180)
        # 其它类型：返回成功（占位）
        return CommandResult(
            command=["<noop>"],
            returncode=0,
            stdout="(no compile step for %s)" % project_type,
            stderr="",
            duration_ms=0,
            mode=self.sandbox.mode,
        )

    # ---- Linter ----

    def _run_linters(self, project_root: Path, files: List[str]) -> List[CompileError]:
        """优先 clang-tidy / cppcheck；不可用时跑规则扫描。"""
        findings: List[CompileError] = []
        cpp_files = [
            f for f in files
            if f.lower().endswith((".cpp", ".cc", ".cxx", ".c", ".h", ".hpp"))
        ]
        # clang-tidy
        if cpp_files:
            r = self.sandbox.run(
                str(project_root),
                ["clang-tidy", "--version"],
                timeout=15,
            )
            if r.ok or "clang-tidy" in (r.stdout + r.stderr).lower():
                for f in cpp_files:
                    rr = self.sandbox.run(
                        str(project_root),
                        ["clang-tidy", f, "--"],
                        timeout=60,
                    )
                    findings.extend(self.parser.parse(rr.combined_output()))
                if findings:
                    return findings
        # cppcheck
        r = self.sandbox.run(str(project_root), ["cppcheck", "--version"], timeout=15)
        if r.ok or "cppcheck" in (r.stdout + r.stderr).lower():
            for f in cpp_files:
                rr = self.sandbox.run(
                    str(project_root),
                    ["cppcheck", "--enable=warning,style", f],
                    timeout=60,
                )
                findings.extend(self.parser.parse(rr.combined_output()))
            if findings:
                return findings
        # 兜底：规则扫描
        return self.scanner.scan(project_root, files)

    # ---- 项目类型检测 ----

    def _detect_project_type(self, root: Path, files: List[str]) -> str:
        """根据文件清单判断项目类型。"""
        names = {Path(f).name.lower() for f in files}
        if "cmakelists.txt" in names:
            return "cmake"
        if "makefile" in names:
            return "make"
        if "package.json" in names:
            return "node"
        if "cargo.toml" in names:
            return "rust"
        if "go.mod" in names:
            return "go"
        if "pom.xml" in names or any(f.endswith(".java") for f in files):
            return "java"
        if "pyproject.toml" in names or "setup.py" in names or "requirements.txt" in names:
            return "python"
        if any(f.endswith(".py") for f in files):
            return "python"
        if any(f.lower().endswith((".cpp", ".cc", ".cxx", ".c")) for f in files):
            return "cpp_source"
        # 兜底：扫项目根下实际文件
        try:
            for p in root.iterdir():
                if p.is_file() and p.name.lower() == "cmakelists.txt":
                    return "cmake"
                if p.is_file() and p.name.lower() == "makefile":
                    return "make"
                if p.is_file() and p.name.lower() == "package.json":
                    return "node"
        except Exception:
            pass
        return "unknown"

    def _collect_artifacts(self, root: Path, project_type: str) -> List[str]:
        """收集构建产物路径。"""
        artifacts: List[str] = []
        build_dir = root / "build"
        if build_dir.exists():
            try:
                for p in sorted(build_dir.rglob("*")):
                    if p.is_file():
                        rel = p.relative_to(root).as_posix()
                        artifacts.append(rel)
            except Exception:
                pass
        return artifacts


def os_name_nt() -> bool:
    """避免在模块顶层 import os 多次。"""
    import os
    return os.name == "nt"


# ---- 单例 ----

_build_loop: Optional[BuildLoop] = None


def get_build_loop() -> BuildLoop:
    """获取 BuildLoop 单例。"""
    global _build_loop
    if _build_loop is None:
        _build_loop = BuildLoop()
    return _build_loop


def reset_build_loop_for_test() -> None:
    """测试用：重置单例。"""
    global _build_loop
    _build_loop = None

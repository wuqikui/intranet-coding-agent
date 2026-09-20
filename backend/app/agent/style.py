"""代码风格保真模块 - Task 9。

提供：
- CodeStyleProfile: 既有代码风格画像
- detect_style(content, language): 从一段代码推断风格
- check_consistency(content, profile, language): 一致性检查
- auto_rewrite(content, profile, language): 按既有风格最小化重写

设计原则：
- 生成前检索知识库"既有代码风格"（缩进/命名/注释语言/模块化）→ 填 state.code_style
- 生成后一致性检查，不符则自动重写
- 禁止擅自套用预设风格；知识库无数据时回退到语言社区默认（PEP8/Google C++ Style 等）

应用：retriever 节点用 detect_style 抽取 code 通道风格；writer 节点对生成
文件做 check_consistency + auto_rewrite。
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("agent.style")


# ============================================================================
# 风格画像
# ============================================================================


@dataclass
class CodeStyleProfile:
    """既有代码风格画像。"""

    indent: Any = 4              # int（空格数） 或 "tab"
    naming: str = "snake_case"  # snake_case | camelCase | PascalCase | kebab-case
    comment_language: str = "中文"  # 中文 | English
    max_line_length: int = 100
    brace_style: str = "same_line"  # same_line | next_line (K&R / Allman)
    trailing_semicolon: bool = True  # C/C++ 行尾分号
    module_docstring: bool = False  # 是否有模块级 docstring
    sample_count: int = 0  # 抽样文件数

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CodeStyleProfile":
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid})


# 语言默认风格（社区规范）
_LANGUAGE_DEFAULTS: Dict[str, CodeStyleProfile] = {
    "python": CodeStyleProfile(
        indent=4, naming="snake_case", comment_language="中文",
        max_line_length=100, brace_style="same_line",
        trailing_semicolon=False, module_docstring=True,
    ),
    "cpp": CodeStyleProfile(
        indent=4, naming="snake_case", comment_language="中文",
        max_line_length=100, brace_style="same_line",
        trailing_semicolon=True, module_docstring=False,
    ),
    "ts": CodeStyleProfile(
        indent=2, naming="camelCase", comment_language="中文",
        max_line_length=120, brace_style="same_line",
        trailing_semicolon=True, module_docstring=False,
    ),
    "javascript": CodeStyleProfile(
        indent=2, naming="camelCase", comment_language="中文",
        max_line_length=120, brace_style="same_line",
        trailing_semicolon=True, module_docstring=False,
    ),
    "go": CodeStyleProfile(
        indent=4, naming="camelCase", comment_language="中文",
        max_line_length=120, brace_style="same_line",
        trailing_semicolon=True, module_docstring=False,
    ),
    "rust": CodeStyleProfile(
        indent=4, naming="snake_case", comment_language="中文",
        max_line_length=100, brace_style="same_line",
        trailing_semicolon=True, module_docstring=False,
    ),
    "java": CodeStyleProfile(
        indent=4, naming="camelCase", comment_language="中文",
        max_line_length=120, brace_style="next_line",
        trailing_semicolon=True, module_docstring=False,
    ),
}


def get_language_default(language: str) -> CodeStyleProfile:
    """语言社区默认风格。未知语言回退 python。"""
    return _LANGUAGE_DEFAULTS.get((language or "").lower(), _LANGUAGE_DEFAULTS["python"])


# ============================================================================
# 风格检测
# ============================================================================


def detect_style(content: str, language: str = "") -> CodeStyleProfile:
    """从一段代码推断其风格画像。

    Args:
        content: 代码内容
        language: 主语言（用于 fallback）

    Returns:
        CodeStyleProfile
    """
    profile = CodeStyleProfile()
    profile.sample_count = 1
    if not content:
        return profile

    lines = content.splitlines()
    # 缩进
    profile.indent = _detect_indent(lines)
    # 命名
    profile.naming = _detect_naming(content, language)
    # 注释语言
    profile.comment_language = _detect_comment_language(content)
    # 最大行长
    profile.max_line_length = _detect_max_line_length(lines)
    # 大括号风格（仅 C/C++/Java/JS/TS/Rust/Go）
    if language in ("cpp", "java", "javascript", "ts", "rust", "go"):
        profile.brace_style = _detect_brace_style(lines)
    # 行尾分号
    profile.trailing_semicolon = _detect_trailing_semicolon(lines, language)
    # 模块 docstring（Python）
    if language == "python":
        profile.module_docstring = _detect_module_docstring(lines)
    return profile


def merge_styles(samples: List[CodeStyleProfile], language: str = "") -> CodeStyleProfile:
    """合并多个样本：取众数；样本不足时回退到语言默认。"""
    if not samples:
        return get_language_default(language)
    base = get_language_default(language)
    base.sample_count = len(samples)
    # 缩进众数
    indents = [s.indent for s in samples if s.indent]
    if indents:
        # int vs tab 分别计数
        int_counts: Dict[int, int] = {}
        tab_count = 0
        for ind in indents:
            if ind == "tab":
                tab_count += 1
            elif isinstance(ind, int):
                int_counts[ind] = int_counts.get(ind, 0) + 1
        if tab_count > max(int_counts.values() or [0]):
            base.indent = "tab"
        elif int_counts:
            base.indent = max(int_counts, key=int_counts.get)
    # 命名众数
    naming_counts: Dict[str, int] = {}
    for s in samples:
        naming_counts[s.naming] = naming_counts.get(s.naming, 0) + 1
    if naming_counts:
        base.naming = max(naming_counts, key=naming_counts.get)
    # 注释语言众数
    comment_counts: Dict[str, int] = {}
    for s in samples:
        comment_counts[s.comment_language] = comment_counts.get(s.comment_language, 0) + 1
    if comment_counts:
        base.comment_language = max(comment_counts, key=comment_counts.get)
    # 最大行长平均
    lens = [s.max_line_length for s in samples if s.max_line_length > 0]
    if lens:
        base.max_line_length = sum(lens) // len(lens)
    # 大括号众数
    brace_counts: Dict[str, int] = {}
    for s in samples:
        brace_counts[s.brace_style] = brace_counts.get(s.brace_style, 0) + 1
    if brace_counts:
        base.brace_style = max(brace_counts, key=brace_counts.get)
    return base


# ---- 单项检测 ----


def _detect_indent(lines: List[str]) -> Any:
    """从代码行的前导空白推断缩进。"""
    counts: Dict[Any, int] = {}
    for ln in lines:
        if not ln.strip():
            continue
        leading = ln[: len(ln) - len(ln.lstrip())]
        if not leading:
            continue
        if "\t" in leading:
            counts["tab"] = counts.get("tab", 0) + 1
        elif " " in leading:
            n = len(leading)
            # 常见 8/4/2：从大到小，优先匹配最大公约缩进
            for candidate in (8, 4, 2):
                if n % candidate == 0:
                    counts[candidate] = counts.get(candidate, 0) + 1
                    break
            else:
                counts[n] = counts.get(n, 0) + 1
    if not counts:
        return 4
    return max(counts, key=counts.get)


def _detect_naming(content: str, language: str) -> str:
    """从函数名/变量名推断命名风格。"""
    # 抽取 def / function / void / int 等后面的标识符
    patterns = [
        r"(?:def|function|void|int|float|double|bool|public|private|static)\s+(\w+)",
        r"func\s+(\w+)",
        r"fn\s+(\w+)",
    ]
    names: List[str] = []
    for pat in patterns:
        names.extend(re.findall(pat, content))
    if not names:
        return get_language_default(language).naming
    snake = sum(1 for n in names if re.fullmatch(r"[a-z][a-z0-9_]*", n) and "_" in n)
    camel = sum(1 for n in names if re.fullmatch(r"[a-z][a-zA-Z0-9]*", n) and any(c.isupper() for c in n))
    pascal = sum(1 for n in names if re.fullmatch(r"[A-Z][a-zA-Z0-9]*", n))
    if pascal >= max(snake, camel):
        return "PascalCase"
    if camel >= snake:
        return "camelCase"
    if snake > 0:
        return "snake_case"
    return get_language_default(language).naming


def _detect_comment_language(content: str) -> str:
    """从注释推断语言（中文/英文）。"""
    comments: List[str] = []
    # 行注释
    for m in re.finditer(r"//\s*(.+)|#\s*(.+)", content):
        comments.append((m.group(1) or m.group(2) or "").strip())
    # 块注释
    for m in re.finditer(r"/\*(.+?)\*/", content, re.DOTALL):
        comments.append(m.group(1).strip())
    if not comments:
        return "中文"
    # 中文占比
    cn_chars = sum(
        1 for c in "".join(comments)
        if "\u4e00" <= c <= "\u9fff"
    )
    en_words = sum(
        1 for w in re.findall(r"[A-Za-z]+", "".join(comments))
    )
    if cn_chars > en_words * 0.5:
        return "中文"
    if en_words > cn_chars * 5:
        return "English"
    # 默认中文（系统约束）
    return "中文"


def _detect_max_line_length(lines: List[str]) -> int:
    """检测最长行长（取 90 分位）。"""
    if not lines:
        return 100
    lens = sorted(len(ln) for ln in lines)
    return lens[int(len(lens) * 0.9)] if lens else 100


def _detect_brace_style(lines: List[str]) -> str:
    """推断大括号风格：same_line（K&R）或 next_line（Allman）。"""
    same = 0
    next_line = 0
    for ln in lines:
        s = ln.rstrip()
        if s.endswith("{"):
            # 下一行是否是 }
            same += 1
        # 检测 { 单独成行
        if s.strip() == "{":
            next_line += 1
    if next_line > same:
        return "next_line"
    return "same_line"


def _detect_trailing_semicolon(lines: List[str], language: str) -> bool:
    """是否行尾加分号（C/C++/JS/TS/Rust/Java/Go）。"""
    if language in ("python",):
        return False
    # 仅统计"语句行"：排除注释、控制流（{、}、:、,）
    code_lines = [
        ln for ln in lines
        if ln.strip()
        and not ln.strip().startswith(("#", "//", "/*", "*", "*/"))
        and not ln.rstrip().endswith(("{", "}", ":", ","))
    ]
    if not code_lines:
        return True
    semi = sum(1 for ln in code_lines if ln.rstrip().endswith(";"))
    return semi >= len(code_lines) * 0.5


def _detect_module_docstring(lines: List[str]) -> bool:
    """检测文件首是否有模块 docstring（Python）。"""
    for ln in lines[:10]:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        if s.startswith('"""') or s.startswith("'''"):
            return True
        return False
    return False


# ============================================================================
# 一致性检查
# ============================================================================


@dataclass
class StyleViolation:
    """风格不一致项。"""

    file: str = ""
    line: int = 0
    rule: str = ""           # indent | naming | line-length | brace | semicolon | comment-lang
    expected: str = ""
    actual: str = ""
    severity: str = "warn"   # warn | error

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def check_consistency(
    content: str,
    profile: CodeStyleProfile,
    language: str,
    file_path: str = "",
) -> List[StyleViolation]:
    """检查生成代码与既有风格的一致性。返回差异列表。"""
    violations: List[StyleViolation] = []
    if not content:
        return violations
    lines = content.splitlines()

    # 缩进
    for i, ln in enumerate(lines, 1):
        if not ln.strip():
            continue
        leading = ln[: len(ln) - len(ln.lstrip())]
        if not leading:
            continue
        if profile.indent == "tab":
            if " " in leading and "\t" not in leading:
                violations.append(StyleViolation(
                    file=file_path, line=i, rule="indent",
                    expected="tab", actual="spaces(%d)" % len(leading),
                ))
                break
        else:
            expected = profile.indent
            if "\t" in leading:
                violations.append(StyleViolation(
                    file=file_path, line=i, rule="indent",
                    expected="%d spaces" % expected, actual="tab",
                ))
                break
            # 检查是否是 4 的倍数（如果不是 4 缩进但用了 4 空格）
            n = len(leading)
            if n % expected != 0:
                violations.append(StyleViolation(
                    file=file_path, line=i, rule="indent",
                    expected="%d spaces (multiple)" % expected,
                    actual="%d spaces" % n,
                ))
                break

    # 命名风格：检查函数定义
    func_pattern = (
        r"def\s+(\w+)" if language == "python"
        else r"(?:void|int|float|double|bool|public|private|static)\s+(\w+)\s*\("
    )
    for m in re.finditer(func_pattern, content):
        name = m.group(1)
        line = content.count("\n", 0, m.start()) + 1
        actual = _classify_name(name)
        if actual and actual != profile.naming:
            # 仅当差异明显时报告
            violations.append(StyleViolation(
                file=file_path, line=line, rule="naming",
                expected=profile.naming, actual=actual,
                severity="warn",
            ))

    # 行长
    for i, ln in enumerate(lines, 1):
        if len(ln) > profile.max_line_length + 20:
            violations.append(StyleViolation(
                file=file_path, line=i, rule="line-length",
                expected="<=%d" % profile.max_line_length,
                actual="%d" % len(ln),
                severity="warn",
            ))

    # 注释语言（粗略：检查注释是否含中文，如果 profile 是中文）
    if profile.comment_language == "中文":
        comments = re.findall(r"//\s*(.+)|#\s*(.+)", content)
        en_only_comments = 0
        for c in comments:
            text = (c[0] or c[1] or "").strip()
            if text and not any("\u4e00" <= ch <= "\u9fff" for ch in text):
                en_only_comments += 1
        if en_only_comments > 5:
            violations.append(StyleViolation(
                file=file_path, line=0, rule="comment-lang",
                expected="中文", actual="English (%d 处)" % en_only_comments,
                severity="warn",
            ))

    return violations


def _classify_name(name: str) -> str:
    """分类标识符命名风格。"""
    if not name:
        return ""
    if "_" in name and name.islower():
        return "snake_case"
    if name[0].isupper():
        return "PascalCase"
    if any(c.isupper() for c in name[1:]):
        return "camelCase"
    if "-" in name:
        return "kebab-case"
    return ""


# ============================================================================
# 自动重写（最小化）
# ============================================================================


@dataclass
class RewriteResult:
    """风格重写结果。"""

    content: str
    applied: List[str] = field(default_factory=list)  # 应用的修复描述
    skipped: List[str] = field(default_factory=list)  # 跳过的修复


def auto_rewrite(
    content: str,
    profile: CodeStyleProfile,
    language: str,
) -> RewriteResult:
    """按既有风格最小化重写生成代码。

    保守策略：只做安全且可逆的修改：
    - 缩进转换（tab<->space）
    - 函数命名转换（snake_case<->camelCase）
    - 行长不强制改（仅记录）
    """
    if not content:
        return RewriteResult(content=content)
    applied: List[str] = []
    skipped: List[str] = []
    new_content = content

    # 1. 缩进转换
    if profile.indent == "tab":
        # 4 空格 → tab
        new = _convert_spaces_to_tabs(new_content, 4)
        if new != new_content:
            applied.append("缩进 4 空格 → tab")
            new_content = new
    elif isinstance(profile.indent, int):
        # tab → N 空格
        new = _convert_tabs_to_spaces(new_content, profile.indent)
        if new != new_content:
            applied.append("缩进 tab → %d 空格" % profile.indent)
            new_content = new
        # 修正"非 N 倍数"的前导空格（保守：只处理明显的 4→2 或 2→4）
        new = _normalize_space_indent(new_content, profile.indent)
        if new != new_content:
            applied.append("缩进规范化为 %d 空格倍数" % profile.indent)
            new_content = new

    # 2. 命名转换（C/C++/Java/JS/TS/Python 函数定义；目标 snake_case 或 camelCase）
    if language in ("cpp", "java", "javascript", "ts", "python") and profile.naming in ("camelCase", "snake_case"):
        new = _convert_function_naming(new_content, profile.naming, language)
        if new != new_content:
            applied.append("函数命名 → %s" % profile.naming)
            new_content = new

    return RewriteResult(content=new_content, applied=applied, skipped=skipped)


def _convert_spaces_to_tabs(content: str, tab_width: int) -> str:
    """行首 tab_width 个空格 → 1 个 tab。"""
    lines = content.split("\n")
    out = []
    for ln in lines:
        if not ln or not ln[0].isspace():
            out.append(ln)
            continue
        leading = ln[: len(ln) - len(ln.lstrip())]
        rest = ln[len(leading):]
        # 把 tab_width 个空格转 1 个 tab
        n_tabs = len(leading) // tab_width
        n_rem = len(leading) % tab_width
        out.append("\t" * n_tabs + " " * n_rem + rest)
    return "\n".join(out)


def _convert_tabs_to_spaces(content: str, tab_width: int) -> str:
    """行首 tab → tab_width 个空格。"""
    lines = content.split("\n")
    out = []
    for ln in lines:
        if not ln or "\t" not in ln[: len(ln) - len(ln.lstrip()) + 1]:
            out.append(ln)
            continue
        leading = ln[: len(ln) - len(ln.lstrip())]
        rest = ln[len(leading):]
        new_leading = leading.replace("\t", " " * tab_width)
        out.append(new_leading + rest)
    return "\n".join(out)


def _normalize_space_indent(content: str, expected: int) -> str:
    """把行首缩进规整为 expected 的倍数（不破坏结构，保守版：只处理 4↔2）。"""
    # 仅当 expected 是 2 或 4 时启用
    if expected not in (2, 4):
        return content
    lines = content.split("\n")
    out = []
    for ln in lines:
        if not ln or not ln[0].isspace():
            out.append(ln)
            continue
        leading = ln[: len(ln) - len(ln.lstrip())]
        rest = ln[len(leading):]
        n = len(leading)
        if n == 0:
            out.append(ln)
            continue
        # 把 4→2 或 2→4 转换（按当前 expected 反推）
        if expected == 2 and n % 4 == 0:
            new_n = n // 2
            out.append(" " * new_n + rest)
        elif expected == 4 and n % 2 == 0 and n % 4 != 0:
            new_n = n * 2
            out.append(" " * new_n + rest)
        else:
            out.append(ln)
    return "\n".join(out)


def _convert_function_naming(content: str, target: str, language: str) -> str:
    """转换函数命名风格（仅函数定义行；调用点不安全转换，跳过）。"""
    if language == "cpp":
        # 修改 C++ 函数定义：返回类型 + name(...)
        def repl(m: "re.Match[str]") -> str:
            prefix = m.group(1)
            name = m.group(2)
            rest = m.group(3)
            new_name = _convert_name(name, target)
            return "%s%s%s" % (prefix, new_name, rest)
        # 形如：void my_func(...)
        return re.sub(
            r"(void|int|float|double|bool|auto|std::\w+)\s+(\w+)(\s*\()",
            repl,
            content,
        )
    if language == "python":
        def repl(m: "re.Match[str]") -> str:
            prefix = m.group(1)
            name = m.group(2)
            rest = m.group(3)
            new_name = _convert_name(name, target)
            return "%s%s%s" % (prefix, new_name, rest)
        # 形如：def myFunc(   或  async def myFunc(
        return re.sub(
            r"((?:async\s+)?def\s+)(\w+)(\s*\()",
            repl,
            content,
        )
    if language in ("javascript", "ts"):
        def repl(m: "re.Match[str]") -> str:
            prefix = m.group(1)
            name = m.group(2)
            rest = m.group(3)
            new_name = _convert_name(name, target)
            return "%s%s%s" % (prefix, new_name, rest)
        # function my_func(
        return re.sub(
            r"(function\s+)(\w+)(\s*\()",
            repl,
            content,
        )
    return content


def _convert_name(name: str, target: str) -> str:
    """转换单个标识符命名风格。"""
    if target == "snake_case":
        # camelCase → snake_case
        s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
        return s.lower()
    if target == "camelCase":
        # snake_case → camelCase
        parts = name.split("_")
        if len(parts) <= 1:
            return name
        return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])
    return name

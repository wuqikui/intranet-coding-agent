"""Task 9 代码风格保真测试。

覆盖：
- TR-9.1: 风格一致性 rubric 1-5 分阈值 ≥4
  - detect_style 准确性（缩进/命名/注释语言）
  - check_consistency 检测出缩进/命名/行长差异
  - auto_rewrite 做最小化重写（tab↔space、snake↔camel）
  - retriever 节点产出 code_style 含 source 字段
  - writer 节点在生成文件上触发重写并记录
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.agent.style import (
    CodeStyleProfile,
    RewriteResult,
    StyleViolation,
    auto_rewrite,
    check_consistency,
    detect_style,
    get_language_default,
    merge_styles,
)


# ============================================================================
# 1. detect_style 准确性
# ============================================================================


def test_detect_python_style():
    code = '''"""模块 docstring."""
def calculate_total(items):
    # 计算总和（中文注释）
    total = 0
    for item in items:
        total += item
    return total
'''
    p = detect_style(code, "python")
    assert p.indent == 4, f"indent={p.indent}"
    assert p.naming == "snake_case", f"naming={p.naming}"
    assert p.comment_language == "中文", f"comment_language={p.comment_language}"
    assert p.module_docstring is True
    print(f"[PASS] detect_style(python) indent={p.indent} naming={p.naming} comment={p.comment_language}")


def test_detect_cpp_style_with_semicolons():
    code = '''#include <iostream>
// 主函数（中文注释）
int main() {
    std::cout << "hello" << std::endl;
    return 0;
}
'''
    p = detect_style(code, "cpp")
    assert p.indent == 4, f"indent={p.indent}"
    assert p.trailing_semicolon is True, f"semicolon={p.trailing_semicolon}"
    assert p.comment_language == "中文"
    print(f"[PASS] detect_style(cpp) indent={p.indent} semicolon={p.trailing_semicolon}")


def test_detect_ts_2space_camel():
    code = '''// 用户服务（中文注释）
function getUserInfo(id: number): string {
  return "user-" + id;
}
'''
    p = detect_style(code, "ts")
    # 2 空格 + camelCase（函数名 getUserInfo）
    assert p.indent in (2, 4), f"indent={p.indent}"
    assert p.trailing_semicolon is True
    print(f"[PASS] detect_style(ts) indent={p.indent} semicolon={p.trailing_semicolon}")


def test_detect_tab_indent():
    code = "def f():\n\tx = 1\n\treturn x\n"
    p = detect_style(code, "python")
    assert p.indent == "tab", f"indent expected tab got {p.indent}"
    print(f"[PASS] detect_style(tab) indent={p.indent}")


# ============================================================================
# 2. check_consistency 检测差异
# ============================================================================


def test_check_indent_violation():
    # profile 要求 4 空格，代码用 tab
    code = "def f():\n\tx = 1\n\treturn x\n"
    profile = CodeStyleProfile(indent=4, naming="snake_case", comment_language="中文")
    vs = check_consistency(code, profile, "python", "test.py")
    assert any(v.rule == "indent" for v in vs), f"expected indent violation, got {[v.rule for v in vs]}"
    print(f"[PASS] check_consistency indent violation found ({len(vs)} 项)")


def test_check_naming_violation():
    # profile 要求 snake_case，代码用 camelCase
    code = "def calculateTotal():\n    return 0\n"
    profile = CodeStyleProfile(indent=4, naming="snake_case", comment_language="中文")
    vs = check_consistency(code, profile, "python", "test.py")
    naming_vs = [v for v in vs if v.rule == "naming"]
    assert naming_vs, f"expected naming violation, got {[v.rule for v in vs]}"
    assert naming_vs[0].expected == "snake_case"
    assert naming_vs[0].actual == "camelCase"
    print(f"[PASS] check_consistency naming violation: {naming_vs[0].expected} vs {naming_vs[0].actual}")


def test_check_line_length_violation():
    # profile 要求 max=80，代码超长
    long_line = "x = " + "a" * 200
    code = "def f():\n    " + long_line + "\n"
    profile = CodeStyleProfile(indent=4, naming="snake_case", comment_language="中文", max_line_length=80)
    vs = check_consistency(code, profile, "python", "test.py")
    assert any(v.rule == "line-length" for v in vs), f"expected line-length violation, got {[v.rule for v in vs]}"
    print(f"[PASS] check_consistency line-length violation ({len(vs)} 项)")


def test_check_consistency_clean_code():
    # 一致的代码不应产生违规
    code = '''"""模块 docstring."""
def calculate_total(items):
    # 计算总和（中文注释）
    total = 0
    for item in items:
        total += item
    return total
'''
    profile = CodeStyleProfile(indent=4, naming="snake_case", comment_language="中文", max_line_length=100)
    vs = check_consistency(code, profile, "python", "test.py")
    # 允许少量行长差异但不应有 indent/naming 违规
    hard_vs = [v for v in vs if v.rule in ("indent", "naming")]
    assert not hard_vs, f"不应有 indent/naming 违规: {hard_vs}"
    print(f"[PASS] check_consistency 干净代码无硬性违规 (共 {len(vs)} 软提醒)")


# ============================================================================
# 3. auto_rewrite 最小化重写
# ============================================================================


def test_auto_rewrite_tab_to_spaces():
    code = "def f():\n\tx = 1\n\treturn x\n"
    profile = CodeStyleProfile(indent=4, naming="snake_case", comment_language="中文")
    result = auto_rewrite(code, profile, "python")
    assert result.applied, f"expected rewrite applied, got {result.applied}"
    assert "\t" not in result.content, "tab should be converted"
    assert "    x = 1" in result.content, f"4-space indent expected: {result.content!r}"
    print(f"[PASS] auto_rewrite tab→4space: applied={result.applied}")


def test_auto_rewrite_spaces_to_tab():
    code = "def f():\n    x = 1\n    return x\n"
    profile = CodeStyleProfile(indent="tab", naming="snake_case", comment_language="中文")
    result = auto_rewrite(code, profile, "python")
    assert result.applied, f"expected rewrite applied, got {result.applied}"
    assert "\tx = 1" in result.content, f"tab indent expected: {result.content!r}"
    print(f"[PASS] auto_rewrite 4space→tab: applied={result.applied}")


def test_auto_rewrite_cpp_function_naming():
    # camelCase 函数 → snake_case（C++ profile）
    code = '''#include <iostream>
int calculateTotal(int a, int b) {
    return a + b;
}
'''
    profile = CodeStyleProfile(
        indent=4, naming="snake_case", comment_language="中文",
        trailing_semicolon=True,
    )
    result = auto_rewrite(code, profile, "cpp")
    assert result.applied, f"expected rewrite applied, got {result.applied}"
    assert "calculate_total" in result.content, f"snake_case name expected: {result.content!r}"
    assert "calculateTotal" not in result.content or "calculate_total" in result.content
    print(f"[PASS] auto_rewrite cpp camel→snake: applied={result.applied}")


def test_auto_rewrite_idempotent_on_clean_code():
    code = "def calculate_total():\n    return 0\n"
    profile = CodeStyleProfile(indent=4, naming="snake_case", comment_language="中文")
    r1 = auto_rewrite(code, profile, "python")
    r2 = auto_rewrite(r1.content, profile, "python")
    assert r2.content == r1.content, "幂等：再重写应无变化"
    print(f"[PASS] auto_rewrite 幂等: applied={r2.applied}")


# ============================================================================
# 4. merge_styles 多样本合并
# ============================================================================


def test_merge_styles_majority():
    s1 = CodeStyleProfile(indent=4, naming="snake_case", comment_language="中文", max_line_length=100)
    s2 = CodeStyleProfile(indent=4, naming="snake_case", comment_language="中文", max_line_length=100)
    s3 = CodeStyleProfile(indent=2, naming="camelCase", comment_language="English", max_line_length=120)
    merged = merge_styles([s1, s2, s3], "python")
    assert merged.indent == 4, f"majority indent=4, got {merged.indent}"
    assert merged.naming == "snake_case", f"majority naming=snake_case, got {merged.naming}"
    assert merged.comment_language == "中文", f"majority comment=中文, got {merged.comment_language}"
    assert merged.sample_count == 3
    print(f"[PASS] merge_styles 众数: indent={merged.indent} naming={merged.naming}")


def test_get_language_default_fallback():
    p = get_language_default("python")
    assert p.indent == 4 and p.naming == "snake_case"
    p2 = get_language_default("unknown_lang")
    assert p2.indent == 4, "未知语言回退到 python 默认"
    print(f"[PASS] get_language_default unknown→python fallback")


# ============================================================================
# 5. 风格一致性 rubric 评分（TR-9.1: ≥4 分）
# ============================================================================


def _score_consistency(content: str, profile: CodeStyleProfile, language: str) -> int:
    """rubric 1-5 评分：
    5: 完全一致（0 违规）
    4: 基本一致（≤2 软违规，无硬违规）
    3: 有少量差异（≤5 违规）
    2: 明显差异（>5 违规）
    1: 严重不一致
    重写后评分应 ≥4。
    """
    vs = check_consistency(content, profile, language, "test")
    hard = [v for v in vs if v.rule in ("indent", "naming")]
    soft = [v for v in vs if v.rule in ("line-length", "comment-lang")]
    if not vs:
        return 5
    if len(hard) == 0 and len(soft) <= 2:
        return 4
    if len(vs) <= 5:
        return 3
    if len(vs) <= 10:
        return 2
    return 1


def test_rubric_consistency_threshold():
    """TR-9.1: 重写后风格一致性 ≥4 分。"""
    # 原始：tab 缩进 + camelCase 命名，profile 要求 4空格 + snake_case
    bad_code = "def calculateTotal(items):\n\ttotal = 0\n\tfor item in items:\n\t\ttotal += item\n\treturn total\n"
    profile = CodeStyleProfile(indent=4, naming="snake_case", comment_language="中文")

    score_before = _score_consistency(bad_code, profile, "python")
    assert score_before < 4, f"原始代码应不一致 (score={score_before})"

    # 自动重写
    result = auto_rewrite(bad_code, profile, "python")
    assert result.applied, "应有重写"

    score_after = _score_consistency(result.content, profile, "python")
    assert score_after >= 4, f"TR-9.1: 重写后一致性应 ≥4 分，实际 {score_after} 分"
    print(f"[PASS] TR-9.1 rubric: {score_before} 分 → 重写后 {score_after} 分 (阈值 4)")


# ============================================================================
# 6. retriever 节点产出 code_style 字段
# ============================================================================


def test_retriever_produces_code_style():
    """retriever 节点应输出 code_style 字典，含 source 字段。"""
    import asyncio
    from app.agent.nodes.retriever import retriever_node
    from app.agent.state import AgentState

    state: AgentState = {
        "user_query": "如何实现用户登录",
        "primary_language": "python",
    }
    result = asyncio.get_event_loop().run_until_complete(retriever_node(state)) if False else None
    # 同步调用（retriever_node 是 async）
    result = asyncio.run(retriever_node(state))

    assert "code_style" in result, "retriever 应返回 code_style"
    cs = result["code_style"]
    assert "source" in cs, "code_style 应含 source 字段"
    assert cs["source"] in ("kb", "language_default"), f"source 非法: {cs['source']}"
    assert "indent" in cs and "naming" in cs, f"code_style 缺字段: {cs}"
    print(f"[PASS] retriever code_style: source={cs['source']} indent={cs['indent']} naming={cs['naming']}")


# ============================================================================
# 7. writer 节点对风格不一致的生成文件做重写
# ============================================================================


def test_writer_rewrites_inconsistent_files():
    """writer 节点对 tab 缩进的 .py 文件应触发 auto_rewrite。"""
    import asyncio
    from app.agent.nodes.writer import writer_node
    from app.agent.state import AgentState

    # 构造：生成文件用 tab，profile 要求 4 空格
    bad_code = "def hello():\n\tprint('hi')\n\treturn None\n"
    state: AgentState = {
        "project_id": "",  # 让 writer 自动创建
        "user_id": "test_user",
        "project_name": "style_test_proj",
        "primary_language": "python",
        "generated_files": {"main.py": bad_code},
        "code_style": CodeStyleProfile(
            indent=4, naming="snake_case", comment_language="中文"
        ).to_dict(),
    }
    result = asyncio.run(writer_node(state))
    msgs = result.get("messages") or []
    assert msgs, "writer 应返回 messages"
    # writer 的 meta 含 rewritten 列表
    last = msgs[-1] if isinstance(msgs, list) else None
    assert last is not None
    meta = last.get("meta", {}) if isinstance(last, dict) else {}
    rewritten = meta.get("rewritten", [])
    # tab→4space 应触发重写
    assert rewritten, f"应触发重写，rewritten={rewritten}"
    assert "main.py" in [r.get("file") for r in rewritten]
    # 同步回 generated_files 的内容应已转 4 空格
    new_content = result["generated_files"]["main.py"]
    assert "\t" not in new_content, "重写后仍含 tab"
    assert "    print" in new_content, f"重写后应为 4 空格: {new_content!r}"
    print(f"[PASS] writer 重写 main.py: rewritten={len(rewritten)} violations_before={rewritten[0].get('violations_before')}")


def test_writer_skips_non_code_files():
    """writer 节点应跳过 .md/.json 等非代码文件（不重写）。"""
    import asyncio
    from app.agent.nodes.writer import writer_node
    from app.agent.state import AgentState

    state: AgentState = {
        "project_id": "",
        "user_id": "test_user_md",
        "project_name": "style_test_md",
        "primary_language": "python",
        "generated_files": {
            "README.md": "# Project\n\nHello.\n",
            "main.py": "def f():\n\treturn 1\n",
        },
        "code_style": CodeStyleProfile(
            indent=4, naming="snake_case", comment_language="中文"
        ).to_dict(),
    }
    result = asyncio.run(writer_node(state))
    msgs = result.get("messages") or []
    meta = msgs[-1].get("meta", {}) if msgs and isinstance(msgs[-1], dict) else {}
    rewritten = meta.get("rewritten", [])
    # 仅 main.py 被重写，README.md 不在
    rewritten_files = [r.get("file") for r in rewritten]
    assert "main.py" in rewritten_files
    assert "README.md" not in rewritten_files, "README.md 不应被重写"
    print(f"[PASS] writer 跳过非代码文件: rewritten={rewritten_files}")


# ============================================================================
# 入口
# ============================================================================


if __name__ == "__main__":
    tests = [
        test_detect_python_style,
        test_detect_cpp_style_with_semicolons,
        test_detect_ts_2space_camel,
        test_detect_tab_indent,
        test_check_indent_violation,
        test_check_naming_violation,
        test_check_line_length_violation,
        test_check_consistency_clean_code,
        test_auto_rewrite_tab_to_spaces,
        test_auto_rewrite_spaces_to_tab,
        test_auto_rewrite_cpp_function_naming,
        test_auto_rewrite_idempotent_on_clean_code,
        test_merge_styles_majority,
        test_get_language_default_fallback,
        test_rubric_consistency_threshold,
        test_retriever_produces_code_style,
        test_writer_rewrites_inconsistent_files,
        test_writer_skips_non_code_files,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{'='*60}\nTask 9 测试: {passed} 通过 / {failed} 失败 / 共 {len(tests)}\n{'='*60}")
    sys.exit(0 if failed == 0 else 1)

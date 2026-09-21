"""Agentic 探索器测试（grep/glob/read 迭代探索）。

覆盖：
- ExploreCorpus 三工具（glob/grep/read）的命中与边界
- build_corpus：KB 命中 + 工作区文件组装、跳过目录
- run_exploration：脚本化多轮工具调用 / finish 汇总 / 无效输出降级
- explorer_node：与 state 集成、空语料跳过、异常降级
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.agent import explorer as explorer_mod
from app.agent.explorer import ExploreCorpus, build_corpus, run_exploration
from app.agent.nodes import _common
from app.agent.nodes import explorer as explorer_node_mod
from app.agent.nodes.explorer import explorer_node


def _run(coro):
    return asyncio.run(coro)


# ============================================================================
# ExploreCorpus 单元
# ============================================================================


def test_corpus_glob():
    corpus = ExploreCorpus({
        "src/main.py": "print(1)\n",
        "src/utils/helper.py": "x = 1\n",
        "CMakeLists.txt": "project(foo)\n",
        "README.md": "# foo\n",
    })
    hits = corpus.glob("src/*.py")
    assert hits == ["src/main.py"], hits
    hits = corpus.glob("**/*.py")
    assert "src/main.py" in hits and "src/utils/helper.py" in hits
    hits = corpus.glob("*.md")
    assert hits == ["README.md"]
    assert corpus.glob("*.xyz") == []
    print("[PASS] glob 通配符匹配")


def test_corpus_grep():
    corpus = ExploreCorpus({
        "a.py": "def add(a, b):\n    return a + b\n",
        "b.py": "from a import add\n",
    })
    hits = corpus.grep(r"\badd\b")
    assert len(hits) == 2, hits
    assert hits[0].startswith("a.py:1:")
    assert corpus.grep("(") == []  # 非法正则不抛异常
    print("[PASS] grep 正则检索 + 非法正则安全")


def test_corpus_read():
    corpus = ExploreCorpus({"big.py": "x = 1\n" * 5000})
    content = corpus.read("big.py")
    assert content.startswith("x = 1")
    assert "截断" in content  # 超长截断
    assert corpus.read("nope.py") == ""
    print("[PASS] read 截断与缺失文件")


# ============================================================================
# build_corpus
# ============================================================================


def test_build_corpus_kb_and_workspace():
    docs = [
        {"filename": "repo/legacy.py", "content": "class UserService:\n    pass\n"},
        {"content": "no filename doc\n"},
    ]
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        (root / "src" / "app.py").write_text("from fastapi import FastAPI\n", encoding="utf-8")
        (root / "__pycache__").mkdir()
        (root / "__pycache__" / "junk.py").write_text("junk\n", encoding="utf-8")
        (root / ".git").mkdir()
        (root / ".git" / "config.py").write_text("secret\n", encoding="utf-8")

        corpus = build_corpus(docs, root)
        # 2 个 KB 文档（有 content 均计入）+ 1 个工作区文件（跳过 __pycache__/.git）
        assert len(corpus) == 3
        kb_paths = [p for p in corpus.paths if p.startswith("kb://")]
        assert len(kb_paths) == 2
        assert "src/app.py" in corpus.paths
        assert not any("__pycache__" in p or ".git" in p for p in corpus.paths)
        # grep 能同时搜 KB 与工作区
        hits = corpus.grep(r"class UserService")
        assert hits and hits[0].startswith("kb://")
    print("[PASS] build_corpus 组装 KB + 工作区并跳过无关目录")


# ============================================================================
# run_exploration 循环
# ============================================================================


class _FakeRouter:
    pass


def _patch_call_llm(script, monkeypatch_like=None):
    """按脚本顺序返回 call_llm 输出。返回 (fake, 还原函数)。"""
    calls = {"n": 0}

    async def fake_call_llm(router, user_id, role, system, user, max_tokens=2048):
        idx = min(calls["n"], len(script) - 1)
        calls["n"] += 1
        return script[idx]

    original = explorer_mod._common.call_llm
    explorer_mod._common.call_llm = fake_call_llm
    return calls, lambda: setattr(explorer_mod._common, "call_llm", original)


def test_run_exploration_scripted_flow():
    corpus = ExploreCorpus({
        "src/calc.py": "def add(a, b):\n    return a + b\n",
        "tests/test_calc.py": "from src.calc import add\n",
    })
    script = [
        '{"tool": "glob", "args": {"pattern": "**/*.py"}}',
        '{"tool": "read", "args": {"path": "src/calc.py"}}',
        '{"tool": "finish", "args": {"summary": "已有 add 函数，测试从 src.calc 导入"}}',
    ]
    _calls, restore = _patch_call_llm(script)
    try:
        result = _run(run_exploration(_FakeRouter(), "u1", "dev", "写一个乘法", corpus))
    finally:
        restore()
    assert result["status"] == "ok", result
    assert result["rounds"] == 3
    assert [t["tool"] for t in result["tool_calls"]] == ["glob", "read"]
    assert result["files_read"] == ["src/calc.py"]
    assert "add" in result["summary"]
    print("[PASS] 脚本化探索：glob → read → finish，发现已入 state")


def test_run_exploration_invalid_output_degrades():
    corpus = ExploreCorpus({"a.py": "x = 1\n"})
    script = ["这不是 JSON（mock 模板输出）", "仍然不是 JSON"]
    _calls, restore = _patch_call_llm(script)
    try:
        result = _run(run_exploration(_FakeRouter(), "u1", "dev", "q", corpus))
    finally:
        restore()
    assert result["status"] == "degraded"
    assert result["rounds"] == 2
    print("[PASS] 连续无效输出 → degraded（mock 模式安全）")


def test_run_exploration_empty_corpus_skipped():
    result = _run(run_exploration(_FakeRouter(), "u1", "dev", "q", ExploreCorpus()))
    assert result["status"] == "skipped"
    print("[PASS] 空语料 → skipped")


# ============================================================================
# explorer_node 集成
# ============================================================================


def _base_state(**overrides):
    from app.agent.state import initial_state
    state = initial_state(user_query="写一个计算器", user_id="u1", role="dev")
    state.update(overrides)
    return state


def test_explorer_node_with_corpus():
    script = ['{"tool": "finish", "args": {"summary": "发现既有 UserService 约定"}}']
    _calls, restore = _patch_call_llm(script)
    try:
        state = _base_state(
            knowledge_context={"code": [{"filename": "legacy.py", "content": "class UserService:\n    pass\n"}]},
        )
        out = _run(explorer_node(state))
    finally:
        restore()
    exploration = out["exploration"]
    assert exploration["status"] == "ok"
    assert "UserService" in exploration["summary"]
    # messages 是追加后的完整列表，最后一条 meta.node == explorer
    assert out["messages"][-1]["meta"]["node"] == "explorer"
    print("[PASS] explorer_node：探索结果写入 state + 消息流")


def test_explorer_node_empty_corpus_skips():
    state = _base_state(knowledge_context={"code": []})
    out = _run(explorer_node(state))
    assert out["exploration"]["status"] == "skipped"
    assert "跳过" in out["messages"][-1]["content"]
    print("[PASS] explorer_node：空语料优雅跳过")


def test_explorer_node_never_blocks_pipeline():
    """call_llm 抛异常时节点降级而不抛出。"""
    async def boom(*a, **k):
        raise RuntimeError("gateway down")
    original = explorer_mod._common.call_llm
    explorer_mod._common.call_llm = boom
    try:
        state = _base_state(
            knowledge_context={"code": [{"filename": "a.py", "content": "x = 1\n"}]},
        )
        out = _run(explorer_node(state))
    finally:
        explorer_mod._common.call_llm = original
    assert out["exploration"]["status"] == "degraded"
    assert out["exploration"]["rounds"] <= 1  # 首轮即失败
    print("[PASS] explorer_node：网关异常 → degraded，绝不阻断管线")


def test_explorer_registered_in_graph():
    from app.agent.nodes import NODES, NODE_ORDER
    assert "explorer" in NODES
    assert NODE_ORDER.index("explorer") == NODE_ORDER.index("retriever") + 1
    assert NODE_ORDER.index("architect") == NODE_ORDER.index("explorer") + 1
    print("[PASS] explorer 已注册到管线（retriever → explorer → architect）")


if __name__ == "__main__":
    test_corpus_glob()
    test_corpus_grep()
    test_corpus_read()
    test_build_corpus_kb_and_workspace()
    test_run_exploration_scripted_flow()
    test_run_exploration_invalid_output_degrades()
    test_run_exploration_empty_corpus_skipped()
    test_explorer_node_with_corpus()
    test_explorer_node_empty_corpus_skips()
    test_explorer_node_never_blocks_pipeline()
    test_explorer_registered_in_graph()
    print("\n=== 探索器测试全部通过 ===")

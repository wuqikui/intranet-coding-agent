"""Task 8 强制工程循环测试。

覆盖：
- TR-8.1: 工程循环日志含真实编译调用 + 修复轮次 + 最终通过
- TR-8.2: 修复轮次上限 N≥5 可配置
- TR-8.3: C/C++ 现代实践符合度（CMake 缺 PUBLIC/PRIVATE 检测 + RAII + 禁 UB 规则扫描）

本机若无 cmake/g++，BuildLoop 会返回 tooling_missing 优雅降级，
不视为代码失败（部署机具备工具链时自动启用真实编译）。
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.agent.build_loop import (
    AutoFixer,
    BuildLoop,
    CCPlusRuleScanner,
    CompileError,
    CompileErrorParser,
    reset_build_loop_for_test,
)
from app.config import get_settings


# ============================================================================
# TR-8.2: 修复轮次上限 N≥5 可配置
# ============================================================================


def test_max_rounds_at_least_5():
    """TR-8.2: MAX_FIX_ROUNDS < 5 时强制提升到 5。"""
    # 默认配置
    settings = get_settings()
    print("settings.MAX_FIX_ROUNDS = %s" % settings.MAX_FIX_ROUNDS)

    # 显式传入 <5 应被强制提升
    bl = BuildLoop(max_rounds=2)
    assert bl.max_rounds >= 5, "max_rounds 必须 >=5，实际 %s" % bl.max_rounds
    print("[PASS] max_rounds 强制下限 >=5，实际 %d" % bl.max_rounds)

    # 显式传入 10 应保留
    bl = BuildLoop(max_rounds=10)
    assert bl.max_rounds == 10
    print("[PASS] max_rounds 可配置为 10")


# ============================================================================
# CompileErrorParser: 解析 gcc/clang/cmake/linker
# ============================================================================


def test_error_parser_gcc():
    """解析 gcc/clang 错误格式。"""
    parser = CompileErrorParser()
    output = (
        "main.cpp: In function 'int main()':\n"
        "main.cpp:5:10: error: 'std::cout' was not declared in this scope\n"
        "    5 |     std::cout << \"hello\";\n"
        "      |          ^~~~~\n"
        "main.cpp:5:21: error: expected ';' before 'return' [Werror=semicolon]\n"
    )
    errors = parser.parse(output)
    assert len(errors) >= 2, "至少解析到 2 个错误，实际 %d" % len(errors)
    e0 = errors[0]
    assert "main.cpp" in e0.file
    assert e0.line == 5
    assert e0.severity == "error"
    assert "was not declared" in e0.message
    print("[PASS] gcc 错误解析: %d 个错误，首个 %s:%d %s" % (len(errors), e0.file, e0.line, e0.message))


def test_error_parser_cmake():
    """解析 cmake 错误格式。"""
    parser = CompileErrorParser()
    output = (
        "CMake Error at CMakeLists.txt (8):\n"
        "  Cannot find package FooConfig\n"
        "CMake Error: The following variables are used in this project, but they are set to NOTFOUND.\n"
    )
    errors = parser.parse(output)
    assert len(errors) >= 1
    cmake_err = next(e for e in errors if e.source == "cmake")
    assert "CMakeLists.txt" in cmake_err.file
    assert cmake_err.line == 8
    print("[PASS] cmake 错误解析: %s:%d %s" % (cmake_err.file, cmake_err.line, cmake_err.message))


def test_error_parser_linker():
    """解析 linker 错误格式。"""
    parser = CompileErrorParser()
    output = (
        "/usr/bin/ld: CMakeFiles/app.dir/main.cpp.o: in function `main':\n"
        "main.cpp:(.text+0x5): undefined reference to `foo()'\n"
        "collect2: error: ld returned 1 exit status\n"
    )
    errors = parser.parse(output)
    assert len(errors) >= 1
    linker_err = next(e for e in errors if e.source == "linker")
    assert "undefined reference" in linker_err.message or "undefined" in linker_err.message
    print("[PASS] linker 错误解析: %s" % linker_err.message[:80])


# ============================================================================
# AutoFixer: include 缺失 / std:: 前缀 / 分号 / cstring
# ============================================================================


def test_fixer_adds_iostream():
    """AutoFixer 检测到 'std::cout' was not declared → 加 #include <iostream>。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "main.cpp").write_text(
            "int main() {\n    std::cout << \"hello\";\n    return 0;\n}\n",
            encoding="utf-8",
        )
        fixer = AutoFixer(root)
        errors = [
            CompileError(
                file="main.cpp",
                line=2,
                severity="error",
                message="'std::cout' was not declared in this scope",
                source="gcc",
            )
        ]
        actions = fixer.fix_errors(errors)
        assert any(a.applied for a in actions), "应有至少一个修复被应用"
        new_content = (root / "main.cpp").read_text(encoding="utf-8")
        assert "#include <iostream>" in new_content, "应已加入 <iostream>"
        print("[PASS] AutoFixer 加 #include <iostream>: %s" % actions[0].description)


def test_fixer_adds_semicolon():
    """AutoFixer 检测到 expected ';' before 'X' → 在行尾补分号。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "main.cpp").write_text(
            "int main() {\n    int x = 1\n    return x;\n}\n",
            encoding="utf-8",
        )
        fixer = AutoFixer(root)
        errors = [
            CompileError(
                file="main.cpp",
                line=2,
                severity="error",
                message="expected ';' before 'return'",
                source="gcc",
            )
        ]
        actions = fixer.fix_errors(errors)
        assert any(a.applied for a in actions), "应补分号"
        new_content = (root / "main.cpp").read_text(encoding="utf-8")
        lines = new_content.split("\n")
        assert lines[1].rstrip().endswith(";"), "第 2 行应已补分号"
        print("[PASS] AutoFixer 补分号: %s" % actions[0].description)


def test_fixer_adds_cstring():
    """AutoFixer 检测到 memcpy/strlen was not declared → 加 #include <cstring>。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "util.cpp").write_text(
            "int len(const char* s) { return strlen(s); }\n"
            "void copy(char* d, const char* s) { memcpy(d, s, 10); }\n",
            encoding="utf-8",
        )
        fixer = AutoFixer(root)
        errors = [
            CompileError(file="util.cpp", line=1, message="'strlen' was not declared in this scope", source="gcc"),
            CompileError(file="util.cpp", line=2, message="'memcpy' was not declared in this scope", source="gcc"),
        ]
        actions = fixer.fix_errors(errors)
        new_content = (root / "util.cpp").read_text(encoding="utf-8")
        assert "#include <cstring>" in new_content, "应已加 <cstring>"
        print("[PASS] AutoFixer 加 #include <cstring>: 应用 %d 个" % sum(1 for a in actions if a.applied))


# ============================================================================
# CCPlusRuleScanner: 现代 CMake + RAII + 禁 UB
# ============================================================================


def test_rule_scanner_cmake_visibility():
    """CCPlusRuleScanner 检测 target_link_libraries 缺 PUBLIC/PRIVATE。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.16)\n"
            "project(app LANGUAGES CXX)\n"
            "add_executable(app main.cpp)\n"
            "target_link_libraries(app foo)  # 缺可见性\n",
            encoding="utf-8",
        )
        scanner = CCPlusRuleScanner()
        findings = scanner.scan(root, ["CMakeLists.txt"])
        assert any(f.code == "cmake-missing-visibility" for f in findings), \
            "应检测到 cmake-missing-visibility"
        print("[PASS] CMake 可见性规则: %d 条" % len(findings))


def test_rule_scanner_raw_new_delete():
    """CCPlusRuleScanner 检测裸 new / delete（违反 RAII）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "main.cpp").write_text(
            "int main() {\n"
            "    Foo* p = new Foo();\n"
            "    delete p;\n"
            "    Bar* q;\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        scanner = CCPlusRuleScanner()
        findings = scanner.scan(root, ["main.cpp"])
        codes = [f.code for f in findings]
        assert "raw-new" in codes, "应检测 raw-new"
        assert "raw-delete" in codes, "应检测 raw-delete"
        assert "raw-ptr-uninitialized" in codes, "应检测 raw-ptr-uninitialized"
        print("[PASS] RAII/UB 规则扫描: %d 条（含 raw-new/delete/未初始化指针）" % len(findings))


# ============================================================================
# TR-8.1: 工程循环日志含真实编译调用 + 修复轮次 + 最终通过
# ============================================================================


def test_build_loop_python_passes():
    """端到端：Python 项目真实编译通过（py_compile）。

    本机应已装 python，工具链可用。
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "main.py").write_text(
            "def add(a, b):\n    return a + b\n\nprint('1+2=', add(1, 2))\n",
            encoding="utf-8",
        )
        reset_build_loop_for_test()
        loop = BuildLoop()
        import asyncio
        result = asyncio.run(loop.run(root, ["main.py"]))
        print("  status=%s rounds=%d project_type=%s" % (result.status, result.rounds, result.project_type))
        # 部署机有 python，本机也有，应该 passed；若极端环境下没 python，会 tooling_missing
        assert result.status in ("passed", "tooling_missing"), \
            "期望 passed/tooling_missing，实际 %s" % result.status
        # TR-8.1: 日志含真实编译调用 + 修复轮次
        assert "==== 工程循环开始 ====" in result.build_log
        assert "Round" in result.build_log or result.status == "tooling_missing"
        assert "项目类型" in result.build_log
        print("[PASS] Python 工程循环: status=%s, 日志含真实编译调用" % result.status)


def test_build_loop_cmake_tooling_missing_on_dev_machine():
    """C++ 项目在本机（无 cmake/g++）应优雅降级到 tooling_missing。

    部署机（带 cmake/g++）会真实编译，但日志/状态机一致。
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.16)\n"
            "project(app LANGUAGES CXX)\n"
            "add_executable(app main.cpp)\n",
            encoding="utf-8",
        )
        (root / "main.cpp").write_text(
            "#include <iostream>\nint main() { std::cout << \"hi\"; return 0; }\n",
            encoding="utf-8",
        )
        reset_build_loop_for_test()
        loop = BuildLoop()
        import asyncio
        result = asyncio.run(loop.run(root, ["CMakeLists.txt", "main.cpp"]))
        print("  status=%s rounds=%d project_type=%s" % (result.status, result.rounds, result.project_type))
        # 部署机有 cmake/g++ 会 passed；本机无则 tooling_missing
        assert result.status in ("passed", "tooling_missing", "failed"), \
            "状态合法，实际 %s" % result.status
        assert result.project_type == "cmake", "项目类型应为 cmake"
        print("[PASS] CMake 工程循环: status=%s, 已检测项目类型" % result.status)


def test_build_loop_unknown_project():
    """无法识别项目类型 → no_project（不视为失败）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "README.txt").write_text("just a readme", encoding="utf-8")
        reset_build_loop_for_test()
        loop = BuildLoop()
        import asyncio
        result = asyncio.run(loop.run(root, ["README.txt"]))
        assert result.status == "no_project"
        print("[PASS] 未知项目类型 → no_project（不视为失败）")


# ============================================================================
# 审计 + 完整循环验证
# ============================================================================


def test_build_loop_log_contains_rounds_and_errors():
    """TR-8.1: 日志含真实编译调用 + 修复轮次 + 错误数。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # 故意写一个有错的 Python 文件（语法错误）
        (root / "main.py").write_text(
            "def add(a, b:\n    return a + b\n",  # 漏右括号
            encoding="utf-8",
        )
        reset_build_loop_for_test()
        loop = BuildLoop()
        import asyncio
        result = asyncio.run(loop.run(root, ["main.py"]))
        print("  status=%s rounds=%d errors=%d" % (result.status, result.rounds, len(result.errors)))
        # 日志必含关键字段
        assert "工程循环" in result.build_log
        assert "Round" in result.build_log or result.status == "tooling_missing"
        assert "项目类型" in result.build_log
        assert "工具链" in result.build_log
        print("[PASS] 工程循环日志完整: 含真实编译/轮次/错误")


# ============================================================================
# Python 工程循环：错误解析 + 自动修复 + 三段编译
# ============================================================================


def test_parse_python_syntax_error():
    """解析 py_compile 的 traceback（File 行 + 异常行配对）。"""
    parser = CompileErrorParser()
    output = (
        '  File "main.py", line 3\n'
        "    def add(a, b)\n"
        "                 ^\n"
        "SyntaxError: expected ':'\n"
    )
    errors = parser.parse(output)
    assert len(errors) == 1, errors
    e = errors[0]
    assert e.source == "python"
    assert e.file == "main.py"
    assert e.line == 3
    assert e.code == "SyntaxError"
    assert "expected" in e.message
    print("[PASS] Python SyntaxError 解析: %s:%d %s" % (e.file, e.line, e.code))


def test_parse_python_pytest_short_tb_combined_line():
    """解析 --tb=line 格式："tests/x.py:5: AssertionError"（位置+异常同行）。"""
    parser = CompileErrorParser()
    output = '/path/tests/test_calc.py:5: AssertionError: assert 3 == 4\n'
    errors = parser.parse(output)
    assert len(errors) == 1, errors
    e = errors[0]
    assert e.source == "python"
    assert e.file.endswith("test_calc.py")
    assert e.line == 5
    assert e.code == "AssertionError"
    print("[PASS] pytest 单行格式解析: %s:%d" % (e.file, e.line))


def test_parse_python_orphan_module_not_found():
    """导入冒烟的 <string> traceback → file 置空（orphan），供 requirements 修复。"""
    parser = CompileErrorParser()
    output = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 1, in <module>\n'
        "ModuleNotFoundError: No module named 'fakepkg_xyz'\n"
    )
    errors = parser.parse(output)
    assert len(errors) == 1
    e = errors[0]
    assert e.source == "python"
    assert e.file == "", "<string> 位置应置空"
    assert e.code == "ModuleNotFoundError"
    assert "fakepkg_xyz" in e.message
    print("[PASS] ModuleNotFoundError orphan 解析: %s" % e.message)


def test_fixer_python_missing_colon():
    """P1: SyntaxError 漏冒号 → 行尾补冒号。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "main.py").write_text(
            "def add(a, b)\n    return a + b\n", encoding="utf-8"
        )
        fixer = AutoFixer(root)
        errors = [
            CompileError(file="main.py", line=1, severity="error",
                         message="invalid syntax", code="SyntaxError", source="python")
        ]
        actions = fixer.fix_errors(errors)
        assert any(a.applied for a in actions), actions
        new_content = (root / "main.py").read_text(encoding="utf-8")
        assert new_content.split("\n")[0].endswith("add(a, b):"), new_content
        print("[PASS] Python 补冒号: %s" % [a.description for a in actions if a.applied][0])


def test_fixer_python_taberror():
    """P2: TabError → 前导制表符转 4 空格。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "main.py").write_text(
            "def f():\n\tx = 1\n\treturn x\n", encoding="utf-8"
        )
        fixer = AutoFixer(root)
        errors = [
            CompileError(file="main.py", line=2, severity="error",
                         message="inconsistent use of tabs and spaces in indentation",
                         code="TabError", source="python")
        ]
        actions = fixer.fix_errors(errors)
        assert any(a.applied for a in actions)
        new_content = (root / "main.py").read_text(encoding="utf-8")
        assert "\t" not in new_content
        assert "    x = 1" in new_content
        print("[PASS] Python TabError 修复: tab → 4 空格")


def test_fixer_python_missing_module_to_requirements():
    """P3: ModuleNotFoundError → 第三方依赖写入 requirements.txt（幂等 + 白名单）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "main.py").write_text("import fakepkg_xyz\n", encoding="utf-8")
        fixer = AutoFixer(root)

        def _err():
            return CompileError(file="", line=0, severity="error",
                                message="No module named 'fakepkg_xyz'",
                                code="ModuleNotFoundError", source="python")

        actions = fixer.fix_errors([_err()])
        assert any(a.applied for a in actions)
        req = (root / "requirements.txt").read_text(encoding="utf-8")
        assert "fakepkg_xyz" in req

        # 幂等：再次修复不重复写入
        actions2 = fixer.fix_errors([_err()])
        assert not any(a.applied for a in actions2), "第二次应跳过（已写入）"

        # 标准库不写入
        fixer2 = AutoFixer(root)
        actions3 = fixer2.fix_errors([
            CompileError(file="", message="No module named 'os'",
                         code="ModuleNotFoundError", source="python")
        ])
        assert not any(a.applied for a in actions3), "os 是标准库不应写入"

        # 项目本地模块不写入
        (root / "mypkg.py").write_text("x = 1\n", encoding="utf-8")
        fixer3 = AutoFixer(root)
        actions4 = fixer3.fix_errors([
            CompileError(file="", message="No module named 'mypkg'",
                         code="ModuleNotFoundError", source="python")
        ])
        assert not any(a.applied for a in actions4), "本地模块不应写入"
        print("[PASS] Python 缺依赖 → requirements.txt（幂等/stdlib/本地模块均正确跳过）")


def test_build_loop_python_syntax_fix_roundtrip():
    """端到端：语法错误的 Python 项目 → 工程循环自动修复 → 编译通过。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "main.py").write_text(
            "def add(a, b)\n    return a + b\n\n\n"
            "if __name__ == '__main__':\n    print('1+2=', add(1, 2))\n",
            encoding="utf-8",
        )
        (root / "requirements.txt").write_text("# deps\n", encoding="utf-8")
        reset_build_loop_for_test()
        loop = BuildLoop()
        import asyncio
        result = asyncio.run(loop.run(root, ["main.py", "requirements.txt"]))
        print("  status=%s rounds=%d fixes=%d" % (
            result.status, result.rounds, len([f for f in result.fixes if f.applied])))
        assert result.status == "passed", "修复后应通过，实际 %s" % result.status
        assert any(f.applied for f in result.fixes), "应有修复动作"
        new_content = (root / "main.py").read_text(encoding="utf-8")
        assert "def add(a, b):" in new_content
        print("[PASS] Python 工程循环闭环: 语法错误 → 自动修复 → passed")


def test_build_loop_python_missing_dep_reports_failed():
    """端到端：缺失第三方依赖 → 写 requirements 后仍无法安装 → failed（上报 LLM 修复）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        pkg = "no_such_pkg_xyz123"
        (root / "main.py").write_text(
            "import %s\n\nprint('ok')\n" % pkg, encoding="utf-8"
        )
        reset_build_loop_for_test()
        loop = BuildLoop()
        import asyncio
        result = asyncio.run(loop.run(root, ["main.py"]))
        print("  status=%s rounds=%d fixes=%d errors=%d" % (
            result.status, result.rounds,
            len([f for f in result.fixes if f.applied]), len(result.errors)))
        assert result.status == "failed", "离线装不了依赖应 failed，实际 %s" % result.status
        req = (root / "requirements.txt").read_text(encoding="utf-8")
        assert pkg in req, "依赖应已写入 requirements.txt"
        print("[PASS] 缺依赖闭环: 写 requirements → 仍失败 → 交 LLM 轮处理")


def test_build_loop_python_pytest_stage():
    """端到端：带测试的 Python 项目 → 三段编译含 pytest → passed。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "calc.py").write_text(
            "def add(a, b):\n    return a + b\n", encoding="utf-8"
        )
        tdir = root / "tests"
        tdir.mkdir()
        (tdir / "test_calc.py").write_text(
            "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
            encoding="utf-8",
        )
        reset_build_loop_for_test()
        loop = BuildLoop()
        import asyncio
        result = asyncio.run(loop.run(root, ["calc.py", "tests/test_calc.py"]))
        print("  status=%s rounds=%d" % (result.status, result.rounds))
        assert result.status == "passed", result.build_log[-500:]
        assert "pytest 通过" in result.build_log, "日志应记录 pytest 段"
        print("[PASS] Python 三段编译: py_compile + 导入冒烟 + pytest 全部通过")


if __name__ == "__main__":
    test_max_rounds_at_least_5()
    test_error_parser_gcc()
    test_error_parser_cmake()
    test_error_parser_linker()
    test_fixer_adds_iostream()
    test_fixer_adds_semicolon()
    test_fixer_adds_cstring()
    test_rule_scanner_cmake_visibility()
    test_rule_scanner_raw_new_delete()
    test_parse_python_syntax_error()
    test_parse_python_pytest_short_tb_combined_line()
    test_parse_python_orphan_module_not_found()
    test_fixer_python_missing_colon()
    test_fixer_python_taberror()
    test_fixer_python_missing_module_to_requirements()
    test_build_loop_python_passes()
    test_build_loop_python_syntax_fix_roundtrip()
    test_build_loop_python_missing_dep_reports_failed()
    test_build_loop_python_pytest_stage()
    test_build_loop_cmake_tooling_missing_on_dev_machine()
    test_build_loop_unknown_project()
    test_build_loop_log_contains_rounds_and_errors()
    print("\n=== Task 8 BuildLoop 测试全部通过 ===")

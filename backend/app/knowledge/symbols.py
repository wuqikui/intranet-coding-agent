"""代码符号图谱 - SQLite 存储，独立于 Chroma。

存储两类对象：
- symbols: 函数/类/文件/全局变量，附签名、docstring、行号区间。
- calls:   调用关系（caller_id → callee_name + 行号）。

解析器：
- parse_python_file: 用 ast 解析 Python，提取函数/类/调用。
- parse_cpp_file:    用正则提取 C++ 函数签名、类名（简化版）。
- index_file:        按扩展名分流。
- index_directory:   遍历目录。

所有对外方法都返回 dict / list[dict]，避免泄露 sqlite3.Row 给上层。
"""
from __future__ import annotations

import ast
import logging
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import get_settings

logger = logging.getLogger("agent.knowledge.symbols")


class SymbolGraph:
    """代码符号图谱 - SQLite 持久化。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        settings = get_settings()
        self._db_path = db_path or settings.SYMBOL_GRAPH_PATH
        # 确保父目录存在
        parent = Path(self._db_path).parent
        parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + 显式锁，允许跨线程访问
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL 提升并发读
        try:
            self._conn.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            pass
        self.init_schema()

    # ---- Schema ----

    def init_schema(self) -> None:
        """建表（幂等）。"""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS symbols (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT NOT NULL,
                    kind        TEXT NOT NULL,
                    file_path   TEXT NOT NULL,
                    line_start  INTEGER NOT NULL,
                    line_end    INTEGER NOT NULL,
                    signature   TEXT NOT NULL DEFAULT '',
                    docstring   TEXT NOT NULL DEFAULT '',
                    channel     TEXT NOT NULL DEFAULT 'code'
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS calls (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    caller_id    INTEGER,
                    callee_name  TEXT NOT NULL,
                    file_path    TEXT NOT NULL,
                    line         INTEGER NOT NULL,
                    FOREIGN KEY (caller_id) REFERENCES symbols(id) ON DELETE SET NULL
                );
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_calls_callee ON calls(callee_name);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_calls_caller ON calls(caller_id);")
            self._conn.commit()

    # ---- 基础写入 ----

    def add_symbol(
        self,
        name: str,
        kind: str,
        file_path: str,
        line_start: int,
        line_end: int,
        signature: str = "",
        docstring: str = "",
    ) -> int:
        """插入一个符号，返回自增 id。"""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO symbols (name, kind, file_path, line_start, line_end, signature, docstring, channel)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'code');
                """,
                (name, kind, file_path, int(line_start), int(line_end), signature, docstring),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def add_call(
        self,
        caller_id: Optional[int],
        callee_name: str,
        file_path: str,
        line: int,
    ) -> None:
        """记录一条调用关系。caller_id 可为 None（顶层调用）。"""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO calls (caller_id, callee_name, file_path, line)
                VALUES (?, ?, ?, ?);
                """,
                (caller_id, callee_name, file_path, int(line)),
            )
            self._conn.commit()

    # ---- 查询 ----

    def find_symbol(self, name: str, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        """按名称精确查找符号（可按 kind 过滤）。"""
        with self._lock:
            if kind is None:
                rows = self._conn.execute(
                    "SELECT * FROM symbols WHERE name = ? ORDER BY line_start;",
                    (name,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM symbols WHERE name = ? AND kind = ? ORDER BY line_start;",
                    (name, kind),
                ).fetchall()
            return [dict(r) for r in rows]

    def find_callers(self, symbol_name: str) -> List[Dict[str, Any]]:
        """谁调用了这个名字的符号。

        JOIN calls → symbols（caller），返回调用者信息 + 调用位置。
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT c.id AS call_id, c.callee_name, c.file_path AS call_file, c.line AS call_line,
                       s.id AS caller_id, s.name AS caller_name, s.kind AS caller_kind,
                       s.file_path AS caller_file, s.line_start AS caller_line_start
                FROM calls c
                LEFT JOIN symbols s ON s.id = c.caller_id
                WHERE c.callee_name = ?
                ORDER BY c.line;
                """,
                (symbol_name,),
            ).fetchall()
            return [dict(r) for r in rows]

    def find_callees(self, caller_name: str) -> List[Dict[str, Any]]:
        """这个符号调用了哪些 callee。"""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT c.id AS call_id, c.callee_name, c.file_path, c.line,
                       s.id AS caller_id, s.name AS caller_name
                FROM calls c
                LEFT JOIN symbols s ON s.id = c.caller_id
                WHERE s.name = ?
                ORDER BY c.line;
                """,
                (caller_name,),
            ).fetchall()
            return [dict(r) for r in rows]

    def search_by_pattern(self, pattern: str) -> List[Dict[str, Any]]:
        """按 SQL LIKE 模式模糊匹配符号名。"""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM symbols
                WHERE name LIKE ?
                ORDER BY kind, name, line_start
                LIMIT 200;
                """,
                (pattern,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ---- 解析器 ----

    def parse_python_file(self, file_path: str) -> Dict[str, int]:
        """用 ast 解析 Python 文件，提取函数/类/调用。

        返回 {"symbols": int, "calls": int}。
        """
        path = Path(file_path)
        try:
            source = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("读取 Python 文件失败 %s: %s", file_path, e)
            return {"symbols": 0, "calls": 0}

        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as e:
            logger.warning("Python 语法错误 %s: %s", file_path, e)
            return {"symbols": 0, "calls": 0}

        sym_count = 0
        call_count = 0

        # 第一遍：定义（建立 name → symbol_id 索引）
        local_sym_ids: Dict[str, int] = {}

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sig = self._python_signature(node)
                doc = ast.get_docstring(node) or ""
                sid = self.add_symbol(
                    name=node.name,
                    kind="function",
                    file_path=str(path),
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno) or node.lineno,
                    signature=sig,
                    docstring=doc,
                )
                local_sym_ids[node.name] = sid
                sym_count += 1
            elif isinstance(node, ast.ClassDef):
                doc = ast.get_docstring(node) or ""
                sid = self.add_symbol(
                    name=node.name,
                    kind="class",
                    file_path=str(path),
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno) or node.lineno,
                    signature=f"class {node.name}",
                    docstring=doc,
                )
                local_sym_ids[node.name] = sid
                sym_count += 1

        # 文件本身也作为一个 file 符号登记
        file_sid = self.add_symbol(
            name=path.name,
            kind="file",
            file_path=str(path),
            line_start=1,
            line_end=source.count("\n") + 1,
            signature=str(path),
            docstring="",
        )
        sym_count += 1

        # 第二遍：调用关系
        # 调用归属最近的 enclosing FunctionDef/AsyncFunctionDef；
        # 没有外层函数时归属文件符号。
        parent_map = self._build_parent_map(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee_name = self._call_name(node.func)
            if not callee_name:
                continue
            enclosing = self._enclosing_function(node, parent_map)
            caller_id = local_sym_ids.get(enclosing.name) if enclosing else file_sid
            self.add_call(
                caller_id=caller_id,
                callee_name=callee_name,
                file_path=str(path),
                line=node.lineno,
            )
            call_count += 1

        return {"symbols": sym_count, "calls": call_count}

    @staticmethod
    def _build_parent_map(tree: ast.AST) -> Dict[int, ast.AST]:
        """构建 node id → parent 的映射，用于回溯 enclosing 节点。"""
        parents: Dict[int, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[id(child)] = parent
        return parents

    @staticmethod
    def _enclosing_function(
        node: ast.AST,
        parent_map: Dict[int, ast.AST],
    ) -> Optional[ast.AST]:
        """沿 parent 链回溯，找最近的 FunctionDef/AsyncFunctionDef。"""
        cur: Optional[ast.AST] = node
        seen = set()
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return cur
            cur = parent_map.get(id(cur))
        return None

    @staticmethod
    def _python_signature(node: ast.FunctionDef) -> str:
        """构造函数签名文本。"""
        args = []
        for a in node.args.args:
            args.append(a.arg)
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        return f"{prefix} {node.name}({', '.join(args)})"

    @staticmethod
    def _call_name(func: ast.expr) -> str:
        """从 Call.func 提取可读的被调用名。"""
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return ""

    def parse_cpp_file(self, file_path: str) -> Dict[str, int]:
        """简化版 C++ 解析：用正则提取函数签名、类名、include。"""
        path = Path(file_path)
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            logger.warning("读取 C++ 文件失败 %s: %s", file_path, e)
            return {"symbols": 0, "calls": 0}

        sym_count = 0
        call_count = 0
        file_sid: Optional[int] = None

        # 类
        for m in re.finditer(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)", source):
            line = source.count("\n", 0, m.start()) + 1
            sid = self.add_symbol(
                name=m.group(1),
                kind="class",
                file_path=str(path),
                line_start=line,
                line_end=line,
                signature=f"class {m.group(1)}",
                docstring="",
            )
            sym_count += 1
            if file_sid is None:
                file_sid = sid

        # 函数定义（简化匹配：返回类型 + name(...) {）
        func_re = re.compile(
            r"\b([A-Za-z_][A-Za-z0-9_:]*)\s+([A-Za-z_][A-Za-z0-9_:]*)\s*\(([^)]*)\)\s*(?:const)?\s*\{"
        )
        for m in func_re.finditer(source):
            ret, name, params = m.group(1), m.group(2), m.group(3)
            # 过滤关键字
            if ret in {"if", "for", "while", "switch", "return", "sizeof"}:
                continue
            if name in {"if", "for", "while", "switch"}:
                continue
            line = source.count("\n", 0, m.start()) + 1
            sid = self.add_symbol(
                name=name,
                kind="function",
                file_path=str(path),
                line_start=line,
                line_end=line,
                signature=f"{ret} {name}({params})",
                docstring="",
            )
            sym_count += 1
            if file_sid is None:
                file_sid = sid

        # 文件符号
        file_sid = file_sid or self.add_symbol(
            name=path.name,
            kind="file",
            file_path=str(path),
            line_start=1,
            line_end=source.count("\n") + 1,
            signature=str(path),
            docstring="",
        )
        sym_count += 1

        # 调用（简化：匹配 ident(...) 模式）
        call_re = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
        for m in call_re.finditer(source):
            name = m.group(1)
            if name in {"if", "for", "while", "switch", "return", "sizeof", "class"}:
                continue
            line = source.count("\n", 0, m.start()) + 1
            self.add_call(
                caller_id=file_sid,
                callee_name=name,
                file_path=str(path),
                line=line,
            )
            call_count += 1

        return {"symbols": sym_count, "calls": call_count}

    def index_file(self, file_path: str) -> Dict[str, int]:
        """按扩展名调用对应解析器。"""
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            logger.warning("索引文件不存在或非文件: %s", file_path)
            return {"symbols": 0, "calls": 0}
        ext = p.suffix.lower()
        if ext == ".py":
            return self.parse_python_file(file_path)
        if ext in {".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh", ".hxx"}:
            return self.parse_cpp_file(file_path)
        # 其他类型暂不索引
        return {"symbols": 0, "calls": 0}

    def index_directory(self, dir_path: str) -> Dict[str, int]:
        """遍历目录索引所有可解析源文件。"""
        root = Path(dir_path)
        if not root.exists() or not root.is_dir():
            logger.warning("索引目录不存在或非目录: %s", dir_path)
            return {"files": 0, "symbols": 0, "calls": 0}

        total_files = 0
        total_symbols = 0
        total_calls = 0
        skip_dirs = {".git", ".venv", "venv", "__pycache__", "node_modules", "build", "dist", ".idea", ".vscode"}

        for cur_path, dirs, files in os.walk(root):
            # 原地修改 dirs 实现剪枝
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in files:
                ext = Path(f).suffix.lower()
                if ext not in {".py", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh", ".hxx"}:
                    continue
                fp = str(Path(cur_path) / f)
                stats = self.index_file(fp)
                total_files += 1
                total_symbols += stats.get("symbols", 0)
                total_calls += stats.get("calls", 0)

        logger.info(
            "索引目录完成 %s: files=%d symbols=%d calls=%d",
            dir_path,
            total_files,
            total_symbols,
            total_calls,
        )
        return {"files": total_files, "symbols": total_symbols, "calls": total_calls}

    # ---- 统计 ----

    def counts(self) -> Dict[str, int]:
        """返回符号与调用关系总数。"""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM symbols;").fetchone()
            symbols = int(row["c"]) if row else 0
            row2 = self._conn.execute("SELECT COUNT(*) AS c FROM calls;").fetchone()
            calls = int(row2["c"]) if row2 else 0
        return {"symbols": symbols, "calls": calls}

    # ---- 维护 ----

    def close(self) -> None:
        """关闭数据库连接。"""
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

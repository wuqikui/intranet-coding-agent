"""知识库统一管理器 - 组合 VectorStore / SymbolGraph / Router / Classifier。

职责：
- ingest: 入库（可选自动重分类；code 通道额外建符号索引）。
- search: 单通道检索。code 通道合并向量检索 + 符号检索。
- route_search: 先按任务类型路由，再单通道检索。
- stats: 各库文档数统计。
- find_code_symbol: 直接查符号图谱。

四库严格隔离：跨库查询直接抛 AppError("禁止跨库混检: ...")。
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.core.exceptions import AppError
from app.knowledge.channels import CHANNEL_DESCRIPTIONS, KnowledgeRouter
from app.knowledge.classifier import DocumentClassifier
from app.knowledge.embedder import Embedder
from app.knowledge.symbols import SymbolGraph
from app.knowledge.vector import CHANNELS, VectorStore

logger = logging.getLogger("agent.knowledge.manager")

# 代码文件扩展名 - 用于决定是否建符号索引
_CODE_EXTS = {".py", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh", ".hxx"}


class KnowledgeBase:
    """四库隔离知识库统一管理器。"""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._vector_store = VectorStore()
        self._symbol_graph = SymbolGraph()
        self._router = KnowledgeRouter()
        self._classifier = DocumentClassifier()
        # 复用 Embedder 仅用于查询向量生成（VectorStore 内部已持有，这里保留独立实例便于扩展）
        self._embedder = Embedder()

    # ---- 入库 ----

    def ingest(
        self,
        channel: str,
        documents: List[Dict[str, Any]],
        auto_classify: bool = False,
    ) -> Dict[str, Any]:
        """入库一批文档。

        Args:
            channel: 目标通道（auto_classify=True 时可能被覆盖）。
            documents: [{"content": str, "filename": str, ...}]。
            auto_classify: True 时用 classifier 重新决定每条文档的归属通道。

        Returns:
            {"ingested": int, "channel": str, "by_channel": {ch: int}}
        """
        if not documents:
            return {"ingested": 0, "channel": channel, "by_channel": {}}

        # 分桶到各 channel
        buckets: Dict[str, List[Dict[str, Any]]] = {ch: [] for ch in CHANNELS}

        for doc in documents:
            content = doc.get("content", "") or ""
            filename = doc.get("filename", "") or ""
            target_ch = channel
            if auto_classify:
                target_ch = self._classifier.classify(content, filename)
            if target_ch not in CHANNELS:
                # 兜底：未知通道回退到入参 channel（如果合法）或 business
                target_ch = channel if channel in CHANNELS else "business"
            buckets[target_ch].append(doc)

        # 实际写入
        by_channel: Dict[str, int] = {}
        total = 0
        primary_channel = channel  # 用于返回值

        for ch, docs in buckets.items():
            if not docs:
                continue
            # 校验合法性（auto_classify 可能产生已知通道）
            if ch not in CHANNELS:
                raise AppError(f"禁止跨库混检: 不允许查询 {ch}")

            contents = [d.get("content", "") or "" for d in docs]
            metadatas = [
                {
                    "filename": d.get("filename", ""),
                    "source": d.get("source", ""),
                    **{k: v for k, v in d.items() if k not in ("content", "filename", "source")},
                }
                for d in docs
            ]
            ids = [str(uuid.uuid4()) for _ in docs]

            # code 通道额外建符号索引
            if ch == "code":
                self._index_code_documents(docs)

            self._vector_store.add(
                channel=ch,
                documents=contents,
                metadatas=metadatas,
                ids=ids,
            )
            by_channel[ch] = by_channel.get(ch, 0) + len(docs)
            total += len(docs)
            if ch == channel:
                primary_channel = ch

        logger.info(
            "入库完成 total=%d by_channel=%s auto_classify=%s",
            total,
            by_channel,
            auto_classify,
        )
        return {
            "ingested": total,
            "channel": primary_channel,
            "by_channel": by_channel,
        }

    def _index_code_documents(self, docs: List[Dict[str, Any]]) -> None:
        """对 code 通道中带 filename 的代码文档建符号索引。"""
        for d in docs:
            filename = d.get("filename", "") or ""
            content = d.get("content", "") or ""
            if not filename:
                continue
            ext = Path(filename).suffix.lower()
            if ext not in _CODE_EXTS:
                continue
            # 优先按真实文件路径索引（若存在）；否则写临时文件再索引
            real_path = Path(filename)
            if real_path.exists() and real_path.is_file():
                try:
                    self._symbol_graph.index_file(str(real_path))
                except Exception as e:
                    logger.warning("符号索引失败 %s: %s", filename, e)
            else:
                # 仅当内容存在时尝试临时索引（.py 才支持）
                if ext == ".py" and content:
                    self._index_python_content(content, filename)

    def _index_python_content(self, source: str, display_path: str) -> None:
        """对内存中的 Python 源代码做符号索引（不落盘）。"""
        import ast

        try:
            tree = ast.parse(source, filename=display_path)
        except SyntaxError as e:
            logger.warning("内存 Python 解析失败 %s: %s", display_path, e)
            return

        # 文件符号
        file_sid = self._symbol_graph.add_symbol(
            name=Path(display_path).name,
            kind="file",
            file_path=display_path,
            line_start=1,
            line_end=source.count("\n") + 1,
            signature=display_path,
            docstring="",
        )

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [a.arg for a in node.args.args]
                prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                sid = self._symbol_graph.add_symbol(
                    name=node.name,
                    kind="function",
                    file_path=display_path,
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno) or node.lineno,
                    signature=f"{prefix} {node.name}({', '.join(args)})",
                    docstring=ast.get_docstring(node) or "",
                )
                # 该函数内部的调用归属到自身
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and sub is not node:
                        callee = self._symbol_graph._call_name(sub.func)  # type: ignore[attr-defined]
                        if callee:
                            self._symbol_graph.add_call(
                                caller_id=sid,
                                callee_name=callee,
                                file_path=display_path,
                                line=sub.lineno,
                            )
            elif isinstance(node, ast.ClassDef):
                self._symbol_graph.add_symbol(
                    name=node.name,
                    kind="class",
                    file_path=display_path,
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno) or node.lineno,
                    signature=f"class {node.name}",
                    docstring=ast.get_docstring(node) or "",
                )

    # ---- 检索 ----

    def search(
        self,
        channel: str,
        query: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """单通道检索。

        code 通道合并向量检索 + 符号检索结果；
        其他通道仅向量检索。
        跨库查询直接抛 AppError。
        """
        if channel not in CHANNELS:
            raise AppError(
                f"禁止跨库混检: 不允许查询 {channel}",
                detail={"allowed": sorted(CHANNELS)},
            )

        # 单库查询；任何跨库意图都不被支持
        # （这里仅做单通道检索，target 显式 = channel 以表明"不允许查别的库"）
        results = self._vector_store.query(channel=channel, query_text=query, n_results=top_k)

        if channel == "code":
            # 额外做符号检索
            sym_results = self._search_code_symbols(query, limit=top_k)
            # 合并：符号在前，向量在后，各保留 top_k，合并后去重并截断
            merged: List[Dict[str, Any]] = []
            seen_contents = set()
            for r in sym_results + results:
                key = r.get("content", "")[:200]
                if key in seen_contents:
                    continue
                seen_contents.add(key)
                merged.append(r)
                if len(merged) >= top_k * 2:
                    break
            return merged
        return results

    def _search_code_symbols(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """从符号图谱里找与查询相关的符号。"""
        out: List[Dict[str, Any]] = []
        # 1. 精确名匹配
        # 把查询拆成候选 token：空格、标点分隔
        import re

        tokens = [t for t in re.split(r"[\s,;:\.()\[\]{}]+", query or "") if t]
        if not tokens:
            return out

        seen_ids = set()
        for tok in tokens:
            # 精确查找
            for sym in self._symbol_graph.find_symbol(tok):
                sid = sym.get("id")
                if sid in seen_ids:
                    continue
                seen_ids.add(sid)
                out.append(self._symbol_to_result(sym, score=1.0))
                if len(out) >= limit:
                    return out
            # 模糊匹配
            pattern = f"%{tok}%"
            for sym in self._symbol_graph.search_by_pattern(pattern):
                sid = sym.get("id")
                if sid in seen_ids:
                    continue
                seen_ids.add(sid)
                out.append(self._symbol_to_result(sym, score=0.5))
                if len(out) >= limit:
                    return out
        return out

    @staticmethod
    def _symbol_to_result(sym: Dict[str, Any], score: float = 0.0) -> Dict[str, Any]:
        """把符号记录转成 search 结果格式。"""
        sig = sym.get("signature", "")
        name = sym.get("name", "")
        content = sig or name
        return {
            "content": content,
            "metadata": {
                "kind": sym.get("kind"),
                "file_path": sym.get("file_path"),
                "line_start": sym.get("line_start"),
                "line_end": sym.get("line_end"),
                "docstring": sym.get("docstring", ""),
                "symbol_id": sym.get("id"),
                "source": "symbol_graph",
            },
            "score": score,
        }

    def route_search(
        self,
        task_type: str,
        query: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """先按任务类型路由，再单通道检索。"""
        channel = self._router.route(task_type)
        # 路由产生的 channel 一定是合法通道，但严格起见再做一次校验
        if channel not in CHANNELS:
            raise AppError(
                f"禁止跨库混检: 路由结果 {channel} 不在允许列表",
                detail={"allowed": sorted(CHANNELS)},
            )
        return self.search(channel=channel, query=query, top_k=top_k)

    # ---- 统计 ----

    def stats(self) -> Dict[str, Any]:
        """各库文档数统计 + 符号库统计。"""
        per_channel: Dict[str, int] = {}
        for ch in sorted(CHANNELS):
            try:
                per_channel[ch] = self._vector_store.count(ch)
            except Exception as e:
                logger.warning("count channel=%s 失败: %s", ch, e)
                per_channel[ch] = 0

        # 符号库统计
        symbol_total = 0
        call_total = 0
        try:
            counts = self._symbol_graph.counts()
            symbol_total = int(counts.get("symbols", 0))
            call_total = int(counts.get("calls", 0))
        except Exception as e:
            logger.warning("符号库统计失败: %s", e)

        return {
            "channels": per_channel,
            "total": sum(per_channel.values()),
            "symbols": symbol_total,
            "calls": call_total,
            "descriptions": dict(CHANNEL_DESCRIPTIONS),
        }

    # ---- 直查符号 ----

    def find_code_symbol(self, name: str) -> List[Dict[str, Any]]:
        """直接查符号图谱。"""
        return self._symbol_graph.find_symbol(name)

    # ---- 暴露内部组件（供 API 层使用） ----

    @property
    def vector_store(self) -> VectorStore:
        return self._vector_store

    @property
    def symbol_graph(self) -> SymbolGraph:
        return self._symbol_graph

    @property
    def router(self) -> KnowledgeRouter:
        return self._router

    @property
    def classifier(self) -> DocumentClassifier:
        return self._classifier

    @property
    def embedder(self) -> Embedder:
        return self._embedder

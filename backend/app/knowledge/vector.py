"""Chroma 向量库操作 - 四库严格隔离。

设计要点：
- 所有向量操作必须显式传入 embedding，禁止使用 Chroma 默认 embedding 函数
  （默认函数会隐式联网下载模型，违反内网离线约束）。
- 四个 channel: business / code / buildops / dataapi，各自独立 collection。
- chromadb 未安装时降级为"仅元数据"模式（不持久化向量），保证 import 不报错；
  但 add/query 仍能工作（query 返回空结果），上层可继续走符号检索兜底。
"""
from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.core.exceptions import AppError
from app.knowledge.embedder import Embedder

logger = logging.getLogger("agent.knowledge.vector")

# chromadb 可能未安装；import 失败时降级
try:  # pragma: no cover - 环境相关
    import chromadb  # type: ignore

    _HAS_CHROMA = True
except Exception:  # pragma: no cover - 库未安装
    chromadb = None  # type: ignore
    _HAS_CHROMA = False


# 四个通道常量
CHANNELS = {"business", "code", "buildops", "dataapi"}


class _InMemoryCollection:
    """chromadb 不可用时的轻量兜底集合。

    仅保存 documents + metadatas，不做向量相似度排序，
    query 时按写入顺序返回前 n_results 条。保证 import 与基础 API 不报错。
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._docs: List[str] = []
        self._metas: List[dict] = []
        self._ids: List[str] = []
        self._lock = threading.Lock()

    def add(
        self,
        documents: List[str],
        embeddings: Optional[List[List[float]]] = None,
        metadatas: Optional[List[dict]] = None,
        ids: Optional[List[str]] = None,
    ) -> None:
        with self._lock:
            metas = metadatas if metadatas is not None else [{} for _ in documents]
            ids_list = ids if ids is not None else [str(uuid.uuid4()) for _ in documents]
            self._docs.extend(documents)
            self._metas.extend(metas)
            self._ids.extend(ids_list)

    def query(
        self,
        query_embeddings: Optional[List[List[float]]] = None,
        n_results: int = 5,
    ) -> Dict[str, List[List[Any]]]:
        with self._lock:
            n = min(n_results, len(self._docs))
            docs = self._docs[:n]
            metas = self._metas[:n]
            # 没有真实相似度，给一个常量 score，避免上层解析报错
            distances = [[0.0] * n]
            return {
                "documents": [docs],
                "metadatas": [metas],
                "distances": distances,
                "ids": [self._ids[:n]],
            }

    def count(self) -> int:
        with self._lock:
            return len(self._docs)


class VectorStore:
    """四库隔离的向量存储。每个 channel 一个独立 collection。"""

    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._embedder = Embedder()
        self._collections: Dict[str, Any] = {}
        self._client: Any = None
        self._lock = threading.Lock()

        persist_path = Path(settings.CHROMA_PERSIST_PATH)
        persist_path.mkdir(parents=True, exist_ok=True)

        if _HAS_CHROMA:
            try:
                # PersistentClient 走本地文件存储，离线可用
                self._client = chromadb.PersistentClient(  # type: ignore[union-attr]
                    path=str(persist_path)
                )
            except Exception as e:
                logger.warning("chromadb PersistentClient 初始化失败, 降级到内存兜底: %s", e)
                self._client = None

        # 为每个 channel 创建/获取 collection
        for ch in CHANNELS:
            self._collections[ch] = self._get_or_create_collection(ch)

        backend = "chromadb" if self._client is not None else "memory-fallback"
        logger.info("VectorStore 初始化完成 (backend=%s, channels=%d)", backend, len(self._collections))

    def _get_or_create_collection(self, channel: str) -> Any:
        """获取或创建一个 channel 的 collection。

        关键：embedding_function=None，禁止 Chroma 默认 embedding 函数，
        所有向量必须由调用方显式传入。
        """
        name = f"kb_{channel}"
        if self._client is not None:
            try:
                # embedding_function 显式置空，避免隐式联网
                return self._client.get_or_create_collection(
                    name=name,
                    embedding_function=None,  # type: ignore[arg-type]
                    metadata={"channel": channel},
                )
            except Exception as e:
                logger.warning("创建 chromadb collection %s 失败, 降级到内存: %s", name, e)
        return _InMemoryCollection(name)

    def _ensure_channel(self, channel: str) -> Any:
        """校验 channel 合法并返回对应 collection。"""
        if channel not in CHANNELS:
            raise AppError(
                f"非法知识库通道: {channel}",
                detail={"allowed": sorted(CHANNELS)},
            )
        return self._collections[channel]

    def add(
        self,
        channel: str,
        documents: List[str],
        metadatas: List[dict],
        ids: Optional[List[str]] = None,
    ) -> None:
        """向指定 channel 写入文档。

        关键：embeddings 由 self._embedder 显式生成后传入，绝不依赖 Chroma 默认。
        """
        if not documents:
            return
        if len(metadatas) != len(documents):
            raise AppError(
                "metadatas 与 documents 长度不一致",
                detail={"docs": len(documents), "metas": len(metadatas)},
            )
        collection = self._ensure_channel(channel)

        # 显式生成 embedding
        embeddings = self._embedder.embed(documents)

        if ids is None:
            ids = [str(uuid.uuid4()) for _ in documents]

        # 不同 chromadb 版本对位置参数 / 关键字参数的要求不同，统一用关键字
        collection.add(
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
            ids=ids,
        )
        logger.info(
            "向量入库 channel=%s count=%d backend=%s",
            channel,
            len(documents),
            type(collection).__name__,
        )

    def query(
        self,
        channel: str,
        query_text: str,
        n_results: int = 5,
    ) -> List[Dict[str, Any]]:
        """在指定 channel 内做向量检索。

        关键：query_embedding 显式生成并传入，禁止 Chroma 默认 embedding。
        """
        collection = self._ensure_channel(channel)
        query_embedding = self._embedder.embed_query(query_text)

        raw = collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
        )

        # 标准化输出为 [{"content", "metadata", "score"}]
        documents_list: List[List[str]] = raw.get("documents", [[]])
        metadatas_list: List[List[dict]] = raw.get("metadatas", [[]])
        distances_list: List[List[float]] = raw.get("distances", [[]])

        results: List[Dict[str, Any]] = []
        if not documents_list:
            return results
        docs = documents_list[0]
        metas = metadatas_list[0] if metadatas_list else []
        dists = distances_list[0] if distances_list else []
        for i, doc in enumerate(docs):
            score = float(dists[i]) if i < len(dists) else 0.0
            meta = metas[i] if i < len(metas) else {}
            results.append(
                {
                    "content": doc,
                    "metadata": meta,
                    "score": score,
                }
            )
        return results

    def count(self, channel: str) -> int:
        """返回指定 channel 的文档数。"""
        collection = self._ensure_channel(channel)
        try:
            return int(collection.count())
        except Exception:
            # 兜底集合直接走 count()
            return int(collection.count())

    def list_channels(self) -> List[str]:
        """返回所有通道名（按字母序）。"""
        return sorted(CHANNELS)

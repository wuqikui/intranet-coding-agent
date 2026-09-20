"""离线 Embedding 生成器 - 多后端 + 自动降级策略。

设计要点：
- 禁止隐式联网。优先尝试 sentence-transformers 本地模型路径。
- 模型路径存在 + 库已安装 → 使用 SentenceTransformer。
- 任一条件不满足 → 降级到 HashingEmbedder（纯 Python、零依赖、断网可用）。
- 同一段文本必须生成相同向量（确定性）。
"""
from __future__ import annotations

import hashlib
import logging
import struct
from pathlib import Path
from typing import Any, List

from app.config import get_settings

logger = logging.getLogger("agent.knowledge.embedder")

# 尝试加载 sentence-transformers，未安装则降级
try:  # pragma: no cover - 环境相关
    from sentence_transformers import SentenceTransformer  # type: ignore

    _HAS_ST = True
except Exception:  # pragma: no cover - 库未安装
    SentenceTransformer = None  # type: ignore
    _HAS_ST = False


class HashingEmbedder:
    """离线兜底 Embedder - 纯 Python、零依赖。

    用 sha256 流式哈希将文本映射为 dim 维浮点向量。
    同一段文本永远生成相同向量（确定性）。
    """

    def __init__(self, dim: int | None = None) -> None:
        settings = get_settings()
        self._dim = dim if dim is not None else settings.EMBEDDING_DIM
        if self._dim <= 0:
            raise ValueError(f"EMBEDDING_DIM 必须为正数, 当前 = {self._dim}")

    def _hash_to_vector(self, text: str) -> List[float]:
        """将文本通过 sha256 流式哈希扩展为 dim 维浮点向量。

        每次 sha256 摘要 32 字节 → 8 个 uint32 → 归一化到 [-1, 1]。
        通过 counter 拓展保证可生成任意长度向量，且完全确定性。
        """
        vec: List[float] = []
        counter = 0
        seed = text.encode("utf-8")
        while len(vec) < self._dim:
            digest = hashlib.sha256(seed + counter.to_bytes(8, "big")).digest()
            # 32 字节按 4 字节切分为 8 个 uint32
            for i in range(0, len(digest) - 3, 4):
                (val,) = struct.unpack("<I", digest[i : i + 4])
                vec.append((val / 0xFFFFFFFF) * 2 - 1)
                if len(vec) >= self._dim:
                    break
            counter += 1
        return vec[: self._dim]

    def embed(self, texts: List[str]) -> List[List[float]]:
        """对每段文本生成 dim 维向量。"""
        return [self._hash_to_vector(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        """单条查询文本向量。"""
        return self._hash_to_vector(text)

    def dim(self) -> int:
        """返回向量维度。"""
        return self._dim


class Embedder:
    """统一 Embedding 入口 - 多后端 + 自动降级。

    优先使用 sentence-transformers 本地模型；不可用则降级到 HashingEmbedder。
    对外暴露统一 API: embed / embed_query / dim。
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._backend_name = "unknown"
        self._model: Any = None
        self._fallback: HashingEmbedder | None = None
        self._dim: int = settings.EMBEDDING_DIM

        model_path = settings.EMBEDDING_MODEL_PATH
        path_ok = bool(model_path) and Path(model_path).exists()

        if _HAS_ST and path_ok:
            try:
                self._model = SentenceTransformer(model_path)  # type: ignore[assignment]
                # 取真实维度
                try:
                    real_dim = self._model.get_sentence_embedding_dimension()  # type: ignore[union-attr]
                    if isinstance(real_dim, int) and real_dim > 0:
                        self._dim = real_dim
                except Exception:
                    # 某些版本不支持，用 settings 维度兜底
                    pass
                self._backend_name = "sentence-transformers"
                logger.info("Embedder 使用 sentence-transformers 后端: %s (dim=%d)", model_path, self._dim)
                return
            except Exception as e:
                logger.warning("sentence-transformers 加载失败, 降级到 HashingEmbedder: %s", e)
                self._model = None

        # 降级路径
        self._fallback = HashingEmbedder(dim=self._dim)
        self._backend_name = "hashing"
        logger.info("Embedder 使用 HashingEmbedder 后端 (dim=%d)", self._dim)

    @property
    def backend(self) -> str:
        """当前使用的后端名称。"""
        return self._backend_name

    def embed(self, texts: List[str]) -> List[List[float]]:
        """生成一组文本的向量。"""
        if self._model is not None:
            try:
                embs = self._model.encode(texts, convert_to_numpy=True)  # type: ignore[union-attr]
                return [list(map(float, row)) for row in embs]
            except Exception as e:
                logger.warning("sentence-transformers encode 失败, 临时降级到 HashingEmbedder: %s", e)
                # 临时降级
                if self._fallback is None:
                    self._fallback = HashingEmbedder(dim=self._dim)
                return self._fallback.embed(texts)
        # 兜底路径
        if self._fallback is None:
            self._fallback = HashingEmbedder(dim=self._dim)
        return self._fallback.embed(texts)

    def embed_query(self, text: str) -> List[float]:
        """单条查询文本向量。"""
        if self._model is not None:
            try:
                emb = self._model.encode([text], convert_to_numpy=True)  # type: ignore[union-attr]
                return list(map(float, emb[0]))
            except Exception as e:
                logger.warning("sentence-transformers encode_query 失败, 临时降级到 HashingEmbedder: %s", e)
                if self._fallback is None:
                    self._fallback = HashingEmbedder(dim=self._dim)
                return self._fallback.embed_query(text)
        if self._fallback is None:
            self._fallback = HashingEmbedder(dim=self._dim)
        return self._fallback.embed_query(text)

    def dim(self) -> int:
        """返回向量维度。"""
        return self._dim

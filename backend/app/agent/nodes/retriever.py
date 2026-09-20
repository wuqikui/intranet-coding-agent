"""Retriever 节点 - 前置知识库检索（code / buildops / dataapi 三通道）+ 风格画像抽取。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.agent.state import AgentState, append_message
from app.agent.style import CodeStyleProfile, detect_style, get_language_default, merge_styles
from . import _common  # noqa: F401  (保持导入位置)

logger = logging.getLogger("agent.nodes.retriever")


def _safe_search(kb: Any, channel: str, query: str, top_k: int = 5) -> list:
    """单通道检索，跨库或异常时返回空列表（不阻断管线）。"""
    try:
        return kb.search(channel=channel, query=query, top_k=top_k) or []
    except Exception as e:  # 包含 AppError(跨库) 在内
        logger.info("知识库检索 channel=%s 失败（降级为空）: %s", channel, e)
        return []


async def retriever_node(state: AgentState) -> Dict[str, Any]:
    """前置知识库检索。

    - code 通道：既有实现 + 风格规范（缩进/命名/注释语言/最大行长/模块化）
    - buildops 通道：构建脚本
    - dataapi 通道：API 契约
    """
    user_query = state.get("user_query", "")
    primary_language = state.get("primary_language", "")

    # 延迟导入：避免 agent 包循环依赖，且 KB 单例来自 knowledge API 模块
    try:
        from app.api.knowledge import get_knowledge_base
        kb = get_knowledge_base()
    except Exception as e:
        logger.warning("知识库不可用，跳过检索: %s", e)
        kb = None

    knowledge_context: Dict[str, Any] = {}
    code_style_dict: Dict[str, Any] = {}

    if kb is not None:
        knowledge_context["code"] = _safe_search(kb, "code", user_query, top_k=5)
        knowledge_context["buildops"] = _safe_search(kb, "buildops", user_query, top_k=3)
        knowledge_context["dataapi"] = _safe_search(kb, "dataapi", user_query, top_k=3)
        # 从 code 命中抽取精确风格画像（Task 9）
        code_style = _build_code_style(knowledge_context["code"], primary_language)
        code_style_dict = code_style.to_dict()
        code_style_dict["source"] = "kb" if knowledge_context["code"] else "language_default"
    else:
        # KB 不可用时回退到语言社区默认
        code_style_dict = get_language_default(primary_language).to_dict()
        code_style_dict["source"] = "language_default"

    total = sum(len(v) for v in knowledge_context.values() if isinstance(v, list))
    msg = "🔍 知识库检索完成：code=%d, buildops=%d, dataapi=%d。风格画像：%s缩进=%s 命名=%s 注释=%s。" % (
        len(knowledge_context.get("code", [])),
        len(knowledge_context.get("buildops", [])),
        len(knowledge_context.get("dataapi", [])),
        "知识库" if code_style_dict.get("source") == "kb" else "默认",
        code_style_dict.get("indent"),
        code_style_dict.get("naming"),
        code_style_dict.get("comment_language"),
    )
    return {
        "knowledge_context": knowledge_context,
        "code_style": code_style_dict,
        "messages": append_message(
            state, msg,
            {"node": "retriever", "hits": total, "code_style_source": code_style_dict.get("source")},
        ),
    }


def _build_code_style(code_hits: List[Dict[str, Any]], language: str) -> CodeStyleProfile:
    """从 code 通道命中抽取风格画像（Task 9）。

    若知识库无命中，回退到语言社区默认（不擅自套用预设风格）。
    """
    if not code_hits:
        return get_language_default(language)
    samples: List[CodeStyleProfile] = []
    for hit in code_hits[:5]:  # 取前 5 个命中
        content = hit.get("content") or ""
        if not content:
            continue
        samples.append(detect_style(content, language))
    if not samples:
        return get_language_default(language)
    return merge_styles(samples, language)

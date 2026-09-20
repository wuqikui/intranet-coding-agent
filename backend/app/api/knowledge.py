"""知识库 API 路由。

- GET  /api/knowledge/channels      列出四库信息
- POST /api/knowledge/ingest         入库
- POST /api/knowledge/search         单通道检索
- POST /api/knowledge/route-search    按任务类型路由后检索
- GET  /api/knowledge/stats          各库统计
- POST /api/knowledge/symbol-search  符号检索

四库严格隔离：跨库查询直接抛 AppError。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.knowledge.channels import CHANNEL_DESCRIPTIONS
from app.knowledge.manager import KnowledgeBase
from app.knowledge.vector import CHANNELS

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


# ---- 单例依赖 ----

@lru_cache
def get_knowledge_base() -> KnowledgeBase:
    """知识库管理器单例。"""
    return KnowledgeBase()


# ---- 请求/响应模型 ----

class IngestDocument(BaseModel):
    content: str = ""
    filename: str = ""
    # 透传其他元数据字段
    source: Optional[str] = None


class IngestRequest(BaseModel):
    channel: str = Field(..., description="目标通道: business/code/buildops/dataapi")
    documents: List[IngestDocument] = Field(default_factory=list)
    auto_classify: bool = Field(False, description="True 时由 classifier 重新决定通道")


class SearchRequest(BaseModel):
    channel: str = Field(..., description="通道名")
    query: str = Field(..., description="查询文本")
    top_k: int = Field(5, ge=1, le=50)


class RouteSearchRequest(BaseModel):
    task_type: str = Field(..., description="任务类型，如 generate_code/build_project/...")
    query: str
    top_k: int = Field(5, ge=1, le=50)


class SymbolSearchRequest(BaseModel):
    name: str = Field(..., description="符号名（精确匹配）")
    kind: Optional[str] = Field(None, description="可选 kind 过滤")


# ---- 路由 ----

@router.get("/channels")
async def list_channels(kb: KnowledgeBase = Depends(get_knowledge_base)):
    """列出四库信息。"""
    return {
        "channels": [
            {"name": ch, "description": desc}
            for ch, desc in CHANNEL_DESCRIPTIONS.items()
        ],
        "allowed": sorted(CHANNELS),
    }


@router.post("/ingest")
async def ingest(
    req: IngestRequest,
    kb: KnowledgeBase = Depends(get_knowledge_base),
):
    """入库一批文档到指定通道（可选 auto_classify）。"""
    # 显式校验 channel（auto_classify=False 时）
    if not req.auto_classify and req.channel not in CHANNELS:
        from app.core.exceptions import AppError

        raise AppError(
            f"禁止跨库混检: 不允许查询 {req.channel}",
            detail={"allowed": sorted(CHANNELS)},
        )

    docs = [d.model_dump() for d in req.documents]
    result = kb.ingest(
        channel=req.channel,
        documents=docs,
        auto_classify=req.auto_classify,
    )
    return result


@router.post("/search")
async def search(
    req: SearchRequest,
    kb: KnowledgeBase = Depends(get_knowledge_base),
):
    """单通道检索。code 通道合并向量 + 符号结果。"""
    results = kb.search(
        channel=req.channel,
        query=req.query,
        top_k=req.top_k,
    )
    return {"channel": req.channel, "query": req.query, "results": results, "count": len(results)}


@router.post("/route-search")
async def route_search(
    req: RouteSearchRequest,
    kb: KnowledgeBase = Depends(get_knowledge_base),
):
    """按任务类型路由后单通道检索。"""
    results = kb.route_search(
        task_type=req.task_type,
        query=req.query,
        top_k=req.top_k,
    )
    channel = kb.router.route(req.task_type)
    return {
        "task_type": req.task_type,
        "routed_channel": channel,
        "query": req.query,
        "results": results,
        "count": len(results),
    }


@router.get("/stats")
async def stats(kb: KnowledgeBase = Depends(get_knowledge_base)):
    """各库统计。"""
    return kb.stats()


@router.post("/symbol-search")
async def symbol_search(
    req: SymbolSearchRequest,
    kb: KnowledgeBase = Depends(get_knowledge_base),
):
    """直接查符号图谱。"""
    symbols = kb.symbol_graph.find_symbol(req.name, kind=req.kind) if req.kind else kb.symbol_graph.find_symbol(req.name)
    return {"name": req.name, "kind": req.kind, "results": symbols, "count": len(symbols)}

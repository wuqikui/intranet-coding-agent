"""网关 API 路由 - 对外暴露模型网关 HTTP 接口。

路由前缀 /api/gateway，包含：
- GET  /models    列出可用模型
- GET  /health    网关健康状态
- GET  /recommend 显存自适应推荐
- POST /chat      chat completions（支持 SSE 流式）
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.exceptions import AppError
from app.gateway.router import get_gateway_router

logger = logging.getLogger("agent.api.gateway")

router = APIRouter(prefix="/api/gateway", tags=["gateway"])


# --- 请求/响应模型 ---

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)
    model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 2048
    stream: bool = False


# --- 路由 ---

@router.get("/models")
async def list_models():
    """列出可用模型。"""
    gw = get_gateway_router()
    models = await gw.list_models()
    return {"data": models}


@router.get("/health")
async def health():
    """网关健康状态。"""
    gw = get_gateway_router()
    return await gw.health()


@router.get("/recommend")
async def recommend(
    gpu_count: int = Query(1, ge=1, description="GPU 数量"),
    vram_per_gpu: float = Query(24.0, ge=0, description="单卡显存（GB）"),
):
    """显存自适应推荐（依据 GPU 数量与单卡显存）。"""
    gw = get_gateway_router()
    return await gw.recommend(gpu_count, vram_per_gpu)


@router.post("/chat")
async def chat(
    req: ChatRequest,
    user_id: str = Query("anonymous", description="调用者用户 ID（待鉴权中间件注入）"),
    role: str = Query("user", description="调用者角色"),
):
    """Chat completions，OpenAI 兼容。

    stream=True 时返回 SSE（text/event-stream，data: JSON 行格式）；
    stream=False 时返回 JSON。
    """
    gw = get_gateway_router()
    # 转为纯 dict 列表交给适配器
    messages = [m.model_dump() for m in req.messages]
    if not messages:
        raise AppError("messages 不能为空", status_code=400)

    model = req.model
    temperature = req.temperature
    max_tokens = req.max_tokens

    if not req.stream:
        # 非流式：返回 JSON
        text = await gw.chat(
            user_id=user_id,
            role=role,
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )
        return {
            "object": "chat.completion",
            "model": model or "mock",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
        }

    # 流式：SSE
    async def _sse_stream():
        gen = await gw.chat(
            user_id=user_id,
            role=role,
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in gen:
            payload = {
                "object": "chat.completion.chunk",
                "model": model or "mock",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": chunk},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        # 结束标记
        yield 'data: [DONE]\n\n'

    return StreamingResponse(
        _sse_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

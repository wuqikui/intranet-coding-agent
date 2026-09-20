"""推理服务（轻量 FastAPI 应用，部署于独立 Docker 容器）。

根据 INFERENCE_BACKEND 环境变量：
- mock:      纯本地模板生成（断网/无 GPU 降级，零网络依赖）
- vllm:      反向代理到 vLLM（OpenAI 兼容）
- llama_cpp: 反向代理到 llama-server（OpenAI 兼容）

对外暴露 OpenAI 兼容接口：
- GET  /health
- GET  /v1/models
- POST /v1/chat/completions（支持 SSE 流式）
"""
import json
import logging
import os
from typing import Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic_settings import BaseSettings

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="[%(asctime)s] %(levelname)-8s %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("inference")


# --- 配置（独立容器，自有 env） ---

class Settings(BaseSettings):
    INFERENCE_BACKEND: str = "mock"  # vllm | llama_cpp | mock
    MODEL_NAME: str = "mock-model"
    INFERENCE_PORT: int = 8001
    # 反向代理上游（真实推理后端地址）
    VLLM_BASE_URL: str = "http://localhost:8000"
    LLAMA_CPP_BASE_URL: str = "http://localhost:8080"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

# 复用 httpx 客户端（反向代理用）
_http = httpx.AsyncClient(
    timeout=httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0),
)

app = FastAPI(
    title="推理服务",
    description="内网离线 AI Coding Agent - 推理网关",
    version="0.1.0",
)


# --- Mock 模板（纯本地，零网络依赖） ---

_MOCK_PROJECT = """\
# ===== 全栈项目骨架（Mock 推理服务生成） =====
# C++ 核心模块 (src/calculator.cpp)
class Calculator:
    def add(a, b): return a + b
# FastAPI 后端
from fastapi import FastAPI
app = FastAPI()
@app.post("/api/calc/add")
def add(req): return {"result": req["a"] + req["b"]}
# React 前端
import React, {useState} from 'react'
export default function App() { ... }
"""

_MOCK_CMAKE = """\
# ===== CMake 构建模板（Mock 推理服务生成） =====
cmake_minimum_required(VERSION 3.16)
project(App VERSION 1.0.0 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
add_library(calculator src/calculator.cpp)
add_executable(app src/main.cpp)
target_link_libraries(app PRIVATE calculator)
"""

_MOCK_FIX = """\
# ===== 修复建议（Mock 推理服务生成） =====
1. 复现错误并确认堆栈
2. 检查最近变更
3. 定位根因：编译/链接/运行时
4. 最小化修复
5. 重新编译 + 测试验证
"""

_MOCK_DEFAULT = "已收到您的请求，当前推理服务运行在 Mock 模式（无 GPU 降级）。"


def _mock_reply(messages: list[dict]) -> str:
    """根据最后一条 user 消息关键词匹配模板。"""
    content = ""
    for m in reversed(messages or []):
        if m.get("role") == "user":
            content = str(m.get("content", ""))
            break
    text = content.lower()
    if any(k in text for k in ("生成", "create", "project")):
        return _MOCK_PROJECT
    if any(k in text for k in ("编译", "build", "cmake")):
        return _MOCK_CMAKE
    if any(k in text for k in ("修复", "fix", "error")):
        return _MOCK_FIX
    return _MOCK_DEFAULT


# --- 路由 ---

@app.get("/health")
async def health():
    """存活探针。"""
    return {"status": "ok", "backend": settings.INFERENCE_BACKEND}


@app.get("/v1/models")
async def list_models():
    """列出可用模型。"""
    if settings.INFERENCE_BACKEND == "mock":
        return {"data": [{"id": "mock-model", "object": "model"}]}
    # 反向代理到真实后端
    upstream = _upstream_url()
    try:
        resp = await _http.get(f"{upstream}/v1/models", timeout=10.0)
        return resp.json()
    except httpx.HTTPError as e:
        logger.warning("列出模型失败: %s", e)
        return {"data": []}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI 兼容 chat completions，支持 SSE 流式。"""
    body = await request.json()
    messages = body.get("messages", [])
    model = body.get("model") or settings.MODEL_NAME
    temperature = body.get("temperature", 0.7)
    max_tokens = body.get("max_tokens", 2048)
    stream = bool(body.get("stream", False))

    backend = settings.INFERENCE_BACKEND

    if backend == "mock":
        # 纯本地模板生成
        text = _mock_reply(messages)
        if not stream:
            return _openai_completion(model, text)
        return StreamingResponse(
            _mock_sse(model, text),
            media_type="text/event-stream",
        )

    # 反向代理到真实后端（vllm / llama_cpp）
    upstream = _upstream_url()
    proxy_body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }

    if not stream:
        try:
            resp = await _http.post(
                f"{upstream}/v1/chat/completions", json=proxy_body
            )
            return JSONResponse(content=resp.json(), status_code=resp.status_code)
        except httpx.HTTPError as e:
            logger.error("上游调用失败: %s", e)
            return JSONResponse(
                status_code=502,
                content={"error": f"推理后端不可用: {e}"},
            )

    # 流式：透传上游 SSE
    async def _proxy_sse():
        try:
            async with _http.stream(
                "POST", f"{upstream}/v1/chat/completions", json=proxy_body
            ) as resp:
                async for line in resp.aiter_lines():
                    if line:
                        yield f"{line}\n"
        except httpx.HTTPError as e:
            logger.error("上游流式失败: %s", e)
            err = json.dumps({"error": str(e)}, ensure_ascii=False)
            yield f"data: {err}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        _proxy_sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


def _upstream_url() -> str:
    """根据 backend 返回真实推理后端地址。"""
    if settings.INFERENCE_BACKEND == "vllm":
        return settings.VLLM_BASE_URL.rstrip("/")
    if settings.INFERENCE_BACKEND == "llama_cpp":
        return settings.LLAMA_CPP_BASE_URL.rstrip("/")
    return ""


def _openai_completion(model: str, text: str) -> JSONResponse:
    """构造非流式 OpenAI 响应。"""
    return JSONResponse(
        content={
            "object": "chat.completion",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
        }
    )


async def _mock_sse(model: str, text: str):
    """Mock 流式：按字符分块产出 SSE。"""
    for ch in text:
        chunk = {
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": ch},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    # 结束标记
    end = {
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [
            {"index": 0, "delta": {}, "finish_reason": "stop"}
        ],
    }
    yield f"data: {json.dumps(end, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=settings.INFERENCE_PORT,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )

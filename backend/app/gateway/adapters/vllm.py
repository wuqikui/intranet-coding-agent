"""vLLM 适配器。

vLLM 暴露 OpenAI 兼容 API（http://vllm-host:8000/v1/chat/completions）。
TP (tensor parallel) 仅在启动 vLLM 服务时通过命令行参数配置，
适配器只发 HTTP 请求，不感知 GPU 拓扑。
"""
import json
import logging
from typing import AsyncIterator, Union

import httpx

from .base import BaseAdapter

logger = logging.getLogger("agent.gateway.vllm")


class VllmAdapter(BaseAdapter):
    """vLLM OpenAI 兼容适配器。"""

    def __init__(self, base_url: str, model: str):
        # base_url 形如 http://vllm-host:8000
        self._base_url = base_url.rstrip("/")
        self._model = model
        # 复用连接，超时设宽以支持长上下文推理
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0),
        )

    async def chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
        max_tokens: int,
        stream: bool,
    ) -> Union[str, AsyncIterator[str]]:
        """POST /v1/chat/completions，返回完整文本或流式生成器。"""
        payload = {
            "model": model or self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

        if not stream:
            # 非流式：一次性返回完整内容
            resp = await self._client.post("/v1/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

        # 流式：解析 SSE data: 行
        async def _stream() -> AsyncIterator[str]:
            async with self._client.stream(
                "POST", "/v1/chat/completions", json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    # 形如 "data: {...}"
                    data_str = line[len("data:"):].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk["choices"][0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        # 跳过无法解析的心跳/注释行
                        continue

        return _stream()

    async def health(self) -> bool:
        """GET /health，返回 200 即视为可用。"""
        try:
            resp = await self._client.get("/health", timeout=5.0)
            return resp.status_code == 200
        except httpx.HTTPError as e:
            logger.warning("vLLM 健康检查失败: %s", e)
            return False

    async def list_models(self) -> list[dict]:
        """GET /v1/models，返回 OpenAI 格式模型列表。"""
        try:
            resp = await self._client.get("/v1/models", timeout=10.0)
            resp.raise_for_status()
            return resp.json().get("data", [])
        except httpx.HTTPError as e:
            logger.warning("vLLM 列出模型失败: %s", e)
            return []

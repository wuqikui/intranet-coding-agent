"""模型网关路由 - 统一入口。

封装限流 / 路由 / 重试 / 审计日志。
通过 settings.INFERENCE_BACKEND 切换底层适配器，对上层暴露统一接口。
"""
import asyncio
import logging
import time
from typing import AsyncIterator, Optional, Union

from app.config import get_settings
from app.deps import get_audit_logger
from .adapters.base import BaseAdapter
from .adapters.mock import MockAdapter
from .adapters.vllm import VllmAdapter
from .adapters.llama_cpp import LlamaCppAdapter
from .limiter import ConcurrencyLimiter
from . import vram

logger = logging.getLogger("agent.gateway.router")

# 失败重试：最多 3 次重试（不含首次调用），指数退避 1s / 2s / 4s
MAX_RETRIES = 3
RETRY_DELAYS = (1, 2, 4)


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数（中英混合，约 1 token ≈ 2 字符）。"""
    if not text:
        return 0
    return max(1, len(text) // 2)


class GatewayRouter:
    """模型网关路由器。

    通过 settings.INFERENCE_BACKEND 创建对应适配器：
    - mock: 直接使用本地 MockAdapter（零网络依赖，断网降级）
    - vllm: 指向推理服务（由推理服务反向代理到 vLLM）
    - llama_cpp: 指向推理服务（由推理服务反向代理到 llama-server）
    """

    def __init__(self):
        settings = get_settings()
        self._backend = settings.INFERENCE_BACKEND
        self._model = settings.MODEL_NAME

        if self._backend == "mock":
            # Mock 直接本地，零网络依赖
            self._adapter: BaseAdapter = MockAdapter()
            self._base_url = None
        elif self._backend == "vllm":
            # 推理服务统一入口（反向代理到真实 vLLM）
            self._base_url = f"http://localhost:{settings.INFERENCE_PORT}"
            self._adapter = VllmAdapter(self._base_url, self._model)
        elif self._backend == "llama_cpp":
            self._base_url = f"http://localhost:{settings.INFERENCE_PORT}"
            self._adapter = LlamaCppAdapter(self._base_url, self._model)
        else:
            # 未知后端降级为 mock，保证可用
            logger.warning("未知 INFERENCE_BACKEND=%s，降级为 mock", self._backend)
            self._backend = "mock"
            self._adapter = MockAdapter()
            self._base_url = None

        # 并发限流（0 = 不限）
        self._limiter = ConcurrencyLimiter(settings.MAX_CONCURRENT_REQUESTS)
        # 审计日志器
        self._audit = get_audit_logger()

    async def chat(
        self,
        user_id: str,
        role: str,
        messages: list[dict],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stream: bool = False,
    ) -> Union[str, AsyncIterator[str]]:
        """调用 LLM：限流 → 适配器 → 重试 → 审计日志。

        stream=True 时返回异步生成器（逐块产出文本）。
        """
        use_model = model or self._model

        if stream:
            # 流式：建立流时重试；流建立后由包装器在结束时写审计
            gen = await self._call_with_retry(
                messages, use_model, temperature, max_tokens, stream=True
            )
            return self._audited_stream(
                gen, user_id, role, use_model, messages
            )

        # 非流式：完整重试，结束后写审计
        start = time.perf_counter()
        result = await self._call_with_retry(
            messages, use_model, temperature, max_tokens, stream=False
        )
        duration_ms = int((time.perf_counter() - start) * 1000)
        self._write_audit(
            user_id=user_id,
            role=role,
            model=use_model,
            messages=messages,
            output=result or "",
            duration_ms=duration_ms,
        )
        return result

    async def _call_with_retry(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
        max_tokens: int,
        stream: bool,
    ) -> Union[str, AsyncIterator[str]]:
        """带重试的适配器调用（指数退避 1/2/4s，最多 3 次重试）。"""
        last_exc: Optional[Exception] = None
        # 首次 + 最多 MAX_RETRIES 次重试
        for attempt in range(MAX_RETRIES + 1):
            try:
                async with self._limiter:
                    return await self._adapter.chat(
                        messages, model, temperature, max_tokens, stream
                    )
            except Exception as e:
                last_exc = e
                if attempt >= MAX_RETRIES:
                    break
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                logger.warning(
                    "LLM 调用失败 (attempt=%d/%d, backend=%s): %s，%ss 后重试",
                    attempt + 1, MAX_RETRIES + 1, self._backend, e, delay,
                )
                await asyncio.sleep(delay)
        # 全部重试耗尽
        raise last_exc  # type: ignore[misc]

    async def _audited_stream(
        self,
        gen: AsyncIterator[str],
        user_id: str,
        role: str,
        model: str,
        messages: list[dict],
    ) -> AsyncIterator[str]:
        """包装流式生成器，在流结束时写审计日志。"""
        collected: list[str] = []
        start = time.perf_counter()
        try:
            async for chunk in gen:
                collected.append(chunk)
                yield chunk
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            self._write_audit(
                user_id=user_id,
                role=role,
                model=model,
                messages=messages,
                output="".join(collected),
                duration_ms=duration_ms,
            )

    def _write_audit(
        self,
        *,
        user_id: str,
        role: str,
        model: str,
        messages: list[dict],
        output: str,
        duration_ms: int,
    ) -> None:
        """写一条 llm_call 审计记录（失败不阻断主流程）。"""
        prompt = ""
        tokens_in = 0
        try:
            for m in messages:
                c = m.get("content", "")
                if isinstance(c, str):
                    prompt += c + "\n"
                    tokens_in += _estimate_tokens(c)
        except Exception:
            pass
        tokens_out = _estimate_tokens(output)
        try:
            self._audit.log(
                user_id=user_id,
                role=role,
                action="llm_call",
                prompt=prompt[:2000],
                model=model,
                duration_ms=duration_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
        except Exception as e:
            # 审计失败不应阻断推理结果返回
            logger.error("写审计日志失败: %s", e)

    async def list_models(self) -> list[dict]:
        """代理适配器列出可用模型。"""
        try:
            return await self._adapter.list_models()
        except Exception as e:
            logger.warning("列出模型失败: %s", e)
            return []

    async def health(self) -> dict:
        """网关健康状态: {backend, healthy, model}。"""
        try:
            healthy = await self._adapter.health()
        except Exception as e:
            logger.warning("健康检查异常: %s", e)
            healthy = False
        return {
            "backend": self._backend,
            "healthy": bool(healthy),
            "model": self._model,
        }

    async def recommend(self, gpu_count: int, vram_per_gpu: float) -> dict:
        """显存自适应推荐，委托 vram.recommend_model。"""
        return vram.recommend_model(gpu_count, vram_per_gpu)


# 模块级单例工厂（仿 get_settings / get_audit_logger 模式）
_router_instance: Optional[GatewayRouter] = None


def get_gateway_router() -> GatewayRouter:
    """获取 GatewayRouter 单例。"""
    global _router_instance
    if _router_instance is None:
        _router_instance = GatewayRouter()
    return _router_instance

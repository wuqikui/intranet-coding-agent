"""推理后端适配器基类。

所有适配器（vLLM / llama.cpp / Mock）实现统一接口，
由 GatewayRouter 通过 settings.INFERENCE_BACKEND 切换。
"""
from abc import ABC, abstractmethod
from typing import AsyncIterator, Union


class BaseAdapter(ABC):
    """推理后端适配器抽象基类。"""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
        max_tokens: int,
        stream: bool,
    ) -> Union[str, AsyncIterator[str]]:
        """OpenAI 兼容的 chat 接口。

        Args:
            messages: OpenAI 格式消息列表 [{"role": ..., "content": ...}]
            model: 模型名
            temperature: 采样温度
            max_tokens: 最大生成 token 数
            stream: True 时返回异步生成器逐块产出文本，
                    False 时返回完整字符串。

        Returns:
            完整字符串（stream=False）或异步生成器（stream=True）。
        """

    @abstractmethod
    async def health(self) -> bool:
        """检查推理后端是否可用。"""

    @abstractmethod
    async def list_models(self) -> list[dict]:
        """返回可用模型列表（OpenAI /v1/models 格式）。"""

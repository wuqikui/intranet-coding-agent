"""并发限流 - 基于 asyncio.Semaphore。

从 settings.MAX_CONCURRENT_REQUESTS 读取限制（0 = 不限）。
支持 async with 上下文管理器与显式 acquire/release 两种用法。
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

logger = logging.getLogger("agent.gateway.limiter")


class ConcurrencyLimiter:
    """异步并发限流器。

    max_concurrent=0 表示不限制（直通）。
    """

    def __init__(self, max_concurrent: int):
        self._max = max_concurrent
        # 0 时用一个足够大的值模拟"不限"
        self._effective = max_concurrent if max_concurrent > 0 else 2**31
        # 延迟创建：Python 3.9 的 Semaphore 在构造时需要事件循环，
        # 在异步上下文（首次 acquire）中创建可兼容所有版本
        self._semaphore = None

    def _ensure_semaphore(self) -> "asyncio.Semaphore":
        """首次使用时创建 Semaphore（此时已在事件循环中）。"""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._effective)
        return self._semaphore

    @property
    def max_concurrent(self) -> int:
        """配置的最大并发数（0=不限）。"""
        return self._max

    async def acquire(self) -> None:
        """获取一个并发槽位（会阻塞直到有空位）。"""
        await self._ensure_semaphore().acquire()

    async def release(self) -> None:
        """释放一个并发槽位。"""
        if self._semaphore is not None:
            self._semaphore.release()

    async def __aenter__(self) -> "ConcurrencyLimiter":
        await self._ensure_semaphore().acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._semaphore is not None:
            self._semaphore.release()

    @asynccontextmanager
    async def slot(self) -> AsyncIterator["ConcurrencyLimiter"]:
        """显式上下文管理器别名。"""
        async with self._ensure_semaphore():
            yield self

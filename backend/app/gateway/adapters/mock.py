"""Mock Provider - 离线降级适配器。

在无 GPU、断网时仍能演示 Agent 全链路。
不调用任何外部服务，纯本地模板生成。
"""
import logging
from typing import AsyncIterator, Union

from .base import BaseAdapter

logger = logging.getLogger("agent.gateway.mock")


# --- 模板字符串（纯本地，零网络依赖）---

_TEMPLATE_PROJECT = """\
# ===== 全栈项目骨架（Mock 模式生成） =====

# --- C++ 核心计算模块 (src/calculator.cpp) ---
#include <iostream>
#include <vector>
#include <string>

class Calculator {
public:
    double add(double a, double b) { return a + b; }
    double sub(double a, double b) { return a - b; }
    double mul(double a, double b) { return a * b; }
    double div(double a, double b) {
        if (b == 0) throw std::runtime_error("除零错误");
        return a / b;
    }
};

extern "C" double calc_add(double a, double b) {
    return Calculator().add(a, b);
}
// 文件结束

# --- FastAPI 后端 (backend/main.py) ---
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Mock Backend")
app.add_middleware(CORSMiddleware, allow_origins=["*"])

class CalcReq(BaseModel):
    a: float
    b: float

@app.post("/api/calc/add")
def calc_add(req: CalcReq):
    return {"result": req.a + req.b}
# 文件结束

# --- React 前端 (frontend/src/App.jsx) ---
import React, { useState } from 'react';

export default function App() {
    const [a, setA] = useState(0);
    const [b, setB] = useState(0);
    const [result, setResult] = useState(null);

    const onAdd = async () => {
        const resp = await fetch('/api/calc/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({a: Number(a), b: Number(b)}),
        });
        const data = await resp.json();
        setResult(data.result);
    };

    return (
        <div>
            <input value={a} onChange={e => setA(e.target.value)} />
            <input value={b} onChange={e => setB(e.target.value)} />
            <button onClick={onAdd}>加法</button>
            {result !== null && <p>结果: {result}</p>}
        </div>
    );
}
# 文件结束
"""

_TEMPLATE_CMAKE = """\
# ===== CMake 构建模板（Mock 模式生成） =====
cmake_minimum_required(VERSION 3.16)
project(CalculatorApp VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# 构建选项
option(BUILD_TESTS "构建测试" ON)
option(BUILD_SHARED "构建为共享库" OFF)

# 核心库
add_library(calculator src/calculator.cpp)
target_include_directories(calculator PUBLIC include)

# 可执行文件
add_executable(calc_app src/main.cpp)
target_link_libraries(calc_app PRIVATE calculator)

# 测试
if(BUILD_TESTS)
    enable_testing()
    add_subdirectory(tests)
endif()

# 安装规则
install(TARGETS calculator calc_app
        RUNTIME DESTINATION bin
        LIBRARY DESTINATION lib
        ARCHIVE DESTINATION lib)
install(DIRECTORY include/ DESTINATION include)
# 文件结束
"""

_TEMPLATE_FIX = """\
# ===== 修复建议（Mock 模式生成） =====

诊断步骤:
1. 复现错误，确认错误堆栈与触发条件
2. 检查最近一次变更（git diff / 构建日志）
3. 定位根因：
   - 编译错误：检查头文件包含、符号可见性、C++ 标准版本
   - 链接错误：检查 target_link_libraries、库路径
   - 运行时错误：检查空指针、除零、数组越界、资源释放顺序
4. 应用最小化修复，避免扩大变更范围
5. 重新编译 + 跑测试验证

常见修复模板:
- 未定义引用 → 确认目标链接库: target_link_libraries(xxx PRIVATE <lib>)
- 段错误 → 在可疑指针访问前增加判空: if (p == nullptr) return;
- 内存泄漏 → 使用 RAII 或智能指针管理资源生命周期
- 单元测试失败 → 先让测试可复现，再修源码而非测试

建议在修复后执行:
    cmake --build build && ctest --test-dir build --output-on-failure
# 文件结束
"""

_TEMPLATE_DEFAULT = "已收到您的请求，当前运行在 Mock 模式（无 GPU 降级）。"


def _pick_template(content: str) -> str:
    """根据用户最后一条消息的关键词匹配模板。"""
    if not content:
        return _TEMPLATE_DEFAULT
    text = content.lower()
    # 生成 / create / project → 全栈项目骨架
    if any(k in text for k in ("生成", "create", "project")):
        return _TEMPLATE_PROJECT
    # 编译 / build / cmake → CMake 模板
    if any(k in text for k in ("编译", "build", "cmake")):
        return _TEMPLATE_CMAKE
    # 修复 / fix / error → 修复建议
    if any(k in text for k in ("修复", "fix", "error")):
        return _TEMPLATE_FIX
    return _TEMPLATE_DEFAULT


class MockAdapter(BaseAdapter):
    """Mock 推理适配器 - 离线纯本地模板生成。"""

    def __init__(self):
        # 无任何外部依赖
        pass

    async def chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
        max_tokens: int,
        stream: bool,
    ) -> Union[str, AsyncIterator[str]]:
        """根据 messages 最后一条 user content 的关键词匹配模板返回。"""
        # 取最后一条 user 消息的内容
        last_user_content = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user_content = msg.get("content", "")
                break

        text = _pick_template(last_user_content)

        if not stream:
            return text

        # 流式：按字符分块 yield，模拟逐 token 输出
        async def _stream() -> AsyncIterator[str]:
            for ch in text:
                yield ch

        return _stream()

    async def health(self) -> bool:
        """Mock 模式始终可用。"""
        return True

    async def list_models(self) -> list[dict]:
        """返回单一 mock 模型。"""
        return [{"id": "mock-model", "object": "model"}]

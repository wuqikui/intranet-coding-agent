"""自动分类器 - 按 文件名 + 内容 关键词把文档路由到四个通道。

规则优先级（先匹配先返回）：
1. 文件名扩展名 / 关键词命中 buildops
2. 文件名扩展名 / 关键词命中 code
3. 文件名扩展名 / 关键词命中 dataapi
4. 内容关键词命中 business
5. 内容关键词命中 dataapi
6. 内容关键词命中 code
7. 内容关键词命中 buildops
8. 默认 business

对外暴露：classify / classify_batch。
"""
from __future__ import annotations

import re
from typing import Dict, List

# 文件名特征（小写匹配）
_BUILDOPS_FILE_PATTERNS = (
    "cmakelists",
    "makefile",
    "dockerfile",
)
_BUILDOPS_EXT = (".yml", ".yaml", ".sh", ".toml")

_CODE_EXT = (".py", ".cpp", ".h", ".c", ".ts", ".tsx", ".js", ".cc", ".hpp", ".hh", ".hxx", ".cxx")

_DATAAPI_FILE_PATTERNS = ("openapi", "swagger")
_DATAAPI_EXT = (".sql", ".graphql", ".proto")

# 内容关键词
_BUSINESS_KEYWORDS = ("SOP", "流程", "规范", "业务", "用户故事")
_DATAAPI_CONTENT_KEYWORDS = ("CREATE TABLE", "API", "接口", "endpoint")
_CODE_CONTENT_KEYWORDS = ("def ", "class ", "#include")
_BUILDOPS_CONTENT_KEYWORDS = ("FROM ", "RUN ", "cmake_")


class DocumentClassifier:
    """文档自动分类器。"""

    @staticmethod
    def _filename_key(filename: str) -> str:
        """取文件名小写形式（含扩展名）。"""
        return (filename or "").strip().lower()

    def classify(self, content: str, filename: str = "") -> str:
        """对单条文档分类，返回 channel 名。"""
        fname = self._filename_key(filename)
        text = content or ""

        # ---- 规则 1: buildops（文件名特征） ----
        if any(p in fname for p in _BUILDOPS_FILE_PATTERNS):
            return "buildops"
        if fname.endswith(_BUILDOPS_EXT):
            return "buildops"

        # ---- 规则 2: code（文件名扩展名） ----
        if fname.endswith(_CODE_EXT):
            return "code"

        # ---- 规则 3: dataapi（文件名特征 + 扩展名） ----
        if any(p in fname for p in _DATAAPI_FILE_PATTERNS):
            return "dataapi"
        if fname.endswith(_DATAAPI_EXT):
            return "dataapi"

        # ---- 规则 4: business 内容 ----
        if any(k in text for k in _BUSINESS_KEYWORDS):
            return "business"

        # ---- 规则 5: dataapi 内容 ----
        if any(k in text for k in _DATAAPI_CONTENT_KEYWORDS):
            return "dataapi"

        # ---- 规则 6: code 内容 ----
        if any(k in text for k in _CODE_CONTENT_KEYWORDS):
            return "code"

        # ---- 规则 7: buildops 内容 ----
        if any(k in text for k in _BUILDOPS_CONTENT_KEYWORDS):
            return "buildops"

        # ---- 默认 ----
        return "business"

    def classify_batch(self, items: List[Dict[str, str]]) -> List[Dict[str, object]]:
        """批量分类。

        每项形如 {"content": str, "filename": str}，返回带 channel 字段的副本。
        原始字段（content/filename）原样保留。
        """
        out: List[Dict[str, object]] = []
        for item in items:
            content = item.get("content", "") or ""
            filename = item.get("filename", "") or ""
            channel = self.classify(content, filename)
            merged: Dict[str, object] = dict(item)
            merged["channel"] = channel
            out.append(merged)
        return out

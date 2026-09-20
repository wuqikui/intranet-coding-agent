"""四库路由 - 按任务类型 / 关键词路由到对应知识通道。

通道：
- business: 公司成熟业务文档、SOP、内部术语表、用户故事
- code:     公司既有代码仓库、编码规范、架构蓝图、ADR
- buildops: CMakeLists、Dockerfile、Makefile、CI 流水线、部署脚本
- dataapi:  数据库 Schema、API 设计文档、HTTP 契约
"""
from __future__ import annotations

from typing import Dict, List

# 四通道描述（对外暴露）
CHANNEL_DESCRIPTIONS: Dict[str, str] = {
    "business": "公司成熟业务文档、SOP、内部术语表、用户故事",
    "code": "公司既有代码仓库、编码规范、架构蓝图、ADR",
    "buildops": "CMakeLists、Dockerfile、Makefile、CI 流水线、部署脚本",
    "dataapi": "数据库 Schema、API 设计文档、HTTP 契约",
}

# 任务类型 → 通道
_TASK_ROUTE: Dict[str, str] = {
    "generate_code": "code",
    "search_implementation": "code",
    "check_style": "code",
    "build_project": "buildops",
    "api_design": "dataapi",
    "business_query": "business",
}

# 关键词 → 通道（降级方案）
_KEYWORD_ROUTE: Dict[str, str] = {
    # business
    "sop": "business",
    "流程": "business",
    "规范": "business",
    "业务": "business",
    "用户故事": "business",
    "需求": "business",
    # code
    "代码": "code",
    "函数": "code",
    "类": "code",
    "实现": "code",
    "风格": "code",
    "架构": "code",
    "adr": "code",
    # buildops
    "cmake": "buildops",
    "dockerfile": "buildops",
    "makefile": "buildops",
    "ci": "buildops",
    "部署": "buildops",
    "流水线": "buildops",
    "构建": "buildops",
    # dataapi
    "schema": "dataapi",
    "api": "dataapi",
    "接口": "dataapi",
    "endpoint": "dataapi",
    "graphql": "dataapi",
    "proto": "dataapi",
    "sql": "dataapi",
}


class KnowledgeRouter:
    """知识库通道路由器。"""

    def route(self, task_type: str) -> str:
        """按任务类型路由到对应通道。

        未知 task_type 默认路由到 business（保守选择，避免误入 code 库）。
        """
        if not task_type:
            return "business"
        key = task_type.strip().lower()
        return _TASK_ROUTE.get(key, "business")

    def route_by_keywords(self, keywords: List[str]) -> str:
        """按关键词路由（降级方案）。

        统计每个通道命中的关键词数，命中最多的胜出；并列时按
        business < code < buildops < dataapi 的固定优先级取舍。
        """
        if not keywords:
            return "business"

        scores: Dict[str, int] = {ch: 0 for ch in CHANNEL_DESCRIPTIONS}
        for kw in keywords:
            if not kw:
                continue
            k = kw.strip().lower()
            if not k:
                continue
            ch = _KEYWORD_ROUTE.get(k)
            if ch:
                scores[ch] += 1
            else:
                # 子串包含匹配
                for route_kw, ch in _KEYWORD_ROUTE.items():
                    if route_kw in k or k in route_kw:
                        scores[ch] += 1
                        break

        # 选最高分；同分时按固定优先级
        priority = ["business", "code", "buildops", "dataapi"]
        best = "business"
        best_score = -1
        for ch in priority:
            if scores.get(ch, 0) > best_score:
                best = ch
                best_score = scores[ch]
        return best

    def get_all_channels(self) -> Dict[str, str]:
        """返回所有通道描述。"""
        return dict(CHANNEL_DESCRIPTIONS)

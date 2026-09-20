"""出网维护模式守卫 - 运行时出口流量阻断。

设计：
- settings.MAINTENANCE_MODE 是初始状态来源（"true"/"false" 字符串）。
- 由于 config 用 lru_cache 缓存，运行时无法直接修改 settings；
  采用一个独立的 flag 文件（data/maintenance.flag）持久化运行时切换结果。
- 进程启动时优先读 flag 文件；不存在则回退到 settings。
- 单进程内 EgressGuard 单例持有内存状态，避免每次 IO。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from app.audit.logger import AuditLogger
from app.config import get_settings
from app.core.exceptions import AppError


class MaintenanceModeRequiredError(AppError):
    """非维护模式下尝试外网访问时抛出。"""

    def __init__(self, message: str = "出网维护模式未开启：运行时禁止外网访问"):
        super().__init__(message, status_code=403)


# 视为内网/本地的 host（不视为外网出口）
_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1", "0.0.0.0"}


class EgressGuard:
    """出网守卫：非维护模式下阻断所有外网请求，维护模式下放行并写审计。

    维护状态持久化到 data/maintenance.flag，跨进程保留。
    """

    def __init__(self, audit: AuditLogger):
        self._audit = audit
        self._lock = threading.Lock()
        settings = get_settings()
        # flag 文件放在 AUDIT_LOG_PATH 的父目录（通常为 ./data）
        self._flag_file = Path(settings.AUDIT_LOG_PATH).parent / "maintenance.flag"
        self._flag_file.parent.mkdir(parents=True, exist_ok=True)
        self._state: Optional[bool] = None
        self._load_state()

    # ---- 内部方法 ----

    def _load_state(self) -> None:
        """从 flag 文件加载状态；不存在则回退到 settings。"""
        try:
            if self._flag_file.exists():
                content = self._flag_file.read_text(encoding="utf-8").strip().lower()
                self._state = content == "true"
                return
        except OSError:
            # 读取失败时回退到 settings
            pass
        self._state = (get_settings().MAINTENANCE_MODE or "false").strip().lower() == "true"

    def _persist(self) -> None:
        """将当前状态写入 flag 文件。"""
        try:
            self._flag_file.write_text(
                "true" if self._state else "false", encoding="utf-8"
            )
        except OSError:
            # 写入失败不应阻断业务，但记录到审计
            self._audit.log(
                user_id="system",
                role="egress-guard",
                action="maintenance_flag_persist_failed",
                extra={"flag_file": str(self._flag_file)},
            )

    @staticmethod
    def _is_external_url(url: str) -> bool:
        """判定 URL 是否为外网（host 非空且非 loopback）。"""
        try:
            parsed = urlparse(url)
        except ValueError:
            return True  # 无法解析的 URL 视为外网风险
        host = (parsed.hostname or "").lower()
        return host not in _LOCAL_HOSTS

    # ---- 公开 API ----

    def is_maintenance_active(self) -> bool:
        """当前是否处于出网维护模式。"""
        with self._lock:
            if self._state is None:
                self._load_state()
            return bool(self._state)

    def check_egress(self, url: str) -> None:
        """检查外网访问是否被允许。

        - 内网/本地 URL：直接放行，不审计。
        - 维护模式开启：放行，并写审计。
        - 维护模式关闭：对外网 URL 抛 MaintenanceModeRequiredError。
        """
        if not self._is_external_url(url):
            # 内网/本地 URL 不受出口阻断影响
            return

        if not self.is_maintenance_active():
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            raise MaintenanceModeRequiredError(
                f"出网维护模式未开启：禁止访问外网 {host}"
            )

        # 维护模式：放行并审计
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        self._audit.log(
            user_id="system",
            role="egress-guard",
            action="egress_allowed",
            extra={"url": url, "host": host},
        )

    def enable_maintenance(self, user_id: str, role: str) -> None:
        """开启出网维护模式（管理员操作）。"""
        with self._lock:
            self._state = True
            self._persist()
        self._audit.log(
            user_id=user_id,
            role=role,
            action="maintenance_enable",
            extra={"flag_file": str(self._flag_file)},
        )

    def disable_maintenance(self, user_id: str, role: str) -> None:
        """关闭出网维护模式（管理员操作）。"""
        with self._lock:
            self._state = False
            self._persist()
        self._audit.log(
            user_id=user_id,
            role=role,
            action="maintenance_disable",
            extra={"flag_file": str(self._flag_file)},
        )


# ---- 全局单例 ----

_egress_guard: Optional[EgressGuard] = None


def get_egress_guard() -> EgressGuard:
    """获取 EgressGuard 单例。"""
    global _egress_guard
    if _egress_guard is None:
        # 延迟导入避免循环依赖
        from app.deps import get_audit_logger
        _egress_guard = EgressGuard(get_audit_logger())
    return _egress_guard


def reset_egress_guard_for_test() -> None:
    """仅用于测试：重置单例，使下一次 get_egress_guard() 重新加载状态。"""
    global _egress_guard
    _egress_guard = None

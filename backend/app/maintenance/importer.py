"""离线导入器 - 临时联网下载 → sha256 校验 → 入内网模型库/包仓库。

流程：
1. 校验 expected_sha256 非空。
2. 调用 EgressGuard.check_egress(url) - 维护模式未开启则抛 MaintenanceModeRequiredError。
3. httpx 流式下载到 .part 临时文件，边下边算 sha256。
4. sha256 不匹配：删除临时文件 + 写审计 + 抛 AppError("sha256 校验失败")。
5. 匹配：移动到目标路径（model -> MODEL_REPO_PATH/<filename>；
   package -> PACKAGE_REPO_PATH/<filename>）+ 写审计。
6. 返回 {path, sha256, size_bytes, kind}。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.audit.logger import AuditLogger
from app.config import get_settings
from app.core.exceptions import AppError
from app.maintenance.guard import get_egress_guard

# 下载流式分片大小：64 KiB
_CHUNK_SIZE = 64 * 1024

# 下载总超时（秒）- 单次 chunk 读取最大等待
_DOWNLOAD_TIMEOUT = 300.0


class ModelImporter:
    """离线模型/包导入器。

    所有 httpx 请求必须先经 EgressGuard.check_egress。
    """

    def __init__(self, audit: AuditLogger):
        self._audit = audit

    async def download_and_import(
        self,
        url: str,
        expected_sha256: str,
        dest_kind: str,
        user_id: str,
        role: str,
        filename: Optional[str] = None,
    ) -> dict:
        """下载并校验导入到内网仓库。

        Args:
            url: 资源下载 URL。
            expected_sha256: 期望的 sha256 hex（64 字符）。
            dest_kind: "model" 或 "package"。
            user_id: 触发操作的用户 ID。
            role: 用户角色。
            filename: 可选目标文件名；未提供时从 URL 路径推导。

        Returns:
            dict: {path, sha256, size_bytes, kind}

        Raises:
            AppError: expected_sha256 为空 / dest_kind 非法 / sha256 校验失败 / 下载失败。
            MaintenanceModeRequiredError: 维护模式未开启时尝试外网下载。
        """
        if not expected_sha256 or not expected_sha256.strip():
            raise AppError("expected_sha256 不能为空")

        if dest_kind not in ("model", "package"):
            raise AppError(f"无效的 dest_kind: {dest_kind}（必须为 model 或 package）")

        action = "model_import" if dest_kind == "model" else "package_import"

        # 1. 出口守卫：维护模式未开启则抛 MaintenanceModeRequiredError
        get_egress_guard().check_egress(url)

        # 2. 决定目标文件名（强制取 basename，防止路径穿越）
        if not filename:
            parsed = urlparse(url)
            filename = os.path.basename(parsed.path) or "download.bin"
        filename = os.path.basename(filename) or "download.bin"

        # 3. 解析目标目录
        settings = get_settings()
        if dest_kind == "model":
            dest_dir = Path(settings.MODEL_REPO_PATH)
        else:
            dest_dir = Path(settings.PACKAGE_REPO_PATH)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / filename

        # 4. 流式下载 + 边下边算 sha256
        sha = hashlib.sha256()
        size_bytes = 0
        tmp_path: Optional[Path] = None

        try:
            # 临时文件放在目标目录下，保证跨设备 move 也能 work
            with tempfile.NamedTemporaryFile(
                delete=False, dir=str(dest_dir), suffix=".part"
            ) as tmp:
                tmp_path = Path(tmp.name)
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(_DOWNLOAD_TIMEOUT)
                ) as client:
                    async with client.stream("GET", url) as resp:
                        resp.raise_for_status()
                        async for chunk in resp.aiter_bytes(
                            chunk_size=_CHUNK_SIZE
                        ):
                            tmp.write(chunk)
                            sha.update(chunk)
                            size_bytes += len(chunk)
        except httpx.HTTPError as e:
            self._safe_unlink(tmp_path)
            self._audit.log(
                user_id=user_id,
                role=role,
                action=action,
                extra={
                    "url": url,
                    "status": "download_failed",
                    "error": str(e)[:200],
                    "size_bytes": size_bytes,
                },
            )
            raise AppError(f"下载失败: {e}")
        except Exception:
            # 其他意外异常也清理临时文件
            self._safe_unlink(tmp_path)
            raise

        actual_sha = sha.hexdigest()

        # 5. 校验 sha256（大小写不敏感）
        if actual_sha.lower() != expected_sha256.strip().lower():
            self._safe_unlink(tmp_path)
            self._audit.log(
                user_id=user_id,
                role=role,
                action=action,
                extra={
                    "url": url,
                    "expected_sha256": expected_sha256.strip().lower(),
                    "actual_sha256": actual_sha,
                    "size_bytes": size_bytes,
                    "status": "sha256_mismatch",
                },
            )
            raise AppError(
                f"sha256 校验失败：期望 {expected_sha256.strip()[:16]}… "
                f"实际 {actual_sha[:16]}…"
            )

        # 6. 移动到目标路径（如已存在则覆盖）
        if dest_path.exists():
            try:
                dest_path.unlink()
            except OSError:
                pass
        try:
            shutil.move(str(tmp_path), str(dest_path))
        except OSError as e:
            self._safe_unlink(tmp_path)
            self._audit.log(
                user_id=user_id,
                role=role,
                action=action,
                extra={
                    "url": url,
                    "sha256": actual_sha,
                    "dest_path": str(dest_path),
                    "size_bytes": size_bytes,
                    "status": "move_failed",
                    "error": str(e)[:200],
                },
            )
            raise AppError(f"移动文件到目标路径失败: {e}")

        # 7. 写审计 + 返回
        self._audit.log(
            user_id=user_id,
            role=role,
            action=action,
            extra={
                "url": url,
                "sha256": actual_sha,
                "dest_path": str(dest_path),
                "size_bytes": size_bytes,
                "status": "ok",
            },
        )

        return {
            "path": str(dest_path),
            "sha256": actual_sha,
            "size_bytes": size_bytes,
            "kind": dest_kind,
        }

    @staticmethod
    def _safe_unlink(path: Optional[Path]) -> None:
        """安全删除临时文件，吞掉 OSError。"""
        if path is None:
            return
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


# ---- 全局单例 ----

_model_importer: Optional[ModelImporter] = None


def get_model_importer() -> ModelImporter:
    """获取 ModelImporter 单例。"""
    global _model_importer
    if _model_importer is None:
        from app.deps import get_audit_logger
        _model_importer = ModelImporter(get_audit_logger())
    return _model_importer

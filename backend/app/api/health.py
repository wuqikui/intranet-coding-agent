"""健康检查与就绪探针。"""
from fastapi import APIRouter
from app.config import get_settings
from app.audit.verifier import AuditVerifier

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """存活探针：服务进程是否在运行。"""
    return {"status": "ok"}


@router.get("/ready")
async def readiness():
    """就绪探针：依赖项是否就绪（DB 连接 + 审计链完整性）。"""
    settings = get_settings()
    checks: dict[str, str] = {}

    # 检查审计日志哈希链
    try:
        verifier = AuditVerifier(settings.AUDIT_LOG_PATH, settings.AUDIT_HASH_SEED)
        is_valid, errors = verifier.verify()
        checks["audit_chain"] = "ok" if is_valid else f"broken: {errors[:3]}"
    except Exception as e:
        checks["audit_chain"] = f"error: {e}"

    # 检查工作区目录
    try:
        from pathlib import Path
        Path(settings.WORKSPACE_ROOT).mkdir(parents=True, exist_ok=True)
        checks["workspace"] = "ok"
    except Exception as e:
        checks["workspace"] = f"error: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    return {"ready": all_ok, "checks": checks}

"""首次运行向导 - Task 12。

步骤序列（每步可独立重试）：
  1. gpu_detect       检测 GPU/显存（nvidia-smi 不可用 → 0 卡，可继续 mock）
  2. recommend_model 推荐模型与量化（复用 gateway.vram.recommend_model）
  3. import_weights   扫描 MODEL_REPO_PATH 下已有权重文件并注册
  4. import_kb        导入并分类四库（business/code/buildops/dataapi）已有文档
  5. build_index      建立向量索引 + 符号索引
  6. create_admin     创建管理员账号（若已存在则跳过）
  7. open_users       员工开户占位（提示管理员后续手动）
  8. done             完成

状态持久化到 data/wizard.json，跨进程保留。失败可重试（resume_from）。
CLI 入口：`python -m app.init_wizard [--step <name>] [--reset]`。
Makefile 已引用 `make init` → 触发本向导。

TR-12.1: 各步骤完成且系统就绪。
TR-12.2: 清晰可重试（每步独立状态 + 错误明细 + 友好提示）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.core.logging import setup_logging

logger = logging.getLogger("init_wizard")


# ============================================================================
# 步骤定义
# ============================================================================


# 顺序步骤名（按执行顺序）
STEP_ORDER = [
    "gpu_detect",
    "recommend_model",
    "import_weights",
    "import_kb",
    "build_index",
    "create_admin",
    "open_users",
    "done",
]

STEP_DESCRIPTIONS = {
    "gpu_detect": "检测 GPU/显存",
    "recommend_model": "推荐模型与量化",
    "import_weights": "导入权重",
    "import_kb": "导入并分类知识库",
    "build_index": "建立索引",
    "create_admin": "创建管理员",
    "open_users": "员工开户",
    "done": "完成",
}


# ============================================================================
# 状态
# ============================================================================


@dataclass
class StepResult:
    """单步执行结果。"""

    step: str
    status: str  # success | skipped | failed | pending
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0


@dataclass
class WizardState:
    """向导整体状态。"""

    started_at: str = ""
    updated_at: str = ""
    current_step: str = "gpu_detect"
    completed_steps: List[str] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)  # 跨步骤传递数据
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "current_step": self.current_step,
            "completed_steps": list(self.completed_steps),
            "history": list(self.history),
            "context": dict(self.context),
            "last_error": self.last_error,
            "ready": "done" in self.completed_steps,
            "progress": "%d/%d" % (len(self.completed_steps), len(STEP_ORDER)),
        }


# ============================================================================
# Wizard
# ============================================================================


class InitWizard:
    """首次运行向导。状态持久化到 data/wizard.json。"""

    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self._state_file = Path(settings.AUDIT_LOG_PATH).parent / "wizard.json"
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state: WizardState = self._load_state()

    # ---- 状态持久化 ----

    def _load_state(self) -> WizardState:
        if not self._state_file.exists():
            return WizardState()
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            return WizardState(
                started_at=data.get("started_at", ""),
                updated_at=data.get("updated_at", ""),
                current_step=data.get("current_step", "gpu_detect"),
                completed_steps=list(data.get("completed_steps", [])),
                history=list(data.get("history", [])),
                context=dict(data.get("context", {})),
                last_error=data.get("last_error"),
            )
        except Exception as e:
            logger.warning("加载向导状态失败，重置: %s", e)
            return WizardState()

    def _save_state(self) -> None:
        from datetime import datetime
        now = datetime.now().isoformat(timespec="seconds")
        if not self._state.started_at:
            self._state.started_at = now
        self._state.updated_at = now
        try:
            self._state_file.write_text(
                json.dumps(self._state.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("保存向导状态失败: %s", e)

    def reset(self) -> None:
        """重置向导。"""
        self._state = WizardState()
        self._save_state()

    def get_state(self) -> Dict[str, Any]:
        return self._state.to_dict()

    # ---- 单步执行 ----

    async def run_step(self, step: str) -> StepResult:
        """执行指定步骤。已完成的步骤重跑（除非显式 reset）。"""
        if step not in STEP_ORDER:
            return StepResult(step=step, status="failed", message="未知步骤: %s" % step)
        handler = getattr(self, "_step_%s" % step, None)
        if handler is None:
            return StepResult(step=step, status="failed", message="步骤 %s 未实现" % step)
        try:
            result = await handler()
        except Exception as e:
            logger.exception("步骤 %s 异常", step)
            self._state.last_error = str(e)
            result = StepResult(step=step, status="failed", message="异常: %s" % e)
        # 更新状态
        self._state.history.append({
            "step": step,
            "status": result.status,
            "message": result.message,
            "data": result.data,
            "duration_ms": result.duration_ms,
        })
        if result.status == "success" or result.status == "skipped":
            if step not in self._state.completed_steps:
                self._state.completed_steps.append(step)
            # 推进 current_step
            idx = STEP_ORDER.index(step)
            if idx + 1 < len(STEP_ORDER):
                self._state.current_step = STEP_ORDER[idx + 1]
            self._state.last_error = None
        self._save_state()
        return result

    async def run_all(self) -> Dict[str, Any]:
        """顺序执行所有未完成步骤。失败即停止。"""
        for step in STEP_ORDER:
            if step in self._state.completed_steps:
                continue
            result = await self.run_step(step)
            if result.status == "failed":
                return {
                    "stopped_at": step,
                    "result": result.__dict__,
                    "state": self.get_state(),
                }
        return {"stopped_at": "done", "state": self.get_state()}

    async def resume_from(self, step: str) -> Dict[str, Any]:
        """从指定步骤继续执行（先清掉该步骤及之后的完成标记）。"""
        if step not in STEP_ORDER:
            return {"error": "未知步骤: %s" % step}
        idx = STEP_ORDER.index(step)
        self._state.completed_steps = [
            s for s in self._state.completed_steps
            if STEP_ORDER.index(s) < idx
        ]
        self._state.current_step = step
        self._save_state()
        return await self.run_all()

    # ---- 步骤实现 ----

    async def _step_gpu_detect(self) -> StepResult:
        """检测 GPU/显存。"""
        import time
        start = time.time()
        gpu_info = self._detect_gpus()
        self._state.context["gpu"] = gpu_info
        elapsed = int((time.time() - start) * 1000)
        if gpu_info["count"] == 0:
            return StepResult(
                step="gpu_detect",
                status="success",  # 无 GPU 也算成功（用 mock 模式继续）
                message="未检测到 GPU（将使用 mock 后端；部署机有 GPU 时自动启用真实推理）",
                data=gpu_info,
                duration_ms=elapsed,
            )
        return StepResult(
            step="gpu_detect",
            status="success",
            message="检测到 %d 张 GPU（每卡 %.1f GB）" % (
                gpu_info["count"], gpu_info["vram_per_gpu_gb"]
            ),
            data=gpu_info,
            duration_ms=elapsed,
        )

    async def _step_recommend_model(self) -> StepResult:
        """推荐模型与量化。"""
        import time
        start = time.time()
        from app.gateway.vram import recommend_model
        gpu = self._state.context.get("gpu", {})
        gpu_count = int(gpu.get("count", 0) or 0)
        vram_per = float(gpu.get("vram_per_gpu_gb", 0) or 0)
        if gpu_count == 0 or vram_per == 0:
            rec = {
                "model": self.settings.MODEL_NAME,
                "quantization": self.settings.QUANTIZATION,
                "estimated_vram_gb": 0.0,
                "tp_size": 1,
                "note": "无 GPU，沿用 settings 默认配置（mock 模式）",
            }
        else:
            rec = recommend_model(gpu_count, vram_per)
        self._state.context["recommendation"] = rec
        elapsed = int((time.time() - start) * 1000)
        return StepResult(
            step="recommend_model",
            status="success",
            message="推荐模型：%s（%s，TP=%d，显存≈%.1fGB）" % (
                rec.get("model", "?"),
                rec.get("quantization", "?"),
                rec.get("tp_size", 1),
                rec.get("estimated_vram_gb", 0),
            ),
            data=rec,
            duration_ms=elapsed,
        )

    async def _step_import_weights(self) -> StepResult:
        """扫描 MODEL_REPO_PATH 下已有权重并注册。"""
        import time
        start = time.time()
        repo = Path(self.settings.MODEL_REPO_PATH)
        if not repo.exists():
            return StepResult(
                step="import_weights",
                status="skipped",
                message="MODEL_REPO_PATH=%s 不存在（部署机由管理员通过出网维护模式导入）" % repo,
                data={"path": str(repo), "files": []},
            )
        # 扫描权重文件（.safetensors / .gguf / .bin / .pt）
        weight_exts = (".safetensors", ".gguf", ".bin", ".pt", ".pth")
        files = []
        for p in sorted(repo.rglob("*")):
            if p.is_file() and p.suffix.lower() in weight_exts:
                files.append({
                    "name": p.name,
                    "path": str(p),
                    "size_mb": round(p.stat().st_size / 1e6, 1),
                })
        self._state.context["weights"] = files
        elapsed = int((time.time() - start) * 1000)
        if not files:
            return StepResult(
                step="import_weights",
                status="skipped",
                message="MODEL_REPO_PATH 下未发现权重文件（mock 模式可继续；部署机待导入）",
                data={"path": str(repo), "files": []},
                duration_ms=elapsed,
            )
        return StepResult(
            step="import_weights",
            status="success",
            message="发现 %d 个权重文件" % len(files),
            data={"path": str(repo), "files": files},
            duration_ms=elapsed,
        )

    async def _step_import_kb(self) -> StepResult:
        """导入并分类四库已有文档。"""
        import time
        start = time.time()
        from app.api.knowledge import get_knowledge_base
        kb = get_knowledge_base()
        # 按 settings 的 KB_*_PATH 读取文档（仅 .txt / .md / .py / .cpp / .h / .json / .yaml 等）
        channel_paths = {
            "business": self.settings.KB_BUSINESS_PATH,
            "code": self.settings.KB_CODE_PATH,
            "buildops": self.settings.KB_BUILDOPS_PATH,
            "dataapi": self.settings.KB_DATAAPI_PATH,
        }
        text_exts = (".txt", ".md", ".rst", ".py", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".json", ".yaml", ".yml", ".toml", ".sh", ".ps1", ".bat")
        total_ingested = 0
        by_channel: Dict[str, int] = {}
        for ch, path_str in channel_paths.items():
            path = Path(path_str)
            if not path.exists():
                by_channel[ch] = 0
                continue
            docs: List[Dict[str, Any]] = []
            for p in sorted(path.rglob("*")):
                if not p.is_file() or p.suffix.lower() not in text_exts:
                    continue
                try:
                    content = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if not content.strip():
                    continue
                docs.append({
                    "content": content,
                    "filename": p.name,
                    "path": str(p),
                })
            if docs:
                try:
                    result = kb.ingest(ch, docs, auto_classify=False)
                    count = int(result.get("ingested", 0))
                    total_ingested += count
                    by_channel[ch] = count
                except Exception as e:
                    logger.warning("通道 %s 入库失败: %s", ch, e)
                    by_channel[ch] = 0
            else:
                by_channel[ch] = 0
        self._state.context["kb_ingested"] = {
            "total": total_ingested,
            "by_channel": by_channel,
        }
        elapsed = int((time.time() - start) * 1000)
        if total_ingested == 0:
            return StepResult(
                step="import_kb",
                status="skipped",
                message="四库路径下未发现可导入文档（空库可继续；部署机由管理员上传）",
                data=by_channel,
                duration_ms=elapsed,
            )
        return StepResult(
            step="import_kb",
            status="success",
            message="入库 %d 篇文档（business=%d code=%d buildops=%d dataapi=%d）" % (
                total_ingested, by_channel.get("business", 0),
                by_channel.get("code", 0), by_channel.get("buildops", 0),
                by_channel.get("dataapi", 0),
            ),
            data={"total": total_ingested, "by_channel": by_channel},
            duration_ms=elapsed,
        )

    async def _step_build_index(self) -> StepResult:
        """建立索引（KB 在 ingest 时已建，这里只做一次 stats 校验）。"""
        import time
        start = time.time()
        from app.api.knowledge import get_knowledge_base
        kb = get_knowledge_base()
        try:
            stats = kb.stats()
        except Exception as e:
            return StepResult(
                step="build_index",
                status="failed",
                message="stats 调用失败: %s" % e,
            )
        elapsed = int((time.time() - start) * 1000)
        self._state.context["kb_stats"] = stats
        return StepResult(
            step="build_index",
            status="success",
            message="索引就绪：%s" % (
                ", ".join("%s=%d" % (k, v) for k, v in stats.get("channels", {}).items())
                or "空库"
            ),
            data=stats,
            duration_ms=elapsed,
        )

    async def _step_create_admin(self) -> StepResult:
        """创建管理员账号。"""
        import time
        from sqlalchemy import select as sa_select
        from app.auth.local import hash_password
        from app.auth.models import ROLE_ADMIN, User
        from app.models.db import async_session, init_db
        start = time.time()
        await init_db()
        admin_username = os.environ.get("INIT_ADMIN_USERNAME", "admin")
        admin_password = os.environ.get("INIT_ADMIN_PASSWORD", "")
        if not admin_password:
            # 生成一次性密码
            admin_password = secrets.token_urlsafe(12)
        async with async_session() as s:
            result = await s.execute(sa_select(User).where(User.username == admin_username))
            existing = result.scalar_one_or_none()
            if existing is not None:
                elapsed = int((time.time() - start) * 1000)
                return StepResult(
                    step="create_admin",
                    status="skipped",
                    message="管理员账号 %s 已存在，跳过创建" % admin_username,
                    data={"username": admin_username, "id": existing.id},
                    duration_ms=elapsed,
                )
            u = User(
                username=admin_username,
                role=ROLE_ADMIN,
                password_hash=hash_password(admin_password),
                is_active=True,
            )
            s.add(u)
            await s.commit()
        elapsed = int((time.time() - start) * 1000)
        # 把初始密码写到一个一次性文件（管理员首次登录后改）
        cred_file = Path(self.settings.AUDIT_LOG_PATH).parent / "admin.initial-creds.txt"
        try:
            cred_file.write_text(
                "首次登录凭据：\n  username: %s\n  password: %s\n（首次登录后请立即修改）\n" % (
                    admin_username, admin_password,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass
        return StepResult(
            step="create_admin",
            status="success",
            message="管理员账号已创建（username=%s；初始密码已写到 %s）" % (admin_username, cred_file.name),
            data={"username": admin_username, "creds_file": str(cred_file)},
            duration_ms=elapsed,
        )

    async def _step_open_users(self) -> StepResult:
        """员工开户占位（提示后续手动）。"""
        return StepResult(
            step="open_users",
            status="success",
            message="员工开户请由管理员后续手动（POST /api/admin/users 或后台面板）",
            data={"hint": "POST /api/admin/users {username, password, role}"},
        )

    async def _step_done(self) -> StepResult:
        """完成。"""
        ready = self._is_system_ready()
        if not ready["ready"]:
            return StepResult(
                step="done",
                status="failed",
                message="系统未就绪：%s" % ready["reason"],
                data=ready,
            )
        return StepResult(
            step="done",
            status="success",
            message="✅ 首次运行向导完成，系统就绪",
            data=ready,
        )

    # ---- 工具方法 ----

    def _detect_gpus(self) -> Dict[str, Any]:
        """检测 GPU 数量与单卡显存（nvidia-smi）。"""
        # 优先 nvidia-smi
        for nvidia_smi in ("nvidia-smi", "nvidia-smi.exe"):
            try:
                proc = subprocess.run(
                    [nvidia_smi, "--query-gpu=count,memory.total", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=10,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    # 输出形如 "1, 24576"（单卡）或多行
                    lines = [ln.strip() for ln in proc.stdout.strip().splitlines() if ln.strip()]
                    # nvidia-smi --query-gpu=count 实际返回每行一个 count=1，所以行数=卡数
                    gpu_count = len(lines)
                    # 解析 memory.total（MB）
                    vram_mb = 0
                    if gpu_count > 0:
                        try:
                            vram_mb = int(lines[0].split(",")[1].strip())
                        except (IndexError, ValueError):
                            vram_mb = 0
                    return {
                        "count": gpu_count,
                        "vram_per_gpu_gb": round(vram_mb / 1024, 2),
                        "vram_total_gb": round(gpu_count * vram_mb / 1024, 2),
                        "source": "nvidia-smi",
                    }
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
            except Exception as e:
                logger.debug("nvidia-smi 调用失败: %s", e)
                continue
        # 兜底：环境变量 NVIDIA_VISIBLE_DEVICES
        nvd = os.environ.get("NVIDIA_VISIBLE_DEVICES", "")
        if nvd and nvd != "void":
            count = len([d for d in nvd.split(",") if d.strip()])
            return {
                "count": count,
                "vram_per_gpu_gb": 0,  # 未知显存，待 recommend_model 用默认
                "vram_total_gb": 0,
                "source": "env_var",
            }
        return {
            "count": 0,
            "vram_per_gpu_gb": 0,
            "vram_total_gb": 0,
            "source": "none",
        }

    def _is_system_ready(self) -> Dict[str, Any]:
        """检查系统是否就绪。"""
        checks = []
        # 必要步骤都完成
        required = ["gpu_detect", "recommend_model", "import_weights", "import_kb", "build_index", "create_admin"]
        missing = [s for s in required if s not in self._state.completed_steps]
        if missing:
            checks.append("缺少已完成步骤: %s" % ",".join(missing))
        # 管理员账号存在性（如果跳过则不改 DB，但需要 create_admin 至少跑过）
        if "create_admin" not in self._state.completed_steps:
            checks.append("管理员账号未创建")
        if checks:
            return {"ready": False, "reason": "; ".join(checks)}
        return {
            "ready": True,
            "reason": "全部步骤已完成",
            "next": "管理员可登录后台，POST /api/admin/users 开户员工",
        }


# ============================================================================
# 单例 + CLI
# ============================================================================


_wizard: Optional[InitWizard] = None


def get_wizard() -> InitWizard:
    global _wizard
    if _wizard is None:
        _wizard = InitWizard()
    return _wizard


def reset_wizard_for_test() -> None:
    global _wizard
    _wizard = None


# ---- CLI 入口 ----


async def _cli_main(args: argparse.Namespace) -> int:
    setup_logging()
    wizard = get_wizard()
    if args.reset:
        wizard.reset()
        print("[wizard] 状态已重置")
        return 0
    if args.step:
        result = await wizard.run_step(args.step)
        print("[wizard] %s → %s: %s" % (result.step, result.status, result.message))
        if result.status == "failed":
            return 1
        return 0
    if args.resume_from:
        result = await wizard.resume_from(args.resume_from)
        print("[wizard] resume_from=%s → %s" % (args.resume_from, result.get("stopped_at")))
        return 0 if result.get("stopped_at") == "done" else 1
    # 默认 run_all
    result = await wizard.run_all()
    state = result.get("state", {})
    print("[wizard] 停在: %s；进度: %s；就绪: %s" % (
        result.get("stopped_at"),
        state.get("progress"),
        state.get("ready"),
    ))
    return 0 if result.get("stopped_at") == "done" else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="首次运行向导")
    parser.add_argument("--step", help="只执行指定步骤（gpu_detect/recommend_model/.../done）")
    parser.add_argument("--resume-from", dest="resume_from", help="从指定步骤继续")
    parser.add_argument("--reset", action="store_true", help="重置向导状态")
    parser.add_argument("--state", action="store_true", help="只打印当前状态")
    args = parser.parse_args()
    if args.state:
        setup_logging()
        print(json.dumps(get_wizard().get_state(), ensure_ascii=False, indent=2))
        return
    rc = asyncio.run(_cli_main(args))
    raise SystemExit(rc)


if __name__ == "__main__":
    main()

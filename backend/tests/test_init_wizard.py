"""Task 12 首次运行向导测试。

覆盖：
- TR-12.1: 向导各步骤完成且系统就绪
- TR-12.2: 向导可操作性（清晰可重试）

策略：使用现有 DB，每次测试用随机 admin 用户名避免冲突。
"""
import os
import sys
import asyncio
import secrets
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient

from app.init_wizard import STEP_ORDER, get_wizard, reset_wizard_for_test
from app.main import app

client = TestClient(app)


def _unique_admin_name() -> str:
    return "wizard_admin_%s" % secrets.token_hex(4)


def test_wizard_step_order():
    """步骤顺序正确。"""
    assert STEP_ORDER[0] == "gpu_detect"
    assert STEP_ORDER[-1] == "done"
    assert "create_admin" in STEP_ORDER
    assert "import_weights" in STEP_ORDER
    assert "build_index" in STEP_ORDER
    print("[PASS] STEP_ORDER 正确（%d 步）" % len(STEP_ORDER))


def test_wizard_run_all():
    """TR-12.1: run_all 跑通所有步骤，最后 ready=True。"""
    admin_name = _unique_admin_name()
    os.environ["INIT_ADMIN_USERNAME"] = admin_name
    reset_wizard_for_test()
    wizard = get_wizard()
    result = asyncio.run(wizard.run_all())
    state = result.get("state", {})
    print("  stopped_at=%s progress=%s ready=%s" % (
        result.get("stopped_at"), state.get("progress"), state.get("ready"),
    ))
    assert result.get("stopped_at") == "done", \
        "应停在 done，实际 %s" % result.get("stopped_at")
    assert state.get("ready") is True
    assert "done" in state.get("completed_steps", [])
    # context 应有跨步数据
    ctx = state.get("context", {})
    assert "gpu" in ctx
    assert "recommendation" in ctx
    print("[PASS] run_all 完成，系统就绪（GPU=%s rec=%s）" % (
        ctx.get("gpu", {}).get("count", "?"),
        ctx.get("recommendation", {}).get("model", "?"),
    ))


def test_wizard_state_persistence():
    """状态持久化跨实例。"""
    admin_name = _unique_admin_name()
    os.environ["INIT_ADMIN_USERNAME"] = admin_name
    reset_wizard_for_test()
    wizard = get_wizard()
    asyncio.run(wizard.run_step("gpu_detect"))
    # 新实例加载
    reset_wizard_for_test()
    new_wizard = get_wizard()
    state = new_wizard.get_state()
    assert "gpu_detect" in state.get("completed_steps", []), \
        "新实例应加载已完成步骤"
    print("[PASS] 状态持久化跨实例加载")


def test_wizard_resume_from_step():
    """TR-12.2: 失败可重试 - resume_from 清掉后续步骤。"""
    admin_name = _unique_admin_name()
    os.environ["INIT_ADMIN_USERNAME"] = admin_name
    reset_wizard_for_test()
    wizard = get_wizard()
    # 跑完所有
    asyncio.run(wizard.run_all())
    # 模拟失败：手动清掉 import_kb 及之后
    wizard._state.completed_steps = [
        s for s in wizard._state.completed_steps
        if STEP_ORDER.index(s) < STEP_ORDER.index("import_kb")
    ]
    wizard._save_state()
    # resume_from import_kb
    result = asyncio.run(wizard.resume_from("import_kb"))
    state = result.get("state", {})
    assert state.get("ready") is True, "resume 后应就绪"
    print("[PASS] resume_from import_kb 跑通，系统就绪")


def test_wizard_api_state_endpoint():
    """GET /api/wizard/state 返回完整状态。"""
    r = client.get("/api/wizard/state")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "steps" in body
    assert "current_step" in body
    assert "progress" in body
    assert "completed_steps" in body
    print("[PASS] /api/wizard/state 返回完整字段（progress=%s）" % body.get("progress"))


def test_wizard_api_run_step_endpoint():
    """POST /api/wizard/run 单步执行。"""
    reset_wizard_for_test()
    r = client.post("/api/wizard/run", json={"step": "gpu_detect"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] in ("success", "skipped", "failed")
    assert "state" in body
    assert "duration_ms" in body
    print("[PASS] POST /api/wizard/run step=gpu_detect → %s (%dms)" % (
        body["status"], body.get("duration_ms", 0)
    ))


def test_wizard_api_reset_requires_auth():
    """POST /api/wizard/reset 无 token 应 401。"""
    r = client.post("/api/wizard/reset")
    assert r.status_code in (401, 403), "reset 无 token 应 401/403，实际 %d" % r.status_code
    print("[PASS] /api/wizard/reset 无 token 返回 %d" % r.status_code)


def test_wizard_individual_steps():
    """各步骤单独执行，验证 handler 全部实现。"""
    for step in STEP_ORDER:
        admin_name = _unique_admin_name() if step == "create_admin" else None
        if admin_name:
            os.environ["INIT_ADMIN_USERNAME"] = admin_name
        reset_wizard_for_test()
        wizard = get_wizard()
        result = asyncio.run(wizard.run_step(step))
        assert result.status in ("success", "skipped"), \
            "步骤 %s 应成功/跳过，实际 %s（msg=%s）" % (
                step, result.status, result.message
            )
    print("[PASS] 全部 %d 步可独立执行" % len(STEP_ORDER))


def test_openapi_includes_wizard_routes():
    """OpenAPI schema 包含 wizard 路由。"""
    r = client.get("/openapi.json")
    paths = list(r.json()["paths"].keys())
    for p in ("/api/wizard/state", "/api/wizard/run", "/api/wizard/reset"):
        assert p in paths, "缺少 %s" % p
    print("[PASS] wizard 路由全部挂载（3 个）")


if __name__ == "__main__":
    test_wizard_step_order()
    test_wizard_run_all()
    test_wizard_state_persistence()
    test_wizard_resume_from_step()
    test_wizard_api_state_endpoint()
    test_wizard_api_run_step_endpoint()
    test_wizard_api_reset_requires_auth()
    test_wizard_individual_steps()
    test_openapi_includes_wizard_routes()
    print("\n=== Task 12 首次运行向导测试全部通过 ===")

"""Task 10 沙箱与工具集测试。

覆盖：
- TR-10.1: 危险命令（rm/git push/删库等）被拦截并要求二次确认
- TR-10.2: readonly 调用 shell 返回 403
- 工具 API 基础冒烟（文件/搜索/git diff/test/lint）
"""
import os
import sys
import asyncio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient

from app.auth.local import hash_password
from app.auth.models import User
from app.main import app
from app.models.db import async_session, init_db

client = TestClient(app)


async def _seed_user(username: str, role: str) -> int:
    """创建用户并返回 ID（幂等：若已存在则更新角色/密码）。"""
    from sqlalchemy import select as sa_select
    await init_db()
    async with async_session() as s:
        result = await s.execute(sa_select(User).where(User.username == username))
        u = result.scalar_one_or_none()
        if u is None:
            u = User(
                username=username,
                role=role,
                password_hash=hash_password("pass"),
                is_active=True,
            )
            s.add(u)
        else:
            u.role = role
            u.password_hash = hash_password("pass")
            u.is_active = True
        await s.flush()
        await s.commit()
        return u.id


def _login(username: str, password: str = "pass") -> str:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": "Bearer " + token}


# ============================================================================
# 单元测试：DangerousCommandDetector
# ============================================================================


def test_danger_detector_block_level():
    """TR-10.1: 不可逆命令（block）即便 confirm=true 也拒绝。"""
    from app.sandbox.dangerous import get_danger_detector
    d = get_danger_detector()

    # rm -rf /
    block, v = d.should_block(["rm", "-rf", "/"], confirmed=True)
    assert block, "rm -rf / 即便 confirm 也应被阻断"
    assert v.severity == "block"

    # mkfs
    block, v = d.should_block(["mkfs.ext4", "/dev/sda"], confirmed=True)
    assert block and v.severity == "block"

    # git push --force
    block, v = d.should_block(["git", "push", "--force", "origin", "main"], confirmed=True)
    assert block and v.severity == "block"

    # DROP DATABASE
    block, v = d.should_block(["psql", "-c", "DROP DATABASE prod;"], confirmed=True)
    assert block and v.severity == "block"
    print("[PASS] block 级命令（不可逆）始终阻断")


def test_danger_detector_warn_level_needs_confirm():
    """TR-10.1: warn 级命令未确认 → 阻断；confirm=true → 放行。"""
    from app.sandbox.dangerous import get_danger_detector
    d = get_danger_detector()

    # rm -rf /tmp/foo（warn）
    block, v = d.should_block(["rm", "-rf", "/tmp/foo"], confirmed=False)
    assert block, "未 confirm 应阻断"
    assert v.severity == "warn"
    block, v = d.should_block(["rm", "-rf", "/tmp/foo"], confirmed=True)
    assert not block, "confirm 后应放行"

    # git push（warn）
    block, v = d.should_block(["git", "push", "origin", "main"], confirmed=False)
    assert block and v.severity == "warn"
    block, v = d.should_block(["git", "push", "origin", "main"], confirmed=True)
    assert not block

    # git commit --amend（warn）
    block, v = d.should_block(["git", "commit", "--amend"], confirmed=False)
    assert block and v.severity == "warn"

    # DELETE FROM (warn)
    block, v = d.should_block(["psql", "-c", "DELETE FROM users;"], confirmed=False)
    assert block and v.severity == "warn"
    print("[PASS] warn 级命令未确认阻断，confirm 后放行")


def test_danger_detector_safe_commands():
    """普通命令不阻断。"""
    from app.sandbox.dangerous import get_danger_detector
    d = get_danger_detector()
    for cmd in (
        ["ls", "-l"],
        ["python", "main.py"],
        ["cmake", "--build", "build"],
        ["make"],
        ["cat", "README.md"],
        ["grep", "-rn", "foo", "."],
    ):
        block, v = d.should_block(cmd, confirmed=False)
        assert not block, "%s 不应被阻断（实际 %s）" % (cmd, v.reason)
    print("[PASS] 普通命令不阻断")


# ============================================================================
# 端到端测试：tools API + RBAC
# ============================================================================


def test_shell_readonly_forbidden():
    """TR-10.2: readonly 调用 /api/tools/shell 返回 403。"""
    # seed readonly 用户
    rid = asyncio.run(_seed_user("ro_user", "readonly"))
    token = _login("ro_user")

    # 创建项目（先用 admin 触发生成 - 用 agent/generate mock 跑一次）
    # 但 admin 也得创建，所以先建 admin
    asyncio.run(_seed_user("admin_user", "admin"))
    admin_token = _login("admin_user")

    # 触发一次 mock 生成，拿到 project_id
    r = client.post(
        "/api/agent/generate",
        json={"messages": [{"role": "user", "content": "生成一个 Python hello"}], "stream": False},
        headers=_auth_header(admin_token),
    )
    assert r.status_code == 200, r.text
    pid = r.json()["project_id"]

    # readonly 调用 shell
    r = client.post(
        "/api/tools/shell",
        json={"project_id": pid, "command": ["ls", "-l"]},
        headers=_auth_header(token),
    )
    assert r.status_code == 403, "readonly 调用 shell 应 403，实际 %d" % r.status_code
    print("[PASS] readonly 调用 /api/tools/shell 返回 403")


def test_shell_dangerous_needs_confirm():
    """TR-10.1: 危险命令返回 449 needs_confirm。"""
    asyncio.run(_seed_user("dev_user", "dev"))
    token = _login("dev_user")
    admin_token = _login("admin_user")

    r = client.post(
        "/api/agent/generate",
        json={"messages": [{"role": "user", "content": "python demo"}], "stream": False},
        headers=_auth_header(admin_token),
    )
    pid = r.json()["project_id"]

    # rm -rf（warn，未确认）
    r = client.post(
        "/api/tools/shell",
        json={"project_id": pid, "command": ["rm", "-rf", "build"], "confirm": False},
        headers=_auth_header(token),
    )
    assert r.status_code == 449, "rm -rf 应返回 449，实际 %d" % r.status_code
    body = r.json()
    assert body["needs_confirm"] is True
    assert "rm -rf" in body["reason"].lower() or "递归" in body["reason"]
    print("[PASS] rm -rf 返回 449 needs_confirm: %s" % body["reason"])

    # git push（warn，未确认）
    r = client.post(
        "/api/tools/shell",
        json={"project_id": pid, "command": ["git", "push"], "confirm": False},
        headers=_auth_header(token),
    )
    assert r.status_code == 449
    print("[PASS] git push 返回 449 needs_confirm")


def test_shell_block_level_rejected_even_with_confirm():
    """TR-10.1: 不可逆命令即便 confirm=true 也 403。"""
    asyncio.run(_seed_user("dev_user2", "dev"))
    token = _login("dev_user2")
    admin_token = _login("admin_user")

    r = client.post(
        "/api/agent/generate",
        json={"messages": [{"role": "user", "content": "python x"}], "stream": False},
        headers=_auth_header(admin_token),
    )
    pid = r.json()["project_id"]

    # rm -rf /
    r = client.post(
        "/api/tools/shell",
        json={"project_id": pid, "command": ["rm", "-rf", "/"], "confirm": True},
        headers=_auth_header(token),
    )
    assert r.status_code == 403, "rm -rf / 即便 confirm 也应 403，实际 %d" % r.status_code
    print("[PASS] rm -rf / 即便 confirm=true 也 403")


def test_file_read_write_list():
    """文件读写冒烟。"""
    asyncio.run(_seed_user("dev_user3", "dev"))
    token = _login("dev_user3")
    admin_token = _login("admin_user")

    r = client.post(
        "/api/agent/generate",
        json={"messages": [{"role": "user", "content": "python file test"}], "stream": False},
        headers=_auth_header(admin_token),
    )
    pid = r.json()["project_id"]

    # write
    r = client.post(
        "/api/tools/file/write",
        json={"project_id": pid, "path": "notes.md", "content": "# hi\nworld"},
        headers=_auth_header(token),
    )
    assert r.status_code == 200, r.text

    # read
    r = client.post(
        "/api/tools/file/read",
        json={"project_id": pid, "path": "notes.md"},
        headers=_auth_header(token),
    )
    assert r.status_code == 200
    assert "# hi" in r.json()["content"]

    # list
    r = client.post(
        "/api/tools/file/list",
        json={"project_id": pid, "subpath": ""},
        headers=_auth_header(token),
    )
    assert r.status_code == 200
    paths = [e["path"] for e in r.json()["entries"]]
    assert "notes.md" in paths
    print("[PASS] 文件 read/write/list 通过")


def test_file_write_dangerous_path_forbidden():
    """CI/CD 与 git 内部路径禁止写入。"""
    asyncio.run(_seed_user("dev_user4", "dev"))
    token = _login("dev_user4")
    admin_token = _login("admin_user")

    r = client.post(
        "/api/agent/generate",
        json={"messages": [{"role": "user", "content": "x"}], "stream": False},
        headers=_auth_header(admin_token),
    )
    pid = r.json()["project_id"]

    r = client.post(
        "/api/tools/file/write",
        json={
            "project_id": pid,
            "path": ".github/workflows/deploy.yml",
            "content": "name: deploy\non: push\n",
        },
        headers=_auth_header(token),
    )
    assert r.status_code == 403, "写 .github/workflows 应 403，实际 %d" % r.status_code
    print("[PASS] 写 CI/CD 路径被拦截")


def test_search():
    """全局搜索冒烟。"""
    asyncio.run(_seed_user("dev_user5", "dev"))
    token = _login("dev_user5")
    admin_token = _login("admin_user")

    r = client.post(
        "/api/agent/generate",
        json={"messages": [{"role": "user", "content": "calculator"}], "stream": False},
        headers=_auth_header(admin_token),
    )
    pid = r.json()["project_id"]

    # 搜索 add（应该能命中 mock 生成的 main.py）
    r = client.post(
        "/api/tools/search",
        json={"project_id": pid, "query": "add", "mode": "both", "max_results": 20},
        headers=_auth_header(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["count"] >= 1, "应至少匹配到 1 个结果"
    print("[PASS] search 通过，命中 %d 个" % r.json()["count"])


def test_openapi_includes_tools_routes():
    """OpenAPI schema 包含所有 tools 路由。"""
    r = client.get("/openapi.json")
    paths = list(r.json()["paths"].keys())
    expected = [
        "/api/tools/file/read",
        "/api/tools/file/write",
        "/api/tools/file/list",
        "/api/tools/search",
        "/api/tools/shell",
        "/api/tools/git-diff",
        "/api/tools/test",
        "/api/tools/lint",
    ]
    for p in expected:
        assert p in paths, "缺少路由 %s" % p
    print("[PASS] tools 路由全部挂载（%d 个）" % len(expected))


if __name__ == "__main__":
    test_danger_detector_block_level()
    test_danger_detector_warn_level_needs_confirm()
    test_danger_detector_safe_commands()
    test_shell_readonly_forbidden()
    test_shell_dangerous_needs_confirm()
    test_shell_block_level_rejected_even_with_confirm()
    test_file_read_write_list()
    test_file_write_dangerous_path_forbidden()
    test_search()
    test_openapi_includes_tools_routes()
    print("\n=== Task 10 工具与沙箱测试全部通过 ===")

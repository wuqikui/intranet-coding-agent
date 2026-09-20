"""Task 5 冒烟测试 - 验证 /api/admin/maintenance GET 返回 200。

注意：main.py 暂未 include admin 路由（约束要求不修改 main.py），
本测试在导入时手动挂载 admin_router 到 app 上，仅用于冒烟验证。
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.admin import router as admin_router
from app.auth.local import hash_password
from app.auth.models import User
from app.main import app
from app.models.db import async_session, init_db

# 临时挂载 admin 路由（main.py 未 include，仅测试用）
if "/api/admin" not in {r.path for r in app.routes}:
    app.include_router(admin_router)

ADMIN_USER = "admin_smoke_test"
ADMIN_PASS = "smoke-test-pass-123"


def _seed_admin_user() -> None:
    """直接写 DB 注入一个 admin 用户用于测试（idempotent）。"""

    async def _seed():
        await init_db()  # 确保表存在
        async with async_session() as session:
            existing = await session.execute(
                select(User).where(User.username == ADMIN_USER)
            )
            if existing.scalar_one_or_none() is None:
                session.add(
                    User(
                        username=ADMIN_USER,
                        role="admin",
                        password_hash=hash_password(ADMIN_PASS),
                        is_active=True,
                    )
                )
                await session.commit()

    asyncio.run(_seed())


def test_admin_maintenance_get():
    _seed_admin_user()
    client = TestClient(app)

    # 登录获取 token
    r = client.post(
        "/api/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASS},
    )
    assert r.status_code == 200, f"登录失败: {r.status_code} {r.text}"
    token = r.json()["access_token"]

    # 调用 admin 端点
    r = client.get(
        "/api/admin/maintenance",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, f"维护模式查询失败: {r.status_code} {r.text}"
    data = r.json()
    assert "maintenance_mode" in data
    print(f"[PASS] /api/admin/maintenance GET -> {data}")


if __name__ == "__main__":
    test_admin_maintenance_get()
    print("\n=== Task 5 admin 冒烟测试通过 ===")

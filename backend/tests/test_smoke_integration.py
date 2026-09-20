"""端到端冒烟测试 - 验证 Task 2/3/4/6 集成后的核心端点。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "ok"
    print(f"[PASS] /api/health -> {data}")


def test_auth_flow():
    # 登录失败（无用户）
    r = client.post("/api/auth/login", json={"username": "nobody", "password": "x"})
    assert r.status_code in (401, 400, 404), r.text
    print(f"[PASS] /api/auth/login (无用户) -> {r.status_code}")

    # /me 未授权
    r = client.get("/api/auth/me")
    assert r.status_code in (401, 400, 403), r.text
    print(f"[PASS] /api/auth/me (无 token) -> {r.status_code}")


def test_gateway_endpoints():
    # 健康检查
    r = client.get("/api/gateway/health")
    assert r.status_code == 200, r.text
    print(f"[PASS] /api/gateway/health -> {r.json()}")

    # 模型列表
    r = client.get("/api/gateway/models")
    assert r.status_code == 200, r.text
    print(f"[PASS] /api/gateway/models -> {r.json()}")

    # 推荐
    r = client.get("/api/gateway/recommend")
    assert r.status_code == 200, r.text
    print(f"[PASS] /api/gateway/recommend -> {r.json()}")

    # 非流式 chat（mock）
    r = client.post(
        "/api/gateway/chat",
        json={"messages": [{"role": "user", "content": "生成一个 CMake 项目"}], "stream": False},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert "content" in out or "choices" in out, out
    print(f"[PASS] /api/gateway/chat (mock 非流式) -> {str(out)[:80]}")


def test_knowledge_endpoints():
    # 统计（空库也应返回 200）
    r = client.get("/api/knowledge/stats")
    assert r.status_code == 200, r.text
    print(f"[PASS] /api/knowledge/stats -> {r.json()}")

    # 非法通道
    r = client.post("/api/knowledge/search/invalid_channel", json={"query": "x", "k": 3})
    assert r.status_code in (400, 404), r.text
    print(f"[PASS] /api/knowledge/search/invalid_channel -> {r.status_code}")


def test_openapi_schema():
    r = client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    paths = list(schema["paths"].keys())
    print(f"[PASS] /openapi.json 路径数: {len(paths)}")
    for p in sorted(paths):
        print(f"      - {p}")


if __name__ == "__main__":
    test_health()
    test_auth_flow()
    test_gateway_endpoints()
    test_knowledge_endpoints()
    test_openapi_schema()
    print("\n=== 全部冒烟测试通过 ===")

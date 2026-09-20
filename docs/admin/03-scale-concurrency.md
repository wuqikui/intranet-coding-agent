# 扩容并发

## 概述

多用户并发场景下，单进程 vLLM 受 KV cache 显存限制，最大并发数有上限。
扩容手段有三层：

1. **单卡内优化**：调小 `MAX_MODEL_LEN`、提高 `GPU_MEMORY_UTILIZATION`，挤更多 KV cache 空间。
2. **多卡 TP**：`TENSOR_PARALLEL_SIZE` 提升单实例吞吐；但 TP>4 后通信开销陡增。
3. **多实例水平扩容**：起多个后端实例，前置 nginx/traefik 做负载均衡。

目标验收：3 人并发不超时（AC-1）。

## 前置条件

- 已正确配置 `QUANTIZATION` 与 `MODEL_PATH`（见 `02-quantization-selection.md`）。
- 已知 GPU 数量与单卡显存。
- 已通过 `GET /api/gateway/recommend` 得到推荐配置。

## 操作步骤

### 1. 估算单实例最大并发

```python
# 在部署机 backend 目录运行
import os, sys
sys.path.insert(0, '.')
os.environ.setdefault('PYTHONPATH', '.')

from app.gateway.vram import max_concurrent, estimate_kv_cache_vram, _model_vram_gb

# 假设 4 卡 × 80GB, 32B q4 模型
gpu_count = 4
vram_per_gpu = 80
model_vram = _model_vram_gb(32.0, "q4")  # ≈ 16 GB
kv_per_req = estimate_kv_cache_vram(32.0, "q4", 32768)
max_c = max_concurrent(gpu_count, vram_per_gpu, model_vram, kv_per_req)
print(f"单实例理论并发上限: {max_c}")
```

### 2. 调优单实例参数

编辑 `.env`：

```bash
# 单卡场景：最大化 KV cache
MAX_MODEL_LEN=16384                # 默认 32768；降到 16K 可多放一倍并发
GPU_MEMORY_UTILIZATION=0.92        # 默认 0.90；预留 8% 给系统
MAX_FIX_ROUNDS=5                  # 每个生成任务最多 5 轮修复
```

### 3. 多实例水平扩容（推荐做法）

#### 3.1 启动多个后端实例

`docker-compose.yml` 扩展为多服务：

```yaml
services:
  backend-1:
    image: local-agent-backend:latest
    environment:
      - INFERENCE_BACKEND=vllm
      - MODEL_PATH=/models/qwen2.5-coder-32b-instruct-q4.gguf
      - QUANTIZATION=q4
      - TENSOR_PARALLEL_SIZE=1
      - DATABASE_URL=sqlite:///./data/app.db
      - AUDIT_LOG_PATH=/data/audit
    volumes:
      - ./data:/data
      - /models:/models:ro
    ports: ["8001:8000"]
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              capabilities: [gpu]
              count: 1

  backend-2:
    # 同上，端口 8002
    ports: ["8002:8000"]
```

#### 3.2 前置 nginx 负载均衡

```nginx
upstream local_agent_backend {
    least_conn;
    server 127.0.0.1:8001 max_fails=3 fail_timeout=30s;
    server 127.0.0.1:8002 max_fails=3 fail_timeout=30s;
    # 后续扩容按相同模板追加
}

server {
    listen 8000;
    location / {
        proxy_pass http://local_agent_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;             # SSE 流式必需
        proxy_read_timeout 600s;        # 长生成任务不超时
    }
}
```

#### 3.3 多实例共享数据

- **SQLite**：单写者模式；多写者需切到 PostgreSQL（修改 `DATABASE_URL`）。
- **审计日志**：所有实例写同一 `AUDIT_LOG_PATH` 目录，按 PID 分文件追加；哈希链仅在同实例内严格保序。
  如需跨实例严格保序，需切到 PostgreSQL + 中央化 audit writer。
- **知识库（Chroma）**：Chroma 不支持多写者；建议只读挂载给 backend-2，所有入库走 backend-1。
- **工作区**：多实例需共享 `WORKSPACE_ROOT` 网络存储（NFS / 共享卷）。

### 4. 验证并发能力

```bash
# 3 用户并发压测
for i in 1 2 3; do
  curl -s -X POST $BASE_URL/api/agent/generate \
    -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"prompt":"生成一个 hello world Python 脚本","language":"python"}' \
    --max-time 120 > /tmp/agent_$i.json &
done
wait
ls -lh /tmp/agent_*.json
```

预期：3 个请求全部在 120s 内返回非超时响应。

## 验证

1. Nginx 健康检查：

   ```bash
   curl -s http://127.0.0.1:8000/api/health
   # {"status":"ok"}
   ```

2. 两实例都被命中（看 `docker compose logs backend-1 backend-2`）：

   ```bash
   docker compose logs --tail=20 backend-1 | grep "GET /api/health"
   docker compose logs --tail=20 backend-2 | grep "GET /api/health"
   ```

3. AC-1 验收脚本：见 `backend/tests/test_acceptance_concurrency.py`。

## 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 部分请求 504 超时 | 单实例 KV 占满 | 调小 `MAX_MODEL_LEN`；扩容实例数 |
| SQLite database is locked | 多写者争抢 | 切 PostgreSQL；或单写者模式 |
| 审计链 hash 校验失败 | 多实例并发写同一 jsonl | 按 PID 分文件；或切中央 audit writer |
| 知识库入库失败（Chroma） | 多实例并发写 Chroma | 仅 backend-1 可写，backend-2 只读挂载 |
| GPU 利用率不均 | nginx 轮询不感知 GPU 占用 | 切换为 `least_conn`；或部署 vLLM router |
| 单实例 OOM | `GPU_MEMORY_UTILIZATION` 过高 | 降到 0.85；或加 GPU 数 |

## 扩容决策树

```
3 人并发不超时？
├── 是 → 无需扩容
└── 否
    ├── 单实例 KV 占满？→ 调小 MAX_MODEL_LEN / 升级 GPU 显存
    └── 单实例 CPU/IO 阻塞？→ 起多实例 + nginx 负载均衡
        ├── SQLite 锁竞争？→ 切 PostgreSQL
        └── Chroma 写冲突？→ 单写者模式
```

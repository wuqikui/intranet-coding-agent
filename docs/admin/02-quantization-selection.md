# 量化等级选择

## 概述

本系统支持多种量化等级（fp16 / fp8 / q8 / q5 / q4 / awq / gptq），量化越低显存占用越小但精度损失越大。
管理员需根据 GPU 显存与业务精度需求选择合适量化等级，并通过 `QUANTIZATION` 环境变量或 `.env` 文件配置。

系统内置 VRAM 估算器，可基于模型参数量与量化等级估算权重显存与 KV cache 占用，辅助决策。

## 前置条件

- 已知部署机 GPU 型号与单卡显存（GB）。
- 已知目标模型参数量（B）。常用：7B / 14B / 32B / 70B。
- 已通过 `docs/admin/01-temporary-egress-model-import.md` 完成模型权重导入。

## 操作步骤

### 1. 查询当前推理配置

```bash
curl -s $BASE_URL/api/admin/stats -H "Authorization: Bearer $TOKEN"
```

响应示例：

```json
{
  "inference_backend": "mock",
  "model_name": "qwen2.5-coder-32b-instruct",
  "model_path": "/models/qwen2.5-coder-32b",
  "quantization": "q4",
  "auth_mode": "local",
  "maintenance_mode": false
}
```

### 2. 调用推荐接口

```bash
# 单卡 24GB
curl -s "$BASE_URL/api/gateway/recommend?gpu_count=1&vram_per_gpu_gb=24"
```

响应示例：

```json
{
  "model": "qwen2.5-coder-14b-instruct",
  "quantization": "q8",
  "estimated_vram_gb": 14.0,
  "tp_size": 1
}
```

推荐策略（内置）：

| 单卡显存 | 推荐模型 | 推荐量化 | 估算权重显存 |
|----------|----------|----------|--------------|
| ≥40 GB | 32B | q4 | 16 GB |
| ≥20 GB | 14B | q8 | 14 GB |
| <20 GB | 7B | q4 | 3.5 GB |
| 多卡总 ≥160 GB | 70B | q4 | 35 GB |
| 多卡总 ≥80 GB | 32B | q4 | 16 GB |

### 3. 估算 KV cache 与并发数

可选：调用 `vram.py` 内部估算器（Python 直调）：

```python
from app.gateway.vram import estimate_kv_cache_vram, max_concurrent

# 32B 模型 + q4 + 32K 上下文
kv = estimate_kv_cache_vram(model_params_b=32.0, quantization="q4", context_len=32768)
print(f"KV cache per request ≈ {kv:.2f} GB")

# 4 卡 × 80GB, 模型 35GB, KV 6GB → 并发数
c = max_concurrent(gpu_count=4, vram_per_gpu_gb=80, model_vram_gb=35, kv_per_request_gb=6)
print(f"max concurrent ≈ {c}")
```

### 4. 配置量化等级

编辑 `.env` 文件：

```bash
# 编辑部署机 .env
vi /opt/local-agent/.env
```

修改以下行：

```bash
INFERENCE_BACKEND=vllm        # vllm | llama_cpp | mock
MODEL_NAME=qwen2.5-coder-32b-instruct
MODEL_PATH=/models/qwen2.5-coder-32b-instruct-q4.gguf
QUANTIZATION=q4                # awq | gptq | fp8 | q4 | q5 | q8 | fp16
TENSOR_PARALLEL_SIZE=1         # 单卡=1；多卡按 GPU 数
MAX_MODEL_LEN=32768
GPU_MEMORY_UTILIZATION=0.90
```

### 5. 重启后端使配置生效

```bash
# systemd 部署
sudo systemctl restart local-agent-backend

# docker compose 部署
docker compose restart backend
```

## 验证

1. `GET /api/gateway/health` 返回非 mock backend：

   ```bash
   curl -s $BASE_URL/api/gateway/health
   # {"backend":"vllm","healthy":true,"model":"qwen2.5-coder-32b-instruct"}
   ```

2. 发起一次非流式 chat 验证模型可用：

   ```bash
   curl -s -X POST $BASE_URL/api/gateway/chat \
     -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"messages":[{"role":"user","content":"hello"}]}'
   ```

3. 观察进程显存占用（应在 `GPU_MEMORY_UTILIZATION` 阈值内）：

   ```bash
   nvidia-smi --query-gpu=memory.used,memory.total --format=csv
   ```

## 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 启动后 backend 仍为 mock | `.env` 未被加载或 INFERENCE_BACKEND 未改 | 检查 `printenv INFERENCE_BACKEND`；重启进程 |
| OOM 启动失败 | 量化等级过高（如 32B + fp16 在单 24GB 卡） | 降到 q4 或 q5；或启用 TP=`<gpu_count>` |
| 推理慢、吞吐低 | KV cache 占用过高 / 并发 > max_concurrent | 调小 `MAX_MODEL_LEN`；限制前端并发 |
| 输出乱码 | 量化等级过低（如 q4 对 14B 模型精度损失过大） | 升级到 q5 或 q8 |
| 模型路径不存在 | `MODEL_PATH` 与实际文件名不符 | 核对 `ls -lh $MODEL_PATH` |

## 选型建议

- **代码生成场景（首要）**：32B + q4 是性价比最佳点；多卡部署可上 70B + q4。
- **轻量任务/调试**：7B + q4 在 8GB 显卡可运行。
- **审计/合规要求高精度**：32B + q8 或 fp16（需 ≥80GB 显存）。
- **awq vs gptq**：awq 推理略快、精度相当；vLLM 两者都支持，按可用权重选择。

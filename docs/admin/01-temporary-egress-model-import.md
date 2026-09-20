# 临时开网下模型

## 概述

本系统默认运行在纯内网（airgap）模式，所有出网请求被 `EgressGuard` 阻断。
管理员可通过"维护模式"临时放行出网流量，从可信源下载模型/包文件，下载完成后立即关闭。

整个流程被审计日志全程记录，文件落地前必须通过 sha256 校验。

## 前置条件

- 已部署系统并具备 admin 账号（首次运行向导步骤 6 已完成）。
- 已知目标资源的 URL 与 sha256（来自上游模型仓库或可信镜像站）。
- 部署机具备出网能力（防火墙/网闸已临时放行目标域名）。
- 目标目录可写：
  - 模型：`MODEL_REPO_PATH`（默认 `/models`）
  - 包：`PACKAGE_REPO_PATH`（默认 `/packages`）

## 操作步骤

### 1. 登录获取 admin token

```bash
export BASE_URL=http://127.0.0.1:8000
export TOKEN=$(curl -s -X POST $BASE_URL/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<admin-password>"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo "TOKEN=$TOKEN"
```

验证：`echo $TOKEN` 应输出非空 JWT。

### 2. 开启出网维护模式

```bash
curl -s -X POST $BASE_URL/api/admin/maintenance \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"enable": true}'
```

预期响应：`{"maintenance_mode": true}`

### 3. 触发模型导入

```bash
curl -s -X POST $BASE_URL/api/admin/import-model \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://<trusted-host>/qwen2.5-coder-32b-instruct-q4.gguf",
    "sha256": "<expected-sha256-64-hex>",
    "kind": "model",
    "filename": "qwen2.5-coder-32b-instruct-q4.gguf"
  }'
```

预期响应：

```json
{
  "path": "/models/qwen2.5-coder-32b-instruct-q4.gguf",
  "sha256": "<actual-sha256>",
  "size_bytes": 19700000000,
  "kind": "model"
}
```

> 若 sha256 不匹配，后端会返回 400 + `sha256 校验失败`，并自动删除 `.part` 临时文件。

### 4. 关闭出网维护模式（关键）

下载完成**必须立即关闭**，恢复 airgap：

```bash
curl -s -X POST $BASE_URL/api/admin/maintenance \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"enable": false}'
```

预期响应：`{"maintenance_mode": false}`

## 验证

1. 查询维护状态确认为关闭：

   ```bash
   curl -s $BASE_URL/api/admin/maintenance -H "Authorization: Bearer $TOKEN"
   # {"maintenance_mode": false}
   ```

2. 查询审计日志，应能看到 `maintenance_enable` → `model_import` → `maintenance_disable` 三条记录，时间戳与操作人 ID 一致：

   ```bash
   curl -s "$BASE_URL/api/admin/audit?limit=10" -H "Authorization: Bearer $TOKEN"
   ```

3. 文件已落地：

   ```bash
   ls -lh /models/qwen2.5-coder-32b-instruct-q4.gguf
   ```

4. 重新加载模型（重启后端进程或调 `POST /api/admin/refresh`）后，`GET /api/gateway/health` 返回的 `backend` 字段应从 `mock` 切换为 `vllm` 或 `llama_cpp`。

## 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 403 出网维护模式未开启 | 未先开启维护模式就调 import-model | 重新执行步骤 2 |
| 400 sha256 校验失败 | 上游文件被篡改或 URL 错误 | 核对 sha256；不可重试；重新获取可信 URL |
| 400 下载失败 | 防火墙未放行 / 上游不可达 | 检查出网代理；维护模式下手动 `curl -I <url>` 验证可达 |
| 500 移动文件失败 | 目标目录不可写或磁盘满 | `df -h $MODEL_REPO_PATH`；`chmod` 修正权限 |
| 维护模式关闭后仍能访问外网 | flag 文件未刷新 | 检查 `data/maintenance.flag` 内容应为 `false`；重启进程 |

## 安全约束

- 维护模式总开关持久化在 `data/maintenance.flag`；进程重启后仍生效，**操作完务必手动关闭**。
- 每次出网访问都写审计，包括 URL 与 host，便于事后追溯。
- 内网/本地 URL 不受维护模式限制；本接口只对真正外网 URL 强制校验。

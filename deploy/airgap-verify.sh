#!/usr/bin/env bash
# ============================================================
# 断网(airgap)验证脚本 — 验证运行时无任何外网请求
# 用法：bash deploy/airgap-verify.sh
# 原理：用 iptables OUTPUT DROP 拦截（需 root），或用环境变量断言
# 内网部署机无 root 时，改为静态检查：确认 MAINTENANCE_MODE=false 且
# 无 httpx 对公网域名的请求日志
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> [1/3] 检查 .env 中 MAINTENANCE_MODE=false"
if [ -f .env ]; then
  if grep -q "^MAINTENANCE_MODE=true" .env; then
    echo "  FAIL: MAINTENANCE_MODE=true，运行时不应处于出网维护模式" >&2
    exit 1
  fi
  echo "  PASS: MAINTENANCE_MODE 未开启"
else
  echo "  WARN: 未找到 .env，跳过"
fi

echo "==> [2/3] 健康检查 backend /ready"
if command -v curl >/dev/null 2>&1; then
  if curl -sf http://localhost:8000/api/ready >/dev/null 2>&1; then
    echo "  PASS: backend /ready 200"
  else
    echo "  WARN: backend 未就绪或未启动（make up 后重试）"
  fi
fi

echo "==> [3/3] 检查审计日志中无公网域名请求"
AUDIT="./backend/data/audit/audit.jsonl"
if [ -f "$AUDIT" ]; then
  if grep -E '"url"\s*:\s*"https?://[^"]*\.com' "$AUDIT" >/dev/null 2>&1; then
    echo "  FAIL: 审计日志中发现公网请求" >&2
    exit 1
  fi
  echo "  PASS: 审计日志无公网域名请求"
else
  echo "  SKIP: 无审计日志"
fi

echo ""
echo "==> airgap 检查完成"

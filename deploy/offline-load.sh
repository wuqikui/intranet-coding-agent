#!/usr/bin/env bash
# ============================================================
# 离线镜像加载脚本 — 在内网机上执行
# 前提：已将 deploy/images/*.tar 拷入本机
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

IMAGES_DIR="$ROOT/deploy/images"
if [ ! -d "$IMAGES_DIR" ]; then
  echo "错误：找不到 $IMAGES_DIR，请先在有网机执行 offline-build.sh 并拷贝整个 deploy/ 目录" >&2
  exit 1
fi

echo "==> 加载离线镜像 tar"
for tar in "$IMAGES_DIR"/*.tar; do
  [ -f "$tar" ] || continue
  echo "  - $tar"
  docker load -i "$tar"
done

echo ""
echo "==> 已加载镜像："
docker images | grep intranet-coding-agent || true

echo ""
echo "下一步："
echo "  1. cp .env.example .env  并按内网实际填写 MODEL_REPO_PATH/显存/知识库路径"
echo "  2. make up"
echo "  3. make init  (首次运行向导)"

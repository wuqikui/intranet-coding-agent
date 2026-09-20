#!/usr/bin/env bash
# ============================================================
# 离线镜像构建脚本 — 在有网维护机上执行
# 产出：deploy/images/*.tar  拷入内网后执行 offline-load.sh
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

IMAGES_DIR="$ROOT/deploy/images"
mkdir -p "$IMAGES_DIR"

# 镜像列表（与 docker-compose.yml 对齐）
IMAGES=(
  "intranet-coding-agent-frontend:latest"
  "intranet-coding-agent-backend:latest"
  "intranet-coding-agent-inference:latest"
  "intranet-coding-agent-sandbox:latest"
)

echo "==> [1/4] 构建全部镜像（使用内网镜像源）"
export DOCKER_BUILDKIT=1
docker compose build

echo "==> [2/4] 为 sandbox 镜像单独打标签（docker-compose 使用 tools/sandbox）"
docker tag "$(docker compose config --images sandbox | head -1)" "intranet-coding-agent-sandbox:latest" 2>/dev/null || true

echo "==> [3/4] 导出镜像为 tar"
for img in "${IMAGES[@]}"; do
  out="$IMAGES_DIR/$(echo "$img" | cut -d: -f1).tar"
  echo "  - $img -> $out"
  docker save -o "$out" "$img"
done

echo "==> [4/4] 导出 .env.example 与 docker-compose.yml 供内网使用"
cp "$ROOT/docker-compose.yml" "$IMAGES_DIR/../"
cp "$ROOT/.env.example" "$IMAGES_DIR/../"

echo ""
echo "完成。将整个 deploy/ 目录拷贝到内网机后执行："
echo "  cd /path/to/deploy && bash offline-load.sh"
echo "随后编辑 .env，运行 make up"

# 内网离线 AI Coding Agent - 顶层 Makefile
# 用法: make help

.PHONY: help up down build rebuild logs logs-backend logs-frontend logs-inference init clean test

help: ## 显示所有可用目标
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

up: ## 启动全部服务
	docker compose up -d

down: ## 停止全部服务
	docker compose down

build: ## 构建全部镜像
	docker compose build

rebuild: ## 强制重新构建镜像 (不使用缓存)
	docker compose build --no-cache

logs: ## 查看全部日志 (实时)
	docker compose logs -f

logs-backend: ## 查看后端日志
	docker compose logs -f backend

logs-frontend: ## 查看前端日志
	docker compose logs -f frontend

logs-inference: ## 查看推理日志
	docker compose logs -f inference

init: ## 首次运行向导 (检测 GPU / 导入模型 / 建索引 / 创建管理员)
	docker compose exec backend python -m app.init_wizard

clean: ## 清理数据卷 (谨慎!)
	docker compose down -v

test: ## 运行后端测试
	docker compose exec backend python -m pytest tests/ -v

# --- 前端开发模式 (不通过 Docker, 本机热重载) ---
dev-frontend: ## 前端开发模式 (本机 npm run dev)
	cd frontend && npm run dev

dev-backend: ## 后端开发模式 (本机 uvicorn 热重载)
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

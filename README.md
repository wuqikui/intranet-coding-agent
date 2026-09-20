# 内网离线 AI Coding Agent

> 纯内网、多用户、类 Trae 的 AI Coding Agent 系统 —— 面向公司内网的团队级代码生成协作平台。
>
- **版本**：v0.1（初版，功能闭环已走通）
- **部署形态**：Docker Compose 一键部署，全链路内网运行，默认禁止一切外网访问
- **硬件目标**：带先进 GPU 的台式机 / 工作站（笔记本仅用于开发对话，不要求本地运行）

---

## 目录

- [一、它能做什么](#一它能做什么)
- [二、系统架构](#二系统架构)
- [三、技术栈](#三技术栈)
- [四、目录结构](#四目录结构)
- [五、快速开始（三种部署路径）](#五快速开始三种部署路径)
- [六、首次运行向导](#六首次运行向导)
- [七、操作指南](#七操作指南)
- [八、API 一览](#八api-一览)
- [九、角色与权限（RBAC）](#九角色与权限rbac)
- [十、配置说明（.env）](#十配置说明env)
- [十一、工程循环、沙箱与危险命令](#十一工程循环沙箱与危险命令)
- [十二、测试](#十二测试)
- [十三、管理员文档](#十三管理员文档)
- [十四、当前能力边界与路线图](#十四当前能力边界与路线图)
- [十五、常见问题（FAQ）](#十五常见问题faq)
- [十六、安全须知](#十六安全须知)

---

## 一、它能做什么

### 核心能力

1. **多用户协作**：JWT 登录 + 三角色 RBAC（`admin` / `dev` / `readonly`），预留 LDAP/AD 域控对接；敏感操作全部写入 **SHA-256 哈希链防篡改审计日志**。
2. **全栈项目生成**：输入自然语言需求，Agent 经 7 节点管线（规划→检索→架构→生成→写盘→构建→收尾）产出 **CMake + C++ / FastAPI(Python) / React(TypeScript)** 全栈项目。
3. **强制工程循环（N≥5 轮）**：真实调用编译器构建 → 解析 gcc/clang/cmake/linker/MSVC 错误 → 规则自动修复（补 include、补 `std::`、补分号等）→ 重新编译，直到通过或耗尽轮次；规则修复之外再附 1 轮 LLM 兜底修复（共 2N+1 轮）。
4. **四通道隔离知识库**：业务（business）/ 代码（code）/ 构建运维（buildops）/ 数据接口（dataapi）四库物理隔离，禁止跨库混检。
5. **代码风格保真**：从知识库 code 通道提取团队既有风格（缩进、命名、注释语言、行宽、花括号、分号、docstring），生成代码写盘前做一致性检查与最小化安全重写；无样本时回退语言社区默认（PEP8 / Google C++ Style 等）。
6. **模型网关**：OpenAI 兼容接口统一封装 **vLLM / llama.cpp** 两种推理后端，支持主模型 + fallback 降级、SSE 流式输出、显存自适应推荐、并发限流。
7. **沙箱工具集**：文件读写、目录列举、全局搜索（文件名 + 内容 grep）、shell、git diff、跑测试、lint 共 8 个工具，全部带 RBAC；危险命令三级分类（block 恒拒 / warn 二次确认 / safe 放行）。
8. **纯内网离线**：出口守卫（EgressGuard）默认拒绝所有外网请求；仅管理员可临时开启"维护模式"导入模型与依赖包，导入强制 **sha256 校验**，全程审计。
9. **首启向导**：8 步引导（GPU 检测 → 模型推荐 → 权重导入 → 知识库导入 → 建索引 → 创建管理员 → 员工开户 → 完成），状态持久化、失败可从任意步骤重试。
10. **无 GPU 也能体验**：内置 `mock` 推理后端，零网络、零模型依赖即可启动全系统，用于功能演示与测试。

### 编程语言支持（分层说明）

| 层次 | 支持范围 | 说明 |
|---|---|---|
| 代码生成 | 通用语言（模型会写即可） | Python、C/C++、Go、Rust、Java、JavaScript/TypeScript 等 |
| 风格保真 | Python、C++、JavaScript、TypeScript、Go、Rust、Java 共 7 种有显式画像 | 未知语言回退默认；有知识库样本时以样本为准 |
| 强制编译修复闭环 | **C/C++（CMake / Make）+ npm 前端** 深度支持 | Cargo(Rust) 可识别工程类型但错误修复规则未实现；Python/Go/Java 可生成、可运行，暂无专用编译修复规则 |

> 沙箱镜像本身预装了 gcc/g++/cmake/clang/clang-tidy/cppcheck、Python3、Node.js、Go 工具链。

---

## 二、系统架构

```
┌──────────────────────────────────────────────────────────────────┐
│                         用户浏览器（内网）                         │
│            React 18 + TypeScript + Vite + AntD 5                 │
│   Login │ Chat(SSE 流式) │ Admin(用户/知识库/模型/维护/审计)      │
└──────────────────────────────┬───────────────────────────────────┘
                               │ HTTP / SSE（nginx 反向代理 /api/）
┌──────────────────────────────▼───────────────────────────────────┐
│                        backend（FastAPI）                         │
│  鉴权 RBAC │ 模型网关 │ 知识库 API │ 工具 API │ 向导 │ 审计        │
│                                                                  │
│  ┌─────────────────── Coding Agent 生成管线 ──────────────────┐  │
│  │ planner → retriever → architect → generator → writer      │  │
│  │                                   → runner  ⇄ generator   │  │
│  │                                      ↓ 成功                │  │
│  │                                   finalizer               │  │
│  │ （langgraph 不可用时自动降级为内置 FallbackGraph）          │  │
│  └───────────────┬───────────────────────────┬───────────────┘  │
│                  │                            │                  │
│        ┌─────────▼─────────┐        ┌─────────▼─────────┐        │
│        │ 四通道知识库       │        │ 沙箱 + 工程循环    │        │
│        │ Chroma 向量       │        │ process/docker/   │        │
│        │ + SQLite 符号图谱 │        │ rootless 三模式    │        │
│        │ (符号优先/向量兜底)│       │ 危险命令拦截       │        │
│        └───────────────────┘        └─────────┬─────────┘        │
│  SQLite(用户/会话) + 哈希链审计 JSONL        │ 真实编译执行       │
└──────────────────────────────────┬───────────┘                  │
                                   │ OpenAI 兼容协议               │
┌──────────────────────────────────▼───────────────────────────────┐
│              inference（独立容器，可挂载 GPU）                    │
│        vLLM / llama.cpp 反代 ｜ mock 零依赖降级                   │
└──────────────────────────────────────────────────────────────────┘

         ┌──────────── 出口守卫 EgressGuard（默认全禁）────────────┐
         │ 仅 maintenance_mode=true 时放行 sha256 校验导入         │
         └────────────────────────────────────────────────────────┘
```

**四个容器**（见 `docker-compose.yml`）：

| 服务 | 端口（默认） | 作用 |
|---|---|---|
| frontend | 3000 | nginx 托管 React 构建产物，反向代理 `/api/` 到后端 |
| backend | 8000 | FastAPI 主服务（REST + SSE） |
| inference | 8001 | 推理网关（vLLM / llama.cpp / mock） |
| sandbox | — | 用户代码编译/运行环境（Ubuntu 22.04 多工具链，降权运行） |

---

## 三、技术栈

**后端**

- Python 3.11+ · FastAPI · uvicorn · SSE（sse-starlette）
- SQLAlchemy 2 + aiosqlite（用户/会话）· SQLite（代码符号图谱）
- ChromaDB（向量库，chromadb 未安装时自动降级为内存兜底）· sentence-transformers（本地 embedding，禁用 Chroma 默认联网 embedding）
- langgraph（可选，不可用时自动降级为内置 FallbackGraph）· langchain-core
- python-jose（JWT）· passlib/bcrypt · ldap3（LDAP/AD 预留）
- httpx（推理网关 / 模型导入）

**前端**

- React 18 · TypeScript 5 · Vite 5 · Ant Design 5 · Zustand
- Monaco Editor · xterm 终端 · react-diff-viewer · eventsource-parser（SSE）
- 构建产物由 nginx 1.27 托管

**基础设施**

- Docker Compose v2 · NVIDIA Container Toolkit（GPU 直通）
- 离线镜像 tar 导入导出 · Makefile 统一入口

---

## 四、目录结构

```
local_agent/
├── backend/                  # FastAPI 后端
│   ├── app/
│   │   ├── main.py           # 应用入口（8 个 router 聚合）
│   │   ├── config.py         # 全部配置（.env 驱动）
│   │   ├── init_wizard.py    # 首次运行向导（CLI）
│   │   ├── agent/            # Coding Agent
│   │   │   ├── graph.py      # 7 节点图 + FallbackGraph 降级
│   │   │   ├── build_loop.py # 工程循环：编译→解析→修复（N≥5）
│   │   │   ├── style.py      # 代码风格画像/检测/重写
│   │   │   ├── workspace.py  # 项目工作区
│   │   │   ├── api.py        # /api/agent/* 路由
│   │   │   └── nodes/        # planner/retriever/architect/generator/writer/runner/finalizer
│   │   ├── api/              # auth/gateway/knowledge/admin/tools/wizard/health 路由
│   │   ├── auth/             # JWT、本地账户、LDAP、RBAC
│   │   ├── gateway/          # 模型网关 + vllm/llama_cpp/mock 适配器 + 显存/限流
│   │   ├── knowledge/        # 四通道管理器/向量库/符号图谱/分类器/本地 embedder
│   │   ├── maintenance/      # 出口守卫 + sha256 离线导入器
│   │   ├── sandbox/          # 沙箱运行器 + 危险命令检测器
│   │   ├── audit/            # 哈希链审计日志 + 校验器
│   │   └── models/           # SQLAlchemy 异步引擎
│   ├── scripts/
│   │   └── verify_audit_chain.py   # 审计哈希链完整性校验工具
│   ├── tests/                # pytest 测试集（含验收测试）
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/                 # React 前端
│   ├── src/
│   │   ├── pages/            # Login / Chat / Admin
│   │   ├── components/       # CodeEditor / FileTree / TaskBoard / Terminal
│   │   ├── layouts/ store/ api/
│   └── Dockerfile · nginx.conf · vite.config.ts
├── inference/                # 推理网关容器（vLLM/llama.cpp 反代 + mock）
├── tools/sandbox/            # 沙箱镜像（多语言工具链）
├── deploy/                   # 离线部署脚本
│   ├── offline-build.sh      # 有网维护机：build + save 镜像 tar
│   ├── offline-load.sh       # 内网机：load 镜像
│   └── airgap-verify.sh      # 断网验证
├── docs/admin/               # 管理员文档（5 篇）
├── docker-compose.yml
├── Makefile
└── .env.example
```

---

## 五、快速开始（三种部署路径）

### 路径 A：Docker Compose 一键部署（内网机能访问内部镜像源 / 已 load 镜像时）

**前置条件**

- Docker Engine 24+ 与 Docker Compose v2
- 如需 GPU：安装 NVIDIA 驱动 + NVIDIA Container Toolkit
- 已通过离线方式获得镜像（见路径 B），或内网有镜像仓库

```bash
# 1. 准备配置
cp .env.example .env
#    编辑 .env：至少修改 JWT_SECRET、INFERENCE_BACKEND、MODEL_NAME/MODEL_PATH、SANDBOX_MODE

# 2. 启动全部服务
make up            # 等价于 docker compose up -d

# 3. 查看状态与日志
docker compose ps
make logs          # 实时全部日志

# 4. 首次运行向导（GPU 检测/导模/建库/建管理员）
make init

# 5. 打开浏览器
#    前端：http://<部署机IP>:3000
#    后端 API 文档（Swagger）：http://<部署机IP>:8000/docs
```

停止 / 清理：

```bash
make down          # 停止（保留数据卷）
make clean         # 停止并删除数据卷（谨慎！知识库/用户/审计全部清除）
```

### 路径 B：纯离线（airgap）部署 —— 推荐的生产路径

在**有网的维护机**与**内网部署机**之间用移动介质搬运：

```bash
# ① 有网维护机：构建并导出 4 个镜像 tar
bash deploy/offline-build.sh
#    产物：deploy/images/*.tar（frontend/backend/inference/sandbox）

# ② 将整个项目目录（含 deploy/、docker-compose.yml、.env.example）拷入内网机

# ③ 内网部署机：加载镜像
bash deploy/offline-load.sh

# ④ 配置并启动
cp .env.example .env      # 填写 GPU/模型/知识库路径
make up
make init

# ⑤ 断网自检（确认无任何公网请求）
bash deploy/airgap-verify.sh
```

模型权重离线导入：管理员在维护窗口期临时开启出网模式后导入（也可直接将权重复制到 `MODEL_REPO_PATH`），详见 [管理员文档 01](docs/admin/01-temporary-egress-model-import.md)。

### 路径 C：本机开发模式（调试用，无需 Docker）

**后端**（推荐 Python 3.11）：

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux: source .venv/bin/activate
pip install -r requirements.txt
copy ..\.env.example .env           # 在 backend/ 下放一份 .env（compose 用根目录的 .env）
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# 也可直接：make dev-backend
```

**前端**（Node.js 20+）：

```bash
cd frontend
npm install
npm run dev          # http://localhost:3000，热重载；也可 make dev-frontend
```

> 开发模式下保持 `INFERENCE_BACKEND=mock` 即可在没有 GPU、没有模型权重的情况下跑通完整业务流程。

---

## 六、首次运行向导

向导 8 个步骤，每步独立保存状态到 `data/wizard.json`，**失败可从任意步骤重试**：

| 序号 | 步骤 | 内容 |
|---|---|---|
| 1 | gpu_detect | 检测 GPU 数量与显存（无 nvidia-smi 时按 0 卡处理，可继续用 mock） |
| 2 | recommend_model | 依据显存推荐模型尺寸与量化档位（复用网关显存策略） |
| 3 | import_weights | 扫描 `MODEL_REPO_PATH` 下已有权重并注册 |
| 4 | import_kb | 导入已有文档并自动分类到四通道 |
| 5 | build_index | 建立向量索引 + 代码符号索引 |
| 6 | create_admin | 创建管理员账户（已存在则跳过） |
| 7 | open_users | 员工开户（提示管理员后续在管理页逐个办理） |
| 8 | done | 完成 |

三种使用方式：

```bash
make init                                  # 容器内交互式跑全部步骤
cd backend && python -m app.init_wizard            # 本机直接运行
cd backend && python -m app.init_wizard --step build_index   # 从指定步骤重试
cd backend && python -m app.init_wizard --reset             # 重置向导状态
```

也可通过 API 操作（首启允许匿名；重置必须 admin）：

- `GET  /api/wizard/state` 查询进度
- `POST /api/wizard/run` 执行步骤
- `POST /api/wizard/reset` 重置（仅 admin）

---

## 七、操作指南

### 7.1 登录与日常使用

1. 浏览器访问前端 `http://<部署机IP>:3000`，使用管理员或 dev 账户登录。
2. 进入 **Chat 页**，用自然语言描述需求，例如：
   > "生成一个计算器全栈示例：C++ 计算内核用 CMake 构建，FastAPI 提供 HTTP 接口，React 做前端页面。"
3. Agent 以 SSE 流式推送每个节点的进展（规划 / 检索 / 架构 / 生成 / 编译修复 / 收尾）。
4. 生成结束后可在右侧 **FileTree** 浏览文件树、**CodeEditor** 查看文件内容、**Terminal** 查看构建日志。
5. 项目文件持久化在后端工作区（`data/workspace/<project_id>/`，Docker 部署时为 `workspace-data` 卷）。

### 7.2 知识库入库（管理员 / dev）

- 界面：**Admin 页 → 知识库**，选择通道后上传文档。
- API：`POST /api/knowledge/ingest`，body 中指定 `channel`（`business` / `code` / `buildops` / `dataapi`），开启 `auto_classify` 时系统自动判别文档归属通道。
- **建议**：把团队既有代码放入 **code 通道**——系统会建立符号图谱并提取风格画像，后续生成代码自动贴合团队风格。
- 四库物理隔离，任何跨通道检索请求都会被拒绝（返回"禁止跨库混检"错误）。

### 7.3 用户管理（管理员）

- `POST /api/auth/users` 开户、`GET /api/auth/users` 列表、`DELETE /api/auth/users/{id}` 停用/删除。
- 角色：`admin`（全权）、`dev`（可生成/可写）、`readonly`（只读，禁用 shell 等写操作）。
- 对接域控：在 `.env` 设 `AUTH_MODE=ldap` 或 `ad` 并填写 LDAP 连接信息（接口已预留，实际域控联调需在部署环境验证）。

### 7.4 临时出网与模型导入（管理员）

1. `POST /api/admin/maintenance` 临时开启维护模式（出网守卫放行）。
2. `POST /api/admin/import-model` 提交资源 URL + **期望 sha256**，系统流式下载、边下边算哈希，校验失败自动删除并写审计，成功则入内网模型库/包仓库。
3. 导入完成后立即关闭维护模式，恢复全禁外网。
4. 全程操作可在 `GET /api/admin/audit` 审计页追溯。

### 7.5 审计与合规

- 审计日志：JSONL 追加写（`data/audit/audit.jsonl`），每条含前一条哈希，形成 SHA-256 哈希链。
- 完整性校验：

```bash
python backend/scripts/verify_audit_chain.py
# 或在容器内：docker compose exec backend python scripts/verify_audit_chain.py
```

- 系统就绪探针 `GET /api/ready` 会同步检查审计链完整性。

---

## 八、API 一览

后端 Swagger 交互式文档：`http://<部署机IP>:8000/docs`。主要端点（共 35+）：

| 模块 | 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|---|
| 健康 | GET | `/api/health` `/api/ready` | 匿名 | 存活探针 / 就绪探针（含审计链检查） |
| 鉴权 | POST | `/api/auth/login` | 匿名 | 登录获取 JWT |
| 鉴权 | GET | `/api/auth/me` | 登录用户 | 当前用户信息 |
| 鉴权 | POST/GET/DELETE | `/api/auth/users[...]` | admin | 用户增删查 |
| 网关 | GET | `/api/gateway/models` `/health` `/recommend` | 登录用户 | 模型列表 / 网关健康 / 显存推荐 |
| 网关 | POST | `/api/gateway/chat` | 登录用户 | OpenAI 兼容对话（支持 SSE） |
| 知识库 | GET | `/api/knowledge/channels` `/stats` | 登录用户 | 通道列表 / 统计 |
| 知识库 | POST | `/api/knowledge/ingest` `/search` `/route-search` `/symbol-search` | dev+ | 入库 / 单通道检索 / 路由检索 / 符号检索 |
| Agent | POST | `/api/agent/generate` | admin/dev | 生成全栈项目（`stream:true` 走 SSE） |
| Agent | GET | `/api/agent/projects/{id}/files` `/file` | 登录用户 | 浏览生成结果 |
| 工具 | POST | `/api/tools/file/read` `/file/write` `/file/list` `/search` `/shell` `/git-diff` `/test` `/lint` | dev+（readonly 403） | 沙箱 8 工具 |
| 管理 | GET/POST | `/api/admin/maintenance` | admin | 出网维护模式开关 |
| 管理 | POST | `/api/admin/import-model` | admin | sha256 校验导入模型/包 |
| 管理 | GET | `/api/admin/stats` `/audit` `/users` | admin | 统计 / 审计查询 / 用户列表 |
| 向导 | GET/POST | `/api/wizard/state` `/run` | 首启匿名 | 向导状态/执行 |
| 向导 | POST | `/api/wizard/reset` | admin | 重置向导 |

---

## 九、角色与权限（RBAC）

| 能力 | admin | dev | readonly | 匿名（仅首启） |
|---|---|---|---|---|
| 登录 / 查看项目 | ✅ | ✅ | ✅ | ❌ |
| 对话生成项目（`/api/agent/generate`） | ✅ | ✅ | ❌ | ❌ |
| 文件读写、shell、测试、lint 等工具 | ✅ | ✅ | ❌（403） | ❌ |
| 知识库入库 | ✅ | ✅ | ❌ | ❌ |
| 知识库检索 / 浏览文件 | ✅ | ✅ | ✅ | ❌ |
| 用户管理 / 维护模式 / 导入模型 / 查审计 | ✅ | ❌ | ❌ | ❌ |
| 向导查询/执行（系统未初始化时） | ✅ | ✅ | ✅ | ✅ |
| 向导重置 | ✅ | ❌ | ❌ | ❌ |

越权访问统一返回 HTTP 403，并有验收测试守护（非 admin 访问管理端点必须被拒绝）。

---

## 十、配置说明（.env）

所有配置由根目录 `.env` 驱动（复制自 `.env.example`），关键项分组：

| 分组 | 关键变量 | 说明 |
|---|---|---|
| 端口 | `FRONTEND_PORT` / `BACKEND_PORT` / `INFERENCE_PORT` | 默认 3000 / 8000 / 8001 |
| 推理 | `INFERENCE_BACKEND` | `vllm` / `llama_cpp` / `mock`（无 GPU 演示用 mock） |
| 推理 | `MODEL_NAME` `MODEL_PATH` `QUANTIZATION` | 模型标识、权重路径、量化档位（awq/gptq/fp8/q4/q5/q8） |
| 推理 | `TENSOR_PARALLEL_SIZE` `MAX_MODEL_LEN` `GPU_MEMORY_UTILIZATION` | 多卡并行 / 上下文长度 / 显存利用率上限 |
| 显存策略 | `SINGLE_CARD_STRATEGY` `MULTI_CARD_STRATEGY` `MAX_CONCURRENT_REQUESTS` | 单卡推荐 32B_Q4，多卡推荐 70B_Q4；并发数 0=按显存自动 |
| 知识库 | `KB_*_PATH` `CHROMA_PERSIST_PATH` `SYMBOL_GRAPH_PATH` | 四库独立路径 / 向量持久化 / 符号图谱 |
| Embedding | `EMBEDDING_MODEL_PATH` `EMBEDDING_DIM` | 本地 embedding 模型（如 bge-large-zh，1024 维） |
| 沙箱 | `SANDBOX_MODE` | `process`（轻量）/ `docker`（强隔离，推荐生产）/ `rootless` |
| 工程循环 | `MAX_FIX_ROUNDS` | 规则修复轮数，默认 5（硬性要求 N≥5） |
| 鉴权 | `JWT_SECRET` `JWT_EXPIRE_MINUTES` `AUTH_MODE` | **生产必须改密钥**；认证模式 local/ldap/ad |
| 出网 | `MAINTENANCE_MODE` `MODEL_REPO_PATH` `PACKAGE_REPO_PATH` | 默认 false（全禁外网） |
| 审计 | `AUDIT_LOG_PATH` `AUDIT_HASH_SEED` | 审计目录与哈希链种子 |

> `.env` 已在 `.gitignore` 中，**严禁提交真实密钥与内网地址到仓库**。

---

## 十一、工程循环、沙箱与危险命令

### 工程循环（BuildLoop）

```
cmake -B build → cmake --build build → 解析错误（gcc/clang/cmake/linker/MSVC 正则）
     → AutoFixer 规则修复（缺 #include / 缺 std:: / 缺分号 / 缺 <cstring> …）
     → 重新编译，最多 N=5 轮
     → 仍失败则回 generator 做 1 轮 LLM 兜底修复，再跑 N 轮（合计最多 2N+1 轮）
```

附带 `CCPlusRuleScanner` 静态规则扫描（无 clang-tidy 时启用）：CMake 目标缺 `PUBLIC/PRIVATE` 可见性、裸 `new`（建议 `std::make_unique/shared` 的 RAII 写法）、常见未定义行为模式。

### 沙箱三种模式

- `process`：本机子进程执行（开发/轻量场景）。
- `docker`：每次在独立沙箱容器中执行，`no-new-privileges` 降权（**生产推荐**）。
- `rootless`：无根容器模式。

### 危险命令三级分类

| 级别 | 示例 | 处置 |
|---|---|---|
| **block**（不可逆） | `rm -rf /`、`mkfs`、`git push --force`、`DROP DATABASE` | 恒拒绝，即使带二次确认参数 |
| **warn**（高风险） | `rm -rf /tmp/xxx`、`git push`、`git commit --amend`、`DELETE FROM` | 阻断并要求显式 `confirmed=true` 才放行 |
| **safe** | `ls`、`cmake --build`、`make`、`grep`、`cat` 等 | 直接放行 |

路径穿越（如 `../../etc/passwd`）同样会被拦截。

---

## 十二、测试

```bash
cd backend
python -m pytest tests/ -v
# Docker 方式：make test
```

| 测试文件 | 覆盖内容 |
|---|---|
| `test_smoke_integration.py` | 35+ 端点冒烟（健康/鉴权/网关/知识库等） |
| `test_acceptance.py` | 验收用例 AC-1/2/3/4/6/10：3 人并发无超时、全栈项目生成、RBAC 越权拒绝、离线可用、通道隔离、审计可查 |
| `test_build_loop.py` | 工程循环、错误解析、AutoFixer、C++ 规则扫描 |
| `test_style.py` | 风格检测/一致性检查/自动重写（18 个用例，含风格一致性评分阈值） |
| `test_tools_rbac.py` | 危险命令 block/warn/safe、readonly 调 shell 返回 403、工具冒烟 |
| `test_audit.py` | 哈希链生成与校验 |
| `test_init_wizard.py` | 向导步骤状态与重试 |
| `test_admin_maintenance.py` | 维护模式查询 |

> v0.1 状态：上述测试在开发机全部通过（验收 6/6，冒烟无回归）。**Docker 镜像在带 GPU 部署机上的实际构建/运行验证留待部署现场执行**（构建脚本已就绪）。

---

## 十三、管理员文档

面向部署运维的 5 篇中文手册（`docs/admin/`）：

1. [临时出网与模型导入](docs/admin/01-temporary-egress-model-import.md)
2. [量化档位选择](docs/admin/02-quantization-selection.md)
3. [并发扩容](docs/admin/03-scale-concurrency.md)
4. [知识库备份](docs/admin/04-knowledge-base-backup.md)
5. [审计日志查看](docs/admin/05-audit-viewing.md)

---

## 十四、当前能力边界与路线图

### v0.1 已实现并验证

- 35+ 端点后端、React 管理/对话前端、4 容器 Compose 编排、离线镜像导入导出脚本
- 7 节点生成管线（langgraph 缺失时自动降级）、C/C++ + npm 工程强制编译修复循环
- 四通道隔离知识库（符号图谱优先 + 向量兜底）、7 语言风格画像与自动重写
- RBAC + JWT、哈希链审计、出网守卫 + sha256 导入、8 步首启向导
- mock 推理后端下 3 人并发验收通过

### 已知边界 / 后续完善（v0.2+ 候选）

- **部署机验证**：Docker 镜像 build、GPU 直通、vLLM 实际加载大模型权重需在带先进显卡的台式机上现场验证。
- **Agentic 代码探索**：当前知识库为"前置一次性检索"；计划让模型在生成过程中迭代调用 grep/glob/read 工具自主探索代码库（业界 coding agent 的主流做法），符号图谱作为快速定位入口。
- **更多语言的工程循环**：为 Rust(cargo)、Go(go test/build)、Python(pytest)、Java(maven/gradle) 补齐错误解析与自动修复规则。
- **LDAP/AD 真实域控联调**、>10 人规模压力测试。
- 沙箱 docker 模式与生成管线在生产配置（SANDBOX_MODE=docker）下的端到端联调。

---

## 十五、常见问题（FAQ）

**Q：没有 GPU / 暂时没有大模型，能先跑起来看看吗？**
A：可以。`.env` 保持 `INFERENCE_BACKEND=mock`，`make up` 即可启动全部界面与业务流程（生成内容为模板化占位，不具备真实智能），适合验收流程与培训。

**Q：启动后前端打不开 / 登录报网络错误？**
A：① `docker compose ps` 确认 backend 健康；② 确认访问的是前端 3000 端口（nginx 代理 `/api/`），不是直连 8000；③ 浏览器跨域直连场景用 `VITE_API_BASE` 指到后端。

**Q：知识库检索报"禁止跨库混检"？**
A：这是设计行为。每次检索必须且只能指定一个通道（business/code/buildops/dataapi），请检查请求中的 channel 参数。

**Q：导入模型时报"维护模式未开启"？**
A：出网守卫默认拦截一切外网。先由 admin 调 `POST /api/admin/maintenance` 开启维护模式，导入完成后记得关闭。

**Q：工程循环一直修复不过怎么办？**
A：查看 SSE 推送的编译日志与 AutoFixer 动作；超过 `MAX_FIX_ROUNDS`(默认 5) 后系统会做 1 轮 LLM 修复再试；仍失败时 finalizer 会给出完整错误报告，可据此手动调整需求或在 Chat 中追加约束重新生成。

**Q：审计链校验失败意味着什么？**
A：`/api/ready` 会显示 `audit_chain: broken`，说明审计日志可能被外部篡改或损坏（每条记录都含前一条的 SHA-256）。请用 `scripts/verify_audit_chain.py` 定位断裂位置并按安全事件流程处置。

**Q：开发机上后端起不来，提示 langgraph/chromadb 相关警告？**
A：两者均设计为可选依赖：langgraph 缺失自动降级为内置 FallbackGraph；chromadb 缺失降级为内存向量集合（重启后向量不持久，仅限调试）。生产 Docker 镜像内含完整依赖。

---

## 十六、安全须知

1. **生产部署务必修改** `.env` 中的 `JWT_SECRET` 与 `AUDIT_HASH_SEED`。
2. `.env`、`data/`（含 app.db、审计日志、工作区代码、模型库）均已被 `.gitignore` 忽略，请勿强行提交。
3. 系统默认 **MAINTENANCE_MODE=false**，任何运行期外网请求都会被出口守卫拦截；仅在导入模型/依赖的维护窗口临时开启，用后即关。
4. 生产环境沙箱建议使用 `SANDBOX_MODE=docker`，不要把用户生成代码直接以宿主机进程模式运行。
5. 初始管理员密码仅在首启向导中生成一次（容器场景查看 `data/admin.initial-creds.txt`），首次登录后请立即修改。

---

## 许可证 / 免责声明

本项目为公司内网专用系统初版（v0.1），请在内部环境使用。模型权重、嵌入模型与 npm/pip 依赖的使用须遵守其各自上游许可证。

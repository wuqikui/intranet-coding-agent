# 内网离线多用户类 Trae 的 AI Coding Agent 系统 - 产品需求文档

## Overview
- **Summary**: 一套纯内网、离线、多用户、类 Trae 的 AI Coding Agent 系统。核心能力是"自然语言需求 → 可一键编译运行的全栈代码项目交付"，附带支撑性的多模型推理网关、四通道隔离知识库、强制工程循环（真实编译 + 自动修复）、多用户权限与全量审计。
- **Purpose**: 在断网、数据不可外出的内网环境下，让多角色员工并发使用大模型完成代码项目生成与维护，且生成物可编译、可运行、风格一致、全程可审计。
- **Target Users**:
  - `admin`：维护离线导入、模型/依赖、知识库、用户、审计。
  - `dev`：使用 coding agent 生成/维护项目、调用沙箱工具与构建。
  - `readonly`：仅查看/对话，无 shell/构建/写入权限（原 `office` 角色因办公自动化被移除而并入此角色）。

## Goals
- G1: 纯内网部署，员工机仅浏览器访问，推理跑在多卡内网 GPU 服务器。
- G2: 模型/分词器/依赖/工具二进制全部支持离线导入（sha256 校验），运行时禁止自动联网。
- G3: 前端 TS+React+Vite+Ant Design 5，提供会话窗、仓库/文件树、代码 Diff/编辑、终端回放、任务看板、管理员后台。
- G4: 后端 Python 3.11+ FastAPI，REST + SSE/WebSocket。
- G5: 推理支持 vLLM（AWQ/GPTQ/FP8，OpenAI 兼容）与 llama.cpp llama-server（GGUF Q4/Q5/Q8），配置切换；多卡 TP；统一走"模型网关"做鉴权/限流/路由/重试/日志。
- G6: 显存自适应推荐（单卡→32B Q4 或 14B Q8；多卡→70B Q4 或更高）+ 自动量化脚本（safetensors→GGUF/AWQ）+ KV cache 本地估算 + 按显存限并发。
- G7: 四通道隔离知识库（业务/代码/构建运维/数据接口），按任务自动路由，禁止跨库混检；代码库采用符号索引 + 嵌入双通道。
- G8: Coding agent：完整项目生成（架构→文件树→代码→配置/Docker→测试→一键运行）+ 强制工程循环（真实编译 + 错误解析 + 自动修复 ≤N 轮，N≥5）+ 代码风格保真 + 多语言混合 + 跨语言胶水 + 沙箱工具与危险命令二次确认。
- G9: 多用户权限：网页登录（本地/AD/LDAP），RBAC，网关按角色鉴权，项目/工具/模型访问控制，敏感操作二次审批，防篡改审计日志。
- G10: 交付：Monorepo + docker-compose 一键部署 + 离线镜像构建 + .env 配置 + 首次运行向导 + 管理员文档 + 验收标准。

## Non-Goals
- NG1: 办公自动化（PPT/PDF/Word/Excel 生成与预览）——本轮舍弃。
- NG2: `office` 角色及其办公工具——并入 `readonly`。
- NG3: 运行时公网访问（仅管理员"出网维护模式"临时联网，全程审计）。
- NG4: 运行时自动联网下载模型/依赖。
- NG5: K8s 编排（仅在文档中作为扩展方向提及，不实现）。
- NG6: 在开发笔记本上运行整套系统（部署对象是带先进显卡的大台式机/服务器）。

## Functional Requirements
- **FR-1（部署/网络）**: 纯内网；管理员"出网维护模式"可临时联网拉模型/依赖，全程审计；运行时无任何自动联网；离线导入流程：临时联网下载 → sha256 校验 → 拷入内网模型库/包仓库 → 后端加载。
- **FR-2（前端）**: 会话窗（SSE/WS 流式）、仓库/文件树、代码 Diff/编辑、终端回放、任务看板、管理员后台、登录页；组件 Ant Design 5；状态管理 Zustand；避免重型 CSS 框架过度依赖。
- **FR-3（后端）**: FastAPI REST + SSE/WS；统一异常/日志/配置；模块边界清晰（auth/gateway/knowledge/agent/sandbox/audit）。
- **FR-4（推理网关）**: 统一入口；适配 vLLM、llama.cpp、Mock Provider（无 GPU/断网演示与降级）；鉴权/限流/模型路由/请求重试/日志；显存自适应推荐；KV cache 本地估算；按显存限并发；多卡 TP。
- **FR-5（量化与导入）**: 自动量化脚本（safetensors→GGUF/AWQ）；离线导入器（sha256 校验、模型库/包仓库管理、加载注册）。
- **FR-6（知识库）**: 四通道隔离（business/code/buildops/dataapi）；各自独立向量集合；代码库 = 符号索引（类/函数/头文件/全局变量调用图谱，SQLite）+ 嵌入；手动上传 + 自动分类（规则 + 模型辅助）；检索路由按任务类型选库；硬隔离禁止跨库。
- **FR-7（Coding Agent）**:
  - FR-7.1 完整项目生成：NL→架构设计→文件树→代码→配置/Docker→测试→一键运行（Makefile/run.sh/docker-compose），保证 install→build→run 一步通过，失败自动迭代修复。
  - FR-7.2 强制工程循环：每生成一批代码须调用真实构建工具（cmake/make/g++/clang）编译；解析编译/链接错误自动修复，最多 N 轮（N≥5），全通过才交付；C/C++ 强制现代 CMake（target_include_directories/target_link_libraries 明确 PUBLIC/PRIVATE）、RAII（智能指针/容器替代裸 malloc/new）、禁止 UB；有 clang-tidy/cppcheck 优先启用，否则用知识库"C/C++ 常见缺陷范式"规则扫描。
  - FR-7.3 多语言混合：识别主语言、尊重各自生态（Python poetry/pip、C++ CMake、TS npm/pnpm）；跨语言调用生成明确胶水（extern "C"/FFI/HTTP 微服务）。
  - FR-7.4 代码风格保真：生成前检索知识库"既有代码风格"，生成后做一致性检查（缩进/命名/注释语言/模块化），不符则自动重写，不得擅自用预设风格。
  - FR-7.5 工具与沙箱：文件读写、全局搜索、shell（Docker/rootless 沙箱）、git diff 建议、单测/lint 执行；危险命令（rm、git push、删库）二次确认。
- **FR-8（多用户/权限）**: 网页登录（本地/AD/LDAP）；角色 admin/dev/readonly；网关按角色鉴权；限制用户可访问项目目录/工具/模型（readonly 不可跑 shell/构建）；敏感操作（外发/push/删文件）二次审批；全量审计日志（prompt/模型/工具/参数/耗时/token），防篡改、只读留存。
- **FR-9（交付）**: Monorepo（frontend/backend/inference/knowledge/tools/deploy）；docker-compose 起前端+后端+网关+推理引擎；离线镜像构建说明；模型路径/显存并发数/知识库路径全走 .env；首次运行向导（检测 GPU/显存→推荐模型与量化→导入权重→导入并分类四库→建索引→创建管理员→员工开户）；管理员文档；验收标准。

## Non-Functional Requirements
- **NFR-1（离线）**: 运行时零自动公网出口；embedding/模型加载/依赖安装均走内网库或预置。
- **NFR-2（并发）**: ≥3 人并发 coding 会话不超时。
- **NFR-3（安全）**: 审计日志防篡改（哈希链/只读追加）；RBAC 在网关与工具层双重生效。
- **NFR-4（可维护）**: Monorepo、模块边界清晰、配置全部 .env 驱动。
- **NFR-5（可演示/降级）**: 无 GPU/断网时通过 Mock Provider 仍可演示 Agent 全链路（真实编译、真实生成）。
- **NFR-6（可移植）**: 路径用相对/配置化，便于内网迁移。

## Constraints
- **Technical**: Python 3.11+；TS+React+Vite+AntD5；Chroma + SQLite（向量 + 符号图）；vLLM/llama.cpp；Docker/docker-compose（部署目标）。
- **Business**: 内网合规、数据不可外发、全程可审计。
- **Dependencies**: 模型权重、分词器、pip/npm 包、工具二进制均须离线可导入。

## Assumptions
- 部署目标机器具备多张几十 GB 级显存 GPU 与 Docker。
- 管理员可在外网临时下载并离线导入。
- 内网有 LDAP/AD 可对接（无则退化为本地账号）。
- 四库初始内容由管理员导入。

## Acceptance Criteria

### AC-1: 三人并发 coding 不超时
- **Type**: `rule`
- **Given**: 系统已部署、模型已加载、3 个 dev 账号
- **When**: 3 人同时发起 coding 会话并流式生成
- **Then**: 全部在合理时间内完成，无超时/504
- **Pass Condition**: 3 路并发会话均收到完整流式响应且无超时错误
- **Evidence**: 并发测试脚本输出 + 网关日志

### AC-2: 全栈项目一键跑通（含编译修复循环）
- **Type**: `rule`
- **Given**: dev 账号、知识库已建索引
- **When**: 用自然语言要求生成"含 CMake+C++ 核心模块 + FastAPI 后端 + React 前端"的全栈项目
- **Then**: 生成项目可一键 install→build→run；编译失败时 agent 真实调用 cmake/g++ 编译并自动修复直至通过
- **Pass Condition**: 生成的项目 `make up` / 一键脚本跑通且前后端可访问；agent 日志含真实编译与修复轮次记录
- **Evidence**: 生成项目目录 + 构建日志 + 运行截图/curl 结果

### AC-3: 非管理员不可越权读他人项目
- **Type**: `rule`
- **Given**: devA 项目存在、devB/readonly 账号
- **When**: devB/readonly 尝试访问 devA 项目目录与文件
- **Then**: 返回 403，审计记录该次尝试
- **Pass Condition**: 越权访问被拒绝且审计可查
- **Evidence**: 接口返回码 + 审计日志条目

### AC-4: 断网状态全部功能可用
- **Type**: `rule`
- **Given**: 完全断网（airgap）
- **When**: 执行登录/会话/生成/编译/知识库检索/导出
- **Then**: 全部功能可用，无任何联网尝试
- **Pass Condition**: 断网下验收用例全过；网络出口监控为零
- **Evidence**: airgap 测试报告 + 出网审计为零

### AC-5: 离线导入强制 sha256 校验、运行时无联网
- **Type**: `rule`
- **Given**: 管理员出网维护模式
- **When**: 导入模型/依赖
- **Then**: sha256 校验通过才入库；运行时无自动联网
- **Pass Condition**: 校验失败拒绝入库；运行时出口流量为零
- **Evidence**: 导入日志 + sha256 校验记录 + 出网审计

### AC-6: 四知识库通道隔离、禁止跨库混检
- **Type**: `rule`
- **Given**: 四库已建索引、各自内容
- **When**: 按任务类型路由检索
- **Then**: 仅在对应库内检索，不跨库
- **Pass Condition**: 路由日志显示单库命中；跨库查询被拒绝/不存在路径
- **Evidence**: 检索路由日志 + 隔离断言测试

### AC-7: 强制工程循环（真实编译 + 自动修复 ≤N 轮，N≥5）
- **Type**: `rule`
- **Given**: agent 生成 C/C++ 代码
- **When**: 触发工程循环
- **Then**: 每批调用真实 cmake/g++/make 编译；解析错误自动修复；最多 N≥5 轮；全通过才交付
- **Pass Condition**: 工程循环日志含真实编译调用、错误解析、修复轮次、最终通过
- **Evidence**: agent 工程循环日志 + 构建产物

### AC-8: 代码风格保真
- **Type**: `rubric`
- **Dimension**: 生成代码与知识库既有风格的一致性
- **Scale**: 1-5
- **Anchors**: 1 = 完全使用预设风格无视知识库；3 = 部分一致但命名/缩进有偏差；5 = 缩进/命名/注释语言/模块化高度一致
- **Pass Threshold**: >= 4
- **Evidence**: 风格检查报告 + 知识库风格样本对比

### AC-9: 危险命令二次确认
- **Type**: `rule`
- **Given**: dev 执行 rm/git push/删库类命令
- **When**: 命令进入沙箱执行前
- **Then**: 拦截并要求二次确认；未确认不执行
- **Pass Condition**: 危险命令被拦截且审计记录确认动作
- **Evidence**: 拦截日志 + 二次确认审计

### AC-10: 审计日志防篡改且字段完整
- **Type**: `rule`
- **Given**: 任意用户操作
- **When**: 产生 prompt/模型/工具/参数/耗时/token
- **Then**: 审计条目含全部字段；哈希链校验通过；只读留存
- **Pass Condition**: 字段完整 + 哈希链验证通过 + 文件只读
- **Evidence**: 审计样本 + 哈希链校验脚本输出

### AC-11: 首次运行向导完整流程
- **Type**: `rule`
- **Given**: 全新部署
- **When**: 执行首次运行向导
- **Then**: 检测 GPU/显存→推荐模型与量化→导入权重→导入并分类四库→建索引→创建管理员→员工开户 全部完成
- **Pass Condition**: 向导各步骤完成且系统可用
- **Evidence**: 向导执行日志 + 就绪状态

### AC-12: 管理员文档完备
- **Type**: `rubric`
- **Dimension**: 管理员运维文档覆盖度与可操作性
- **Scale**: 1-5
- **Anchors**: 1 = 缺关键章节；3 = 覆盖主要操作但缺验证步骤；5 = 临时开网/量化/扩容/备份/审计查阅均含可执行步骤与验证
- **Pass Threshold**: >= 4
- **Evidence**: docs/admin/*.md

## Open Questions
- [ ] LDAP/AD 端点与基础 DN 由部署方提供（向导中填入，缺省走本地账号）。
- [ ] 具体模型选型由向导按显存推荐、管理员确认（不绑定型号）。
- [ ] 符号索引对非 C/C++ 语言（Python/TS）的图谱粒度（首版：函数级 import/def）。

# 内网离线多用户类 Trae 的 AI Coding Agent 系统 - 实施计划

> 任务粒度：垂直切片，依赖有序。Status 字段承载状态，标题不含状态标记。

## Task 1: Monorepo 骨架与配置基座
- **Status**: `pending`
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 建立 frontend/backend/inference/knowledge/tools/deploy/docs 与 .trae/specs 目录骨架
  - 顶层 .env.example（模型路径/显存并发数/知识库路径/网关端口/沙箱模式）
  - docker-compose.yml 骨架（frontend/backend/gateway/inference 服务占位）
  - 顶层 Makefile（make up/build/logs/init）
- **Acceptance Criteria Addressed**: AC-4, AC-9(部署基础)
- **Test Requirements**:
  - `rule` TR-1.1: 目录结构与 .env.example 存在；`make help` 列出可用目标；证据：目录树 + make 输出
  - `rule` TR-1.2: docker-compose config 校验通过；证据：`docker compose config` 无错误

## Task 2: 后端 FastAPI 核心与审计基座
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 1
- **Description**:
  - FastAPI app（配置/日志/异常/中间件）、模块边界（auth/gateway/knowledge/agent/sandbox/audit）
  - 防篡改审计：哈希链追加、只读留存、字段（prompt/模型/工具/参数/耗时/token）
  - 健康检查与就绪探针
- **Acceptance Criteria Addressed**: AC-10
- **Test Requirements**:
  - `rule` TR-2.1: 审计条目含全部必填字段；哈希链校验脚本通过；证据：审计样本 + 校验输出
  - `rule` TR-2.2: 审计文件只读（追加不可改写历史）；证据：篡改测试失败

## Task 3: 鉴权与 RBAC
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 2
- **Description**:
  - 本地账号/LDAP/AD 登录适配、JWT 会话
  - 角色 admin/dev/readonly；项目目录/工具/模型访问控制矩阵
  - 敏感操作（外发/push/删文件）二次审批流
  - 越权访问 403 + 审计
- **Acceptance Criteria Addressed**: AC-3, AC-9
- **Test Requirements**:
  - `rule` TR-3.1: devB/readonly 访问 devA 项目返回 403 且审计可查；证据：接口返回码 + 审计
  - `rule` TR-3.2: 危险命令（rm/git push/删库）被拦截并要求二次确认；证据：拦截日志

## Task 4: 模型网关与推理适配
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 2
- **Description**:
  - 统一网关入口：鉴权/限流/模型路由/请求重试/日志
  - 适配器：vLLM（OpenAI 兼容，AWQ/GPTQ/FP8，TP）、llama.cpp llama-server（GGUF Q4/Q5/Q8）、Mock Provider（断网/无 GPU 降级，规则模板生成）
  - 显存自适应推荐 + KV cache 本地估算 + 按显存限并发
- **Acceptance Criteria Addressed**: AC-1, AC-4
- **Test Requirements**:
  - `rule` TR-4.1: 三路并发流式会话均完成无超时；证据：并发脚本 + 网关日志
  - `rule` TR-4.2: Mock Provider 在断网下完成一次生成；证据：断网生成日志
  - `rubric` TR-4.3: 网关可观测性；scale 1-5；anchors 1=无日志/3=基础日志/5=含路由/重试/耗时/token 全链路；threshold>=4；证据：网关日志样本

## Task 5: 离线导入与出网维护模式
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 2, Task 4
- **Description**:
  - 管理员"出网维护模式"开关（临时联网、全程审计）
  - 离线导入器：临时联网下载 → sha256 校验 → 入内网模型库/包仓库 → 后端注册加载
  - 运行时自动联网出口阻断（egress guard）
- **Acceptance Criteria Addressed**: AC-5
- **Test Requirements**:
  - `rule` TR-5.1: sha256 校验失败拒绝入库；证据：导入日志
  - `rule` TR-5.2: 运行时出口流量为零；证据：出网审计 + airgap 验证

## Task 6: 四通道隔离知识库
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 2
- **Description**:
  - 四库（business/code/buildops/dataapi）独立 Chroma 集合 + 独立元数据
  - 代码库：SQLite 符号图谱（类/函数/头文件/全局变量调用关系）+ 嵌入双通道
  - 手动上传 + 自动分类（规则 + 模型辅助）
  - embedding 显式生成（避免隐式联网，提供本地/降级兜底）
  - 检索路由按任务类型选库，硬隔离禁止跨库
- **Acceptance Criteria Addressed**: AC-6
- **Test Requirements**:
  - `rule` TR-6.1: 四库内容隔离，跨库检索无结果/被拒；证据：路由日志 + 隔离断言
  - `rule` TR-6.2: 代码库符号查询能命中既有实现；证据：符号查询返回
  - `rubric` TR-6.3: 自动分类准确率；scale 1-5；anchors 1=乱分/3=大部分正确/5=四库边界清晰；threshold>=4；证据：分类报告

## Task 7: Coding Agent - 完整项目生成管线
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 4, Task 6
- **Description**:
  - LangGraph 编排：NL→意图/规划→架构设计→文件树→代码生成→配置/Docker→测试→一键运行
  - 知识库前置检索（代码风格规范、既有实现、构建脚本、API 契约）
  - State schema 显式纳入跨节点字段（防静默失效）
  - install→build→run 一步通过，失败自动迭代
- **Acceptance Criteria Addressed**: AC-2
- **Test Requirements**:
  - `rule` TR-7.1: 自然语言生成全栈项目一键跑通；证据：项目目录 + make up 结果
  - `rubric` TR-7.2: 生成项目可维护性；scale 1-5；anchors 1=跑不起来/3=能跑但结构乱/5=结构清晰可扩展；threshold>=4；证据：项目结构

## Task 8: 强制工程循环（真实编译 + 自动修复）
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 7
- **Description**:
  - 工程循环节点：每批调用真实 cmake/make/g++/clang 编译
  - 编译/链接错误解析器 → 自动修复 → 重编译，最多 N≥5 轮
  - C/C++ 规则：现代 CMake（PUBLIC/PRIVATE）、RAII、禁 UB
  - clang-tidy/cppcheck 优先；无则用知识库"C/C++ 缺陷范式"规则扫描
  - 多语言混合 + 跨语言胶水（extern "C"/FFI/HTTP 微服务）
- **Acceptance Criteria Addressed**: AC-2, AC-7
- **Test Requirements**:
  - `rule` TR-8.1: 工程循环日志含真实编译调用 + 修复轮次 + 最终通过；证据：循环日志 + 产物
  - `rule` TR-8.2: 修复轮次上限 N≥5 可配置；证据：配置项 + 日志
  - `rubric` TR-8.3: C/C++ 现代实践符合度；scale 1-5；anchors 1=裸指针/裸new/3=部分智能指针/5=全面RAII+现代CMake；threshold>=4；证据：生成 CMake/代码

## Task 9: 代码风格保真
- **Status**: `pending`
- **Priority**: medium
- **Depends On**: Task 6, Task 7
- **Description**:
  - 生成前检索知识库"既有代码风格"（缩进/命名/注释语言/模块化）
  - 生成后一致性检查，不符则自动重写
  - 禁止擅自套用预设风格
- **Acceptance Criteria Addressed**: AC-8
- **Test Requirements**:
  - `rubric` TR-9.1: 风格一致性；scale 1-5；anchors 1=无视知识库/3=部分偏差/5=高度一致；threshold>=4；证据：风格检查报告

## Task 10: 沙箱与工具集
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 3, Task 7
- **Description**:
  - 文件读写、全局搜索、shell（Docker/rootless 沙箱）、git diff 建议、单测/lint 执行
  - 危险命令拦截 + 二次确认（与 Task 3 审批流对接）
  - readonly 角色禁用 shell/构建工具
- **Acceptance Criteria Addressed**: AC-9
- **Test Requirements**:
  - `rule` TR-10.1: 危险命令（rm -rf / git push / 删库）被拦截并要求二次确认；证据：拦截日志
  - `rule` TR-10.2: readonly 调用 shell 返回 403；证据：接口返回 + 审计

## Task 11: 前端（TS+React+Vite+AntD5）
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 3, Task 4, Task 7
- **Description**:
  - 登录页、会话窗（SSE/WS 流式）、仓库/文件树、代码 Diff/编辑（Monaco）、终端回放、任务看板、管理员后台
  - Ant Design 5 + Zustand；避免重型 CSS 框架过度依赖
  - 按 RBAC 控制菜单与操作可见性
- **Acceptance Criteria Addressed**: AC-3, AC-1
- **Test Requirements**:
  - `rule` TR-11.1: devB 看不到 devA 项目菜单/路由；证据：前端 RBAC 截图/测试
  - `rule` TR-11.2: 流式会话可展示生成 + 终端回放；证据：会话回放
  - `rubric` TR-11.3: UI 一致性与可用性；scale 1-5；anchors 1=布局错乱/3=可用但粗糙/5=一致流畅；threshold>=4；证据：界面走查

## Task 12: 首次运行向导
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 4, Task 5, Task 6, Task 3
- **Description**:
  - 检测 GPU/显存 → 推荐模型与量化 → 导入权重 → 导入并分类四库 → 建索引 → 创建管理员 → 员工开户
  - 失败可重试，进度可观测
- **Acceptance Criteria Addressed**: AC-11
- **Test Requirements**:
  - `rule` TR-12.1: 向导各步骤完成且系统就绪；证据：向导日志 + 就绪状态
  - `rubric` TR-12.2: 向导可操作性；scale 1-5；anchors 1=卡死无指引/3=能跑但报错不清/5=清晰可重试；threshold>=4；证据：向导交互

## Task 13: 一键部署与离线镜像
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 1, Task 4, Task 11
- **Description**:
  - docker-compose 起 frontend+backend+gateway+inference
  - 离线镜像构建说明与脚本
  - .env 配置模型路径/显存并发数/知识库路径
- **Acceptance Criteria Addressed**: AC-4
- **Test Requirements**:
  - `rule` TR-13.1: `make up` 拉起全套服务且健康检查通过；证据：健康检查输出
  - `rule` TR-13.2: 离线镜像构建文档可执行；证据：构建脚本/说明

## Task 14: 管理员文档
- **Status**: `pending`
- **Priority**: medium
- **Depends On**: Task 5, Task 6, Task 12, Task 2
- **Description**:
  - docs/admin：临时开网下模型、量化、扩容并发、备份知识库、查看审计
- **Acceptance Criteria Addressed**: AC-12
- **Test Requirements**:
  - `rubric` TR-14.1: 文档完备度；scale 1-5；anchors 1=缺章/3=主要操作无验证/5=全含可执行步骤与验证；threshold>=4；证据：docs/admin/*.md

## Task 15: 验收测试集
- **Status**: `pending`
- **Priority**: medium
- **Depends On**: Task 1-14
- **Description**:
  - 并发测试（AC-1）、全栈生成测试（AC-2）、RBAC 测试（AC-3）、airgap 测试（AC-4）、隔离测试（AC-6）、审计校验（AC-10）
- **Acceptance Criteria Addressed**: AC-1, AC-2, AC-3, AC-4, AC-6, AC-10
- **Test Requirements**:
  - `rule` TR-15.1: 验收脚本全过；证据：测试报告
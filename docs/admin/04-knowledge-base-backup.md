# 备份知识库

## 概述

知识库是系统的核心资产：四通道隔离的业务文档、代码、构建脚本、数据契约 + Chroma 向量索引 + 符号图谱。
管理员需制定周期备份策略，确保灾难恢复时可重建。

四通道物理隔离，分别位于独立目录，备份与恢复必须保留通道边界，不得混库。

## 前置条件

- 已有 admin 权限。
- 已知部署机数据目录布局（见 `app/config.py` 默认值）。
- 已规划备份存储位置（建议异机/异地存储，遵循 3-2-1 备份原则）。

## 数据位置参考

| 资源 | 路径（默认） | 说明 |
|------|--------------|------|
| 业务库 | `./data/kb/business/` | 业务文档原文 |
| 代码库 | `./data/kb/code/` | 既有代码 + 编码规范 |
| 构建库 | `./data/kb/buildops/` | CMake/Dockerfile/Makefile 等 |
| 数据 API 库 | `./data/kb/dataapi/` | Schema / API 契约 |
| 向量索引 | `./data/chroma/` | Chroma 持久化（四通道集合） |
| 符号图谱 | `./data/symbols.db` | SQLite 符号索引 |
| 元数据库 | `./data/app.db` | 用户/项目/会话 |
| 审计日志 | `./data/audit/audit.jsonl` | 哈希链审计（不可篡改） |
| 工作区 | `./data/workspace/` | 生成项目落盘 |

## 操作步骤

### 1. 临时冻结写入（可选但推荐）

通过维护模式或前端公告暂停写入：

```bash
# 开启维护模式（仅 admin）
curl -s -X POST $BASE_URL/api/admin/maintenance \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"enable": true}'
```

> 维护模式不影响 KB 读取，仅阻断出网；此处用于告知用户"系统维护中"。

### 2. 备份四通道原文

```bash
# 部署机执行
TS=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR=/backup/kb_$TS
mkdir -p $BACKUP_DIR

# 四通道分别打包（保持隔离）
for ch in business code buildops dataapi; do
  tar czf $BACKUP_DIR/kb_${ch}.tgz -C ./data/kb $ch
  sha256sum $BACKUP_DIR/kb_${ch}.tgz > $BACKUP_DIR/kb_${ch}.tgz.sha256
done

# 验证完整性
cd $BACKUP_DIR
sha256sum -c *.sha256
```

### 3. 备份向量索引与符号图谱

```bash
# Chroma 持久化目录
tar czf $BACKUP_DIR/chroma.tgz -C ./data chroma
sha256sum $BACKUP_DIR/chroma.tgz > $BACKUP_DIR/chroma.tgz.sha256

# 符号图谱 SQLite（先用 .backup 命令保证一致性）
sqlite3 ./data/symbols.db ".backup '$BACKUP_DIR/symbols.db'"
sha256sum $BACKUP_DIR/symbols.db > $BACKUP_DIR/symbols.db.sha256
```

### 4. 备份元数据库与审计

```bash
# 元数据 SQLite
sqlite3 ./data/app.db ".backup '$BACKUP_DIR/app.db'"
sha256sum $BACKUP_DIR/app.db > $BACKUP_DIR/app.db.sha256

# 审计日志（不可篡改，整目录打包）
tar czf $BACKUP_DIR/audit.tgz -C ./data audit
sha256sum $BACKUP_DIR/audit.tgz > $BACKUP_DIR/audit.tgz.sha256
```

### 5. 异地存储

```bash
# 复制到异地备份机
rsync -avz --checksum $BACKUP_DIR/ backup-host:/backup/local-agent/$TS/

# 或上传到对象存储（需先开维护模式）
rsync -avz $BACKUP_DIR/ user@object-storage:/bucket/local-agent/$TS/
```

### 6. 恢复写入

```bash
curl -s -X POST $BASE_URL/api/admin/maintenance \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"enable": false}'
```

## 恢复流程

### 从备份恢复

```bash
# 1. 停止后端进程
sudo systemctl stop local-agent-backend

# 2. 移走现有数据（不直接覆盖）
mv ./data ./data.corrupt.$(date +%s)

# 3. 解压备份
mkdir -p ./data/kb
for ch in business code buildops dataapi; do
  tar xzf /backup/kb_YYYYMMDD_HHMMSS/kb_${ch}.tgz -C ./data/kb
done
tar xzf /backup/kb_YYYYMMDD_HHMMSS/chroma.tgz -C ./data
cp /backup/kb_YYYYMMDD_HHMMSS/symbols.db ./data/
cp /backup/kb_YYYYMMDD_HHMMSS/app.db ./data/
tar xzf /backup/kb_YYYYMMDD_HHMMSS/audit.tgz -C ./data

# 4. 校验完整性
cd /backup/kb_YYYYMMDD_HHMMSS
sha256sum -c *.sha256

# 5. 启动后端
sudo systemctl start local-agent-backend
```

### 验证恢复

1. 查询知识库统计：

   ```bash
   curl -s $BASE_URL/api/knowledge/stats -H "Authorization: Bearer $TOKEN"
   ```

   `channels` 各通道文档数应与备份时一致。

2. 检索测试（四通道各一次）：

   ```bash
   for ch in business code buildops dataapi; do
     curl -s -X POST $BASE_URL/api/knowledge/search \
       -H "Authorization: Bearer $TOKEN" \
       -H 'Content-Type: application/json' \
       -d "{\"channel\":\"$ch\",\"query\":\"test\",\"top_k\":1}"
   done
   ```

3. 审计日志哈希链完整性校验（见 `06-audit-verification.md`）。

## 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 恢复后 KB stats 全为 0 | Chroma 路径错或解压不完整 | 检查 `./data/chroma/` 子目录；重新解压 |
| 跨库查询异常 | 四通道文件被混在一起 | 严格按 `business/code/buildops/dataapi` 子目录解压；不得合并 |
| symbols.db 损坏 | 备份时未用 `.backup` 命令 | 重新跑 `sqlite3 symbols.db ".backup"`；或重建符号索引 |
| audit.jsonl 链断 | 备份被部分覆盖 | 整目录恢复；不可只恢复单文件 |
| app.db 用户丢失 | 恢复了旧备份 | 用最新备份；或手动创建 admin（见 `wizard` 步骤 6） |

## 备份策略建议

- **频率**：KB 入库高峰每日全备；平时每周全备 + 增量。
- **保留**：最近 4 周全备 + 12 个月归档。
- **演练**：每季度执行一次恢复演练，验证可重建。
- **隔离**：备份介质与生产网络物理隔离，防勒索/误删。

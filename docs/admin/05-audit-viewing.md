# 查看审计

## 概述

系统所有敏感操作（鉴权、生成、工具调用、维护模式开关、模型导入、用户管理）都写入防篡改审计日志。
日志采用哈希链追加模式：每条记录的 `hash_curr` = `sha256(hash_prev + sha256(canonical_content))`，
任何对历史记录的修改都会破坏链结构。

管理员通过 REST API 查询审计；离线校验通过 Python 脚本验证哈希链完整性。

## 前置条件

- 已有 admin 权限（readonly 角色无访问权）。
- 审计日志文件路径：`AUDIT_LOG_PATH`（默认 `./data/audit/audit.jsonl`）。

## 操作步骤

### 1. 通过 API 查询审计

#### 1.1 查询最近 N 条

```bash
# 默认 100 条
curl -s "$BASE_URL/api/admin/audit" \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool

# 指定 500 条（最大 10000）
curl -s "$BASE_URL/api/admin/audit?limit=500" \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

响应示例：

```json
{
  "records": [
    {
      "id": "uuid-...",
      "timestamp": "2026-09-20T08:30:00+00:00",
      "user_id": "1",
      "role": "admin",
      "action": "maintenance_enable",
      "prompt": "",
      "model": "",
      "tool": "",
      "params": {},
      "duration_ms": 0,
      "tokens_in": 0,
      "tokens_out": 0,
      "extra": {"flag_file": "./data/maintenance.flag"},
      "hash_prev": "abc...",
      "hash_curr": "def..."
    }
  ],
  "count": 100,
  "total": 1234
}
```

#### 1.2 按 action 类型过滤（grep）

```bash
# 只看维护模式开关
curl -s "$BASE_URL/api/admin/audit?limit=10000" \
  -H "Authorization: Bearer $TOKEN" \
  | python -c "
import sys, json
data = json.load(sys.stdin)
for r in data['records']:
    if 'maintenance' in r['action']:
        print(f\"{r['timestamp']} {r['user_id']} {r['action']}\")
"

# 只看模型导入
curl -s "$BASE_URL/api/admin/audit?limit=10000" \
  -H "Authorization: Bearer $TOKEN" \
  | python -c "
import sys, json
data = json.load(sys.stdin)
for r in data['records']:
    if r['action'] == 'model_import':
        e = r.get('extra', {})
        print(f\"{r['timestamp']} {r['user_id']} -> {e.get('dest_path')} sha={e.get('sha256','')[:16]}… size={e.get('size_bytes')}\")
"
```

### 2. 直读 JSONL 文件（离线分析）

```bash
# 文件路径
ls -lh ./data/audit/audit.jsonl

# 行数 = 总记录数
wc -l ./data/audit/audit.jsonl

# 查看最后 5 条
tail -n 5 ./data/audit/audit.jsonl | python -m json.tool
```

### 3. 哈希链完整性校验（关键）

```python
# 文件: backend/scripts/verify_audit_chain.py
import json
import hashlib
import sys
from pathlib import Path

def verify(log_path: str, hash_seed: str = "genesis-block-seed") -> tuple[bool, int]:
    """校验审计日志哈希链完整性。
    
    Returns:
        (ok, checked_count)
    """
    p = Path(log_path)
    if not p.exists():
        print(f"[ERROR] 审计文件不存在: {log_path}", file=sys.stderr)
        return False, 0

    prev = hash_seed
    checked = 0
    with open(p, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[FAIL] 行 {line_no} JSON 解析失败: {e}", file=sys.stderr)
                return False, checked

            # 校验 hash_prev
            if entry.get("hash_prev") != prev:
                print(
                    f"[FAIL] 行 {line_no} hash_prev 不匹配："
                    f"期望 {prev[:16]}… 实际 {entry.get('hash_prev','')[:16]}…",
                    file=sys.stderr,
                )
                return False, checked

            # 重算 hash_curr
            content = {k: v for k, v in entry.items() if k not in ("hash_prev", "hash_curr")}
            content_str = json.dumps(content, sort_keys=True, ensure_ascii=False)
            content_hash = hashlib.sha256(content_str.encode("utf-8")).hexdigest()
            combined = prev + content_hash
            computed = hashlib.sha256(combined.encode("utf-8")).hexdigest()
            if computed != entry.get("hash_curr"):
                print(
                    f"[FAIL] 行 {line_no} hash_curr 不匹配："
                    f"期望 {entry.get('hash_curr','')[:16]}… 实际 {computed[:16]}…",
                    file=sys.stderr,
                )
                return False, checked

            prev = entry["hash_curr"]
            checked += 1

    print(f"[OK] 哈希链完整：{checked} 条记录全部通过校验")
    return True, checked

if __name__ == "__main__":
    log_path = sys.argv[1] if len(sys.argv) > 1 else "./data/audit/audit.jsonl"
    hash_seed = sys.argv[2] if len(sys.argv) > 2 else "genesis-block-seed"
    ok, n = verify(log_path, hash_seed)
    sys.exit(0 if ok else 1)
```

运行：

```bash
python backend/scripts/verify_audit_chain.py ./data/audit/audit.jsonl
# [OK] 哈希链完整：1234 条记录全部通过校验
```

## 验证

1. API 响应正常：

   ```bash
   curl -s -o /dev/null -w "%{http_code}" \
     "$BASE_URL/api/admin/audit?limit=10" \
     -H "Authorization: Bearer $TOKEN"
   # 200
   ```

2. 非管理员访问被拒（RBAC 验证 AC-3）：

   ```bash
   # readonly 用户
   curl -s -o /dev/null -w "%{http_code}" \
     "$BASE_URL/api/admin/audit?limit=10" \
     -H "Authorization: Bearer $READONLY_TOKEN"
   # 403
   ```

3. 哈希链校验通过：`verify_audit_chain.py` 退出码 0。

4. 篡改检测：手动编辑 `audit.jsonl` 中任一非末行记录的 `params` 字段，重新运行 `verify_audit_chain.py`，应输出 `[FAIL]` 并指出错误行号。

## 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 403 用户角色无权限 | readonly/dev 无权访问 | 用 admin 账号重新登录获取 token |
| 哈希链校验失败行 N | 行 N-1 或 N 被篡改/插入 | 从备份恢复 audit.jsonl；启动内部调查 |
| records 为空 | 首次部署未产生审计 | 触发一次操作（如登录失败）后再查询 |
| hash_prev 不匹配 | 中间有记录被删除 | 从备份恢复；不可"补链"（会破坏证据链） |
| 文件锁占用 | 后端正在追加写 | 只读模式打开；或停服后再校验 |

## 关键字段说明

| 字段 | 说明 |
|------|------|
| `id` | UUID，唯一标识 |
| `timestamp` | UTC ISO8601 |
| `user_id` | 操作者 ID（system 表示系统级） |
| `role` | 操作时角色 |
| `action` | 动作类型：login_success / login_failure / chat / generate / maintenance_enable / model_import / user_create / shell_exec / file_write 等 |
| `prompt` | 用户输入（生成任务时记录） |
| `model` | 使用的模型名 |
| `tool` | 工具调用名（shell/file_read 等） |
| `params` | 调用参数（脱敏后） |
| `duration_ms` | 调用耗时 |
| `tokens_in` / `tokens_out` | 输入/输出 token 数 |
| `extra` | 扩展字段（因动作而异） |
| `hash_prev` / `hash_curr` | 哈希链前后向指针 |

## 合规要求

- 审计日志**只追加、不修改、不删除**。
- 篡改审计日志等同安全事件，触发应急响应流程。
- 备份审计日志时整目录打包，不得单独提取部分文件。
- 审计保留期建议 ≥ 1 年，按行业合规要求延长。

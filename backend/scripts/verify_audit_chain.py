"""审计日志哈希链完整性校验。

用法：
    python backend/scripts/verify_audit_chain.py [log_path] [hash_seed]

退出码：0 = 通过；1 = 失败/损坏。

校验逻辑：
    hash_curr = sha256(hash_prev + sha256(canonical_json(content_without_hashes)))
    任何对历史记录的修改、删除、插入都会破坏链结构。
"""
import hashlib
import json
import sys
from pathlib import Path

DEFAULT_LOG_PATH = "./data/audit/audit.jsonl"
DEFAULT_HASH_SEED = "genesis-block-seed"


def verify(log_path: str, hash_seed: str = DEFAULT_HASH_SEED) -> tuple:
    """校验审计日志哈希链完整性。

    Returns:
        (ok: bool, checked: int)
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
    log_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOG_PATH
    hash_seed = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_HASH_SEED
    ok, n = verify(log_path, hash_seed)
    sys.exit(0 if ok else 1)

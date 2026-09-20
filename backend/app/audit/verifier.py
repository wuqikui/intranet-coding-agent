"""哈希链校验器 - 独立验证审计日志完整性。"""
import json
import hashlib
from pathlib import Path
from typing import Callable


def _compute_hash(entry: dict, hash_prev: str) -> str:
    """与 AuditLogger._compute_hash 相同的哈希算法。"""
    content = {k: v for k, v in entry.items() if k not in ("hash_prev", "hash_curr")}
    content_str = json.dumps(content, sort_keys=True, ensure_ascii=False)
    content_hash = hashlib.sha256(content_str.encode("utf-8")).hexdigest()
    combined = hash_prev + content_hash
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


class AuditVerifier:
    """校验审计日志哈希链是否完整、无篡改。"""

    def __init__(self, log_path: str, hash_seed: str = "genesis-block-seed"):
        self._log_file = Path(log_path) / "audit.jsonl"
        self._hash_seed = hash_seed

    def verify(self) -> tuple[bool, list[str]]:
        """校验整条哈希链。

        Returns:
            (is_valid, errors): 链完整且无篡改返回 (True, [])；
            否则返回 (False, [错误描述...])。
        """
        if not self._log_file.exists():
            return True, []

        errors: list[str] = []
        prev_hash = self._hash_seed
        line_num = 0

        with open(self._log_file, "r", encoding="utf-8") as f:
            for line in f:
                line_num += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    errors.append(f"Line {line_num}: JSON 解析失败")
                    continue

                # 检查 hash_prev 链接
                stored_prev = entry.get("hash_prev", "")
                if stored_prev != prev_hash:
                    errors.append(
                        f"Line {line_num}: hash_prev 不匹配 "
                        f"(期望 {prev_hash[:12]}…, 实际 {stored_prev[:12]}…)"
                    )

                # 重算 hash_curr 验证
                computed = _compute_hash(entry, prev_hash)
                stored_curr = entry.get("hash_curr", "")
                if computed != stored_curr:
                    errors.append(
                        f"Line {line_num}: hash_curr 校验失败 "
                        f"(重算 {computed[:12]}…, 存储 {stored_curr[:12]}…)"
                    )

                prev_hash = stored_curr

        return len(errors) == 0, errors

    def count_entries(self) -> int:
        """统计审计条目数。"""
        if not self._log_file.exists():
            return 0
        count = 0
        with open(self._log_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count

"""防篡改审计日志 - 哈希链追加模式。

每条记录包含 hash_prev（上一条的 hash_curr）和 hash_curr，
hash_curr = sha256(hash_prev + sha256(规范化内容))。
文件以 JSONL 追加写入，不可改写历史。
"""
import json
import hashlib
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class AuditLogger:
    """线程安全的哈希链审计日志器。"""

    def __init__(self, log_path: str, hash_seed: str = "genesis-block-seed"):
        self._log_dir = Path(log_path)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = self._log_dir / "audit.jsonl"
        self._hash_seed = hash_seed
        self._lock = threading.Lock()
        self._last_hash = self._load_last_hash()

    # ---- 内部方法 ----

    def _load_last_hash(self) -> str:
        """读取文件末条的 hash_curr；空文件返回 seed。"""
        if not self._log_file.exists():
            return self._hash_seed
        last = self._hash_seed
        with open(self._log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    last = entry.get("hash_curr", last)
                except json.JSONDecodeError:
                    continue
        return last

    @staticmethod
    def _compute_hash(entry: dict, hash_prev: str) -> str:
        """hash_curr = sha256(hash_prev + sha256(canonical_json(content_without_hashes)))。"""
        content = {k: v for k, v in entry.items() if k not in ("hash_prev", "hash_curr")}
        content_str = json.dumps(content, sort_keys=True, ensure_ascii=False)
        content_hash = hashlib.sha256(content_str.encode("utf-8")).hexdigest()
        combined = hash_prev + content_hash
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    # ---- 公开 API ----

    def log(
        self,
        *,
        user_id: str,
        role: str,
        action: str,
        prompt: str = "",
        model: str = "",
        tool: str = "",
        params: Optional[dict] = None,
        duration_ms: int = 0,
        tokens_in: int = 0,
        tokens_out: int = 0,
        extra: Optional[dict] = None,
    ) -> dict:
        """写入一条审计记录，返回完整条目。"""
        entry: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_id": user_id,
            "role": role,
            "action": action,
            "prompt": prompt,
            "model": model,
            "tool": tool,
            "params": params or {},
            "duration_ms": duration_ms,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        }
        if extra:
            entry["extra"] = extra

        with self._lock:
            entry["hash_prev"] = self._last_hash
            entry["hash_curr"] = self._compute_hash(entry, self._last_hash)
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._last_hash = entry["hash_curr"]

        return entry

    def read_all(self) -> list[dict]:
        """读取全部审计记录（只读查询）。"""
        if not self._log_file.exists():
            return []
        records = []
        with open(self._log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records

    def get_last_hash(self) -> str:
        """当前链尾哈希。"""
        return self._last_hash

"""审计日志哈希链测试。"""
import json
import tempfile
from pathlib import Path

from app.audit.logger import AuditLogger
from app.audit.verifier import AuditVerifier


def test_hash_chain_basic():
    """写入多条记录后链完整。"""
    with tempfile.TemporaryDirectory() as tmp:
        logger = AuditLogger(tmp, "seed-123")
        e1 = logger.log(user_id="u1", role="dev", action="login")
        e2 = logger.log(user_id="u1", role="dev", action="llm_call", prompt="hello", model="mock")
        e3 = logger.log(user_id="u2", role="admin", action="import_model", duration_ms=500)

        # 链链接正确
        assert e1["hash_prev"] == "seed-123"
        assert e2["hash_prev"] == e1["hash_curr"]
        assert e3["hash_prev"] == e2["hash_curr"]

        # 校验通过
        verifier = AuditVerifier(tmp, "seed-123")
        is_valid, errors = verifier.verify()
        assert is_valid, f"链校验失败: {errors}"
        assert verifier.count_entries() == 3


def test_hash_chain_tamper_detection():
    """篡改任一条目内容后被校验器捕获。"""
    with tempfile.TemporaryDirectory() as tmp:
        logger = AuditLogger(tmp, "seed-456")
        logger.log(user_id="u1", role="dev", action="login")
        logger.log(user_id="u1", role="dev", action="llm_call", prompt="hello")
        logger.log(user_id="u2", role="admin", action="import")

        # 篡改第二行内容
        log_file = Path(tmp) / "audit.jsonl"
        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        entry = json.loads(lines[1])
        entry["prompt"] = "TAMPERED"
        lines[1] = json.dumps(entry, ensure_ascii=False)
        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        verifier = AuditVerifier(tmp, "seed-456")
        is_valid, errors = verifier.verify()
        assert not is_valid, "篡改未被检测到"
        assert any("hash_curr" in e for e in errors)


def test_required_fields():
    """审计条目含全部必填字段。"""
    with tempfile.TemporaryDirectory() as tmp:
        logger = AuditLogger(tmp, "seed-789")
        entry = logger.log(
            user_id="u1",
            role="dev",
            action="llm_call",
            prompt="写一个 hello world",
            model="qwen2.5-coder-32b",
            tool="generate_code",
            params={"max_tokens": 2048},
            duration_ms=1500,
            tokens_in=50,
            tokens_out=200,
        )
        required = {
            "id", "timestamp", "user_id", "role", "action",
            "prompt", "model", "tool", "params",
            "duration_ms", "tokens_in", "tokens_out",
            "hash_prev", "hash_curr",
        }
        assert required.issubset(entry.keys()), f"缺失字段: {required - set(entry.keys())}"


def test_append_only():
    """追加写入不改变已有行。"""
    with tempfile.TemporaryDirectory() as tmp:
        logger = AuditLogger(tmp, "seed-append")
        e1 = logger.log(user_id="u1", role="dev", action="a")
        raw_before = (Path(tmp) / "audit.jsonl").read_text(encoding="utf-8")
        e2 = logger.log(user_id="u2", role="dev", action="b")
        raw_after = (Path(tmp) / "audit.jsonl").read_text(encoding="utf-8")
        # 第一行不变
        assert raw_after.startswith(raw_before.rstrip())

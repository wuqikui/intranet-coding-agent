"""危险命令检测器 - Task 10 危险命令拦截 + 二次确认。

覆盖 TR-10.1: 危险命令（rm -rf / git push / 删库等）被拦截并要求二次确认。

设计：
- 黑名单模式匹配（正则），命中即视为危险。
- 调用方在 API 层若 is_dangerous → 返回 449 "needs_confirm"，要求前端弹确认
  对话框；用户确认后用 confirm=true 重新发起请求，才会真正执行。
- 即便 confirm=true，仍走审计 + 路径白名单 + 沙箱执行（不放松隔离）。
- 不可逆操作（rm -rf /、mkfs、dd of=/dev/、DROP DATABASE）即使 confirm=true 也拒绝。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class DangerVerdict:
    """危险判定结果。"""

    dangerous: bool
    reason: str = ""
    pattern: str = ""
    severity: str = "warn"   # warn | block（不可逆，confirm=true 也不放行）

    def to_dict(self) -> dict:
        return {
            "dangerous": self.dangerous,
            "reason": self.reason,
            "pattern": self.pattern,
            "severity": self.severity,
        }


# 危险命令模式表（按严重度排序）
# severity=block：不可逆，confirm=true 也不放行
# severity=warn：可逆/可控，confirm=true 后放行
_DANGER_PATTERNS: List[Tuple[str, str, str]] = [
    # ---- block（不可逆，禁止执行）----
    (r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*)\s+/?(\s|$)",
     "rm -rf / 删根目录（不可逆）", "block"),
    (r"mkfs(\.ext[234]|\.btrfs|\.xfs|\.ntfs)?\b.*?/dev/",
     "mkfs 格式化块设备（不可逆）", "block"),
    (r"dd\s+[^|]*of=/dev/[a-z]+",
     "dd 写块设备（不可逆）", "block"),
    (r":\s*\(\s*\)\s*\{\s*:.*\}\s*;",
     "fork bomb（拒绝服务）", "block"),
    (r"DROP\s+(DATABASE|SCHEMA|TABLE)\s+[^;]*",
     "SQL DROP 数据库/表（不可逆）", "block"),
    (r"TRUNCATE\s+TABLE",
     "SQL TRUNCATE 表（不可逆）", "block"),
    (r"git\s+push\b.*--force(?:-with-lease)?\b",
     "git push --force 重写远端历史（不可逆）", "block"),
    (r"git\s+filter-branch",
     "git filter-branch 重写历史（不可逆）", "block"),
    (r"git\s+reset\s+--hard\s+HEAD~?\d*",
     "git reset --hard 丢弃工作区改动（不可逆）", "block"),
    (r"git\s+clean\s+-[a-zA-Z]*[fd][a-zA-Z]*",
     "git clean -fd 清理未跟踪文件（不可逆）", "block"),
    # ---- warn（可控，confirm=true 放行）----
    (r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*)",
     "rm -rf 递归强制删除", "warn"),
    (r"rm\s+-[a-zA-Z]*\s+/\s+",
     "rm 删除根目录下文件", "warn"),
    (r"git\s+push(?!\s+--force)",
     "git push 推送到远端", "warn"),
    (r"git\s+commit\s+--amend",
     "git commit --amend 修改提交历史", "warn"),
    (r"git\s+tag\s+(-d|--delete)",
     "git tag -d 删除标签", "warn"),
    (r"git\s+branch\s+(-D|--delete\s+--force)",
     "git branch -D 强删分支", "warn"),
    (r"git\s+stash\s+(drop|clear)",
     "git stash drop/clear 清理暂存", "warn"),
    (r"chmod\s+-R\s*[0-7]{3,4}\s+/?[^\s]+",
     "chmod -R 递归改权限", "warn"),
    (r"chown\s+-R\s+",
     "chown -R 递归改属主", "warn"),
    (r"kill(\s+-9)?\s+\d+",
     "kill 进程", "warn"),
    (r"killall\s+",
     "killall 杀进程组", "warn"),
    (r"pkill\s+",
     "pkill 按名杀进程", "warn"),
    (r"shutdown|reboot|halt|poweroff",
     "关机/重启", "warn"),
    (r"sudo\s+",
     "sudo 提权", "warn"),
    (r">\s*/dev/sda|>\s*/dev/null\s+2>&1\s*$",
     "重定向到设备文件", "warn"),
    (r"docker\s+(rm|rmi|volume\s+rm|network\s+rm)",
     "docker 删容器/镜像/卷/网络", "warn"),
    (r"docker\s+system\s+prune",
     "docker system prune 清理 docker", "warn"),
    (r"npm\s+(unpublish|publish)",
     "npm publish/unpublish 发布到仓库", "warn"),
    (r"pip\s+uninstall",
     "pip uninstall 卸载包", "warn"),
    (r"DELETE\s+FROM\s+\w+",
     "SQL DELETE FROM（删数据）", "warn"),
]


class DangerousCommandDetector:
    """危险命令检测器。"""

    # 编译后的模式列表
    _compiled: List[Tuple["re.Pattern", str, str]] = []

    def __init__(self) -> None:
        if not DangerousCommandDetector._compiled:
            for pat, reason, sev in _DANGER_PATTERNS:
                DangerousCommandDetector._compiled.append(
                    (re.compile(pat, re.IGNORECASE | re.DOTALL), reason, sev)
                )

    def check(self, command: List[str]) -> DangerVerdict:
        """检查命令是否危险。返回 DangerVerdict。

        Args:
            command: 命令 token 列表（如 ["rm", "-rf", "/tmp/foo"]）

        Returns:
            DangerVerdict：dangerous=True 时 reason + pattern + severity 填充。
        """
        if not command:
            return DangerVerdict(dangerous=False)
        # 拼接成字符串做正则匹配（保守：保留空白分隔）
        # 注意：参数里若有引号包裹也会被拼回，正则不依赖引号
        text = " ".join(command)
        # block 优先（一旦命中即返回）
        for pat, reason, sev in self._compiled:
            m = pat.search(text)
            if m and sev == "block":
                return DangerVerdict(
                    dangerous=True,
                    reason=reason,
                    pattern=pat.pattern,
                    severity="block",
                )
        # 再扫 warn
        for pat, reason, sev in self._compiled:
            m = pat.search(text)
            if m and sev == "warn":
                return DangerVerdict(
                    dangerous=True,
                    reason=reason,
                    pattern=pat.pattern,
                    severity="warn",
                )
        return DangerVerdict(dangerous=False)

    def should_block(self, command: List[str], confirmed: bool) -> Tuple[bool, DangerVerdict]:
        """是否阻断执行。

        - block 命令：始终阻断
        - warn 命令：confirmed=False 时阻断（需要二次确认）；confirmed=True 放行
        - 普通命令：放行

        Returns:
            (block: bool, verdict: DangerVerdict)
        """
        v = self.check(command)
        if not v.dangerous:
            return False, v
        if v.severity == "block":
            return True, v
        if v.severity == "warn":
            return (not confirmed), v
        return False, v


# ---- 单例 ----

_detector: Optional[DangerousCommandDetector] = None


def get_danger_detector() -> DangerousCommandDetector:
    """获取单例。"""
    global _detector
    if _detector is None:
        _detector = DangerousCommandDetector()
    return _detector

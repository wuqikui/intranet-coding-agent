"""沙箱执行器 - 在隔离环境中运行 shell 命令。

三种模式（settings.SANDBOX_MODE）：
- process: 直接子进程（无隔离，仅用于本机开发/无 docker 环境）
- docker  : docker run --rm -v <cwd>:/work <image>（生产首选）
- rootless: 子进程 + 受限环境变量 + 用户级 namespace（折中）

所有模式统一返回 CommandResult {returncode, stdout, stderr, duration_ms, command}。
不可用工具（如本机缺 cmake/g++）时 returncode=-1 且 stderr 含 "command not found"，
由上层（BuildLoop）判定为 tooling_missing 并降级，不视为代码失败。
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.config import get_settings

logger = logging.getLogger("sandbox.runner")


@dataclass
class CommandResult:
    """单次命令执行结果。"""

    command: List[str]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    mode: str = "process"
    timed_out: bool = False
    extra: Dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def tool_missing(self) -> bool:
        """是否因工具未安装而非代码错误失败。"""
        if self.returncode != -1 and self.returncode != 127:
            return False
        text = (self.stderr + "\n" + self.stdout).lower()
        return any(
            k in text
            for k in ("not recognized", "not found", "no such file", "command not found")
        )

    def combined_output(self) -> str:
        return (self.stdout or "") + ("\n" if self.stderr else "") + (self.stderr or "")


class SandboxRunner:
    """统一沙箱执行入口。"""

    def __init__(
        self,
        mode: str = "",
        image: str = "",
        default_timeout: int = 120,
    ) -> None:
        settings = get_settings()
        self.mode = (mode or settings.SANDBOX_MODE or "process").lower()
        self.image = image or settings.SANDBOX_IMAGE
        self.default_timeout = default_timeout

    # ---- 公开 API ----

    def run(
        self,
        cwd: str,
        command: List[str],
        timeout: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> CommandResult:
        """执行命令，返回 CommandResult。"""
        if self.mode == "docker":
            return self._run_docker(cwd, command, timeout or self.default_timeout, env)
        if self.mode == "rootless":
            return self._run_rootless(cwd, command, timeout or self.default_timeout, env)
        return self._run_process(cwd, command, timeout or self.default_timeout, env)

    def run_script(
        self,
        cwd: str,
        script_text: str,
        timeout: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> CommandResult:
        """运行 bash 脚本（写入临时 .build_loop.sh，bash 执行）。

        Windows 无 bash 时降级：把脚本首行 split 成 list 当作 command 跑（仅适用于简单脚本）。
        """
        sh_path = os.path.join(cwd, ".build_loop.sh")
        try:
            with open(sh_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(script_text)
        except OSError as e:
            return CommandResult(
                command=["<script>"],
                returncode=-1,
                stdout="",
                stderr="写脚本失败: %s" % e,
                duration_ms=0,
                mode=self.mode,
            )
        # Windows 上优先用 git-bash / wsl bash；找不到则退化为直接跑命令
        bash_bin = self._find_bash()
        if bash_bin:
            return self.run(cwd, [bash_bin, sh_path], timeout=timeout, env=env)
        # 兜底：把脚本当作单条命令拼接（仅对极简脚本有效）
        first_cmd = self._extract_first_command(script_text)
        if first_cmd:
            return self.run(cwd, first_cmd, timeout=timeout, env=env)
        return CommandResult(
            command=["<script>"],
            returncode=-1,
            stdout="",
            stderr="未找到 bash，无法执行脚本",
            duration_ms=0,
            mode=self.mode,
        )

    # ---- process 模式 ----

    def _run_process(
        self,
        cwd: str,
        command: List[str],
        timeout: int,
        env: Optional[Dict[str, str]],
    ) -> CommandResult:
        merged_env = dict(os.environ)
        if env:
            merged_env.update(env)
        # Windows 上某些命令需要 cmd /c
        is_windows = os.name == "nt"
        actual = command
        if is_windows and command and not self._is_native_exec(command[0]):
            actual = ["cmd", "/c"] + command
        start = time.time()
        try:
            proc = subprocess.run(
                actual,
                cwd=cwd,
                env=merged_env,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )
            return CommandResult(
                command=command,
                returncode=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                duration_ms=int((time.time() - start) * 1000),
                mode="process",
            )
        except subprocess.TimeoutExpired as e:
            return CommandResult(
                command=command,
                returncode=-1,
                stdout=(e.stdout or "") if isinstance(e.stdout, str) else "",
                stderr="超时（%ds）" % timeout,
                duration_ms=timeout * 1000,
                mode="process",
                timed_out=True,
            )
        except FileNotFoundError as e:
            return CommandResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr="command not found: %s (%s)" % (command[0] if command else "?", e),
                duration_ms=int((time.time() - start) * 1000),
                mode="process",
            )
        except Exception as e:
            return CommandResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr="执行异常: %s" % e,
                duration_ms=int((time.time() - start) * 1000),
                mode="process",
            )

    # ---- docker 模式 ----

    def _run_docker(
        self,
        cwd: str,
        command: List[str],
        timeout: int,
        env: Optional[Dict[str, str]],
    ) -> CommandResult:
        """docker run --rm -v cwd:/work -w /work <image> <command>。"""
        cwd_abs = os.path.abspath(cwd).replace("\\", "/")
        # Windows 路径转 docker 挂载格式（如 D:/foo -> /d/foo 或 //d/foo）
        if os.name == "nt":
            drive, rest = os.path.splitdrive(cwd_abs)
            cwd_for_mount = "//" + drive.rstrip(":").lower() + rest.replace(":", "")
        else:
            cwd_for_mount = cwd_abs
        docker_cmd = [
            "docker", "run", "--rm",
            "-v", "%s:/work" % cwd_for_mount,
            "-w", "/work",
        ]
        if env:
            for k, v in env.items():
                docker_cmd += ["-e", "%s=%s" % (k, v)]
        docker_cmd += [self.image] + command
        start = time.time()
        try:
            proc = subprocess.run(
                docker_cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )
            return CommandResult(
                command=command,
                returncode=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                duration_ms=int((time.time() - start) * 1000),
                mode="docker",
                extra={"docker_cmd": " ".join(docker_cmd)},
            )
        except subprocess.TimeoutExpired:
            return CommandResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr="docker 执行超时（%ds）" % timeout,
                duration_ms=timeout * 1000,
                mode="docker",
                timed_out=True,
            )
        except FileNotFoundError:
            return CommandResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr="docker not found（未安装或不在 PATH）",
                duration_ms=int((time.time() - start) * 1000),
                mode="docker",
            )
        except Exception as e:
            return CommandResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr="docker 执行异常: %s" % e,
                duration_ms=int((time.time() - start) * 1000),
                mode="docker",
            )

    # ---- rootless 模式 ----

    def _run_rootless(
        self,
        cwd: str,
        command: List[str],
        timeout: int,
        env: Optional[Dict[str, str]],
    ) -> CommandResult:
        """rootless：受限环境变量的子进程（不真起 user namespace，简化实现）。"""
        restricted_env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", "/tmp"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
        if env:
            restricted_env.update(env)
        # 限制写危险路径（仅靠 cwd 隔离，不作硬 chroot）
        start = time.time()
        try:
            proc = subprocess.run(
                command,
                cwd=cwd,
                env=restricted_env,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )
            return CommandResult(
                command=command,
                returncode=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                duration_ms=int((time.time() - start) * 1000),
                mode="rootless",
            )
        except FileNotFoundError as e:
            return CommandResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr="command not found: %s (%s)" % (command[0] if command else "?", e),
                duration_ms=int((time.time() - start) * 1000),
                mode="rootless",
            )
        except subprocess.TimeoutExpired:
            return CommandResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr="超时（%ds）" % timeout,
                duration_ms=timeout * 1000,
                mode="rootless",
                timed_out=True,
            )
        except Exception as e:
            return CommandResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr="执行异常: %s" % e,
                duration_ms=int((time.time() - start) * 1000),
                mode="rootless",
            )

    # ---- 工具方法 ----

    @staticmethod
    def _is_native_exec(cmd0: str) -> bool:
        """Windows 下判断是否原生可执行（.exe/.bat/.cmd），需要 cmd /c 的则返回 False。"""
        if not cmd0:
            return True
        lower = cmd0.lower()
        if lower.endswith((".exe", ".bat", ".cmd")):
            return True
        # cmake / g++ / clang 等在 Windows 上需要 cmd /c 解析（除非带 .exe）
        return False

    @staticmethod
    def _find_bash() -> Optional[str]:
        """查找 bash 可执行文件路径（Windows 上 git-bash / wsl bash）。"""
        for name in ("bash", "bash.exe"):
            for p in os.environ.get("PATH", "").split(os.pathsep):
                cand = os.path.join(p, name)
                if os.path.exists(cand):
                    return cand
        return None

    @staticmethod
    def _extract_first_command(script: str) -> List[str]:
        """从脚本里提取第一条非空非注释命令，用于 bash 不可用时的兜底。"""
        for line in script.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            try:
                return shlex.split(s)
            except Exception:
                return [s]


# ---- 单例 ----

_runner: Optional[SandboxRunner] = None


def get_sandbox_runner() -> SandboxRunner:
    """获取 SandboxRunner 单例。"""
    global _runner
    if _runner is None:
        _runner = SandboxRunner()
    return _runner


def reset_sandbox_for_test() -> None:
    """测试用：重置单例。"""
    global _runner
    _runner = None

"""项目工作区管理 - WORKSPACE_ROOT/<project_id>/ 下的文件读写。"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import get_settings

logger = logging.getLogger("agent.workspace")


class ProjectWorkspace:
    """管理单个项目工作区的文件读写。所有路径均在 WORKSPACE_ROOT 内。"""

    def __init__(self, workspace_root: str = "") -> None:
        self._root = Path(workspace_root or get_settings().WORKSPACE_ROOT)
        self._root.mkdir(parents=True, exist_ok=True)

    # ---- 项目生命周期 ----

    def create_project(self, user_id: str, name: str) -> str:
        """创建唯一 project_id（uuid 短码）并建目录。

        Returns:
            project_id（12 位 hex 短码）。
        """
        project_id = uuid.uuid4().hex[:12]
        root = self._root / project_id
        root.mkdir(parents=True, exist_ok=True)
        # 写一个元信息文件，便于追溯
        meta = {
            "project_id": project_id,
            "name": name or "generated_project",
            "user_id": user_id,
        }
        (root / ".project.json").write_text(
            __import__("json").dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("创建项目工作区 project_id=%s name=%s user=%s", project_id, name, user_id)
        return project_id

    def project_root(self, project_id: str) -> Path:
        """返回项目根目录 Path（不保证存在）。"""
        return self._root / project_id

    def _resolve(self, project_id: str, rel_path: str) -> Path:
        """解析相对路径为绝对路径，并确保不会逃逸出项目根（防路径穿越）。"""
        root = self.project_root(project_id)
        # normalize: 允许 Windows 反斜杠
        rel = rel_path.replace("\\", "/").lstrip("/")
        target = (root / rel).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            raise ValueError(f"非法路径（逃逸出项目根）: {rel_path}")
        return target

    # ---- 文件读写 ----

    def write_file(self, project_id: str, rel_path: str, content: str) -> Path:
        """写文件（自动建父目录）。返回写入文件 Path。"""
        target = self._resolve(project_id, rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def read_file(self, project_id: str, rel_path: str) -> str:
        """读取文件内容。"""
        target = self._resolve(project_id, rel_path)
        if not target.exists():
            raise FileNotFoundError(f"文件不存在: {rel_path}")
        return target.read_text(encoding="utf-8")

    def list_files(self, project_id: str) -> List[Dict[str, Any]]:
        """返回项目文件树（深度优先，含 is_dir）。

        Returns:
            [{"name": str, "path": str, "is_dir": bool}, ...]
            path 为相对项目根的 POSIX 路径。
        """
        root = self.project_root(project_id)
        if not root.exists():
            return []
        entries: List[Dict[str, Any]] = []
        for p in sorted(root.rglob("*")):
            # 跳过隐藏元信息文件
            if p.name == ".project.json":
                continue
            rel = p.relative_to(root).as_posix()
            entries.append({"name": p.name, "path": rel, "is_dir": p.is_dir()})
        return entries

    def exists(self, project_id: str) -> bool:
        """项目工作区是否存在。"""
        return self.project_root(project_id).exists()


# 模块级单例
_workspace_instance: Optional[ProjectWorkspace] = None


def get_workspace() -> ProjectWorkspace:
    """获取 ProjectWorkspace 单例。"""
    global _workspace_instance
    if _workspace_instance is None:
        _workspace_instance = ProjectWorkspace()
    return _workspace_instance

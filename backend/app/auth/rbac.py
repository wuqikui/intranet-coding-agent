"""RBAC 访问控制矩阵。

角色：admin / dev / readonly。纯逻辑模块，不依赖数据库。
"""

# 各角色可执行的操作集合
PERMISSIONS: dict[str, set[str]] = {
    "admin": {
        "manage_users",
        "import_models",
        "maintenance_mode",
        "shell",
        "build",
        "generate",
        "read",
        "write",
        "delete",
        "push",
    },
    "dev": {
        "generate",
        "build",
        "shell",
        "read",
        "write",
        "delete_own",
        "git_diff",
        "run_tests",
    },
    "readonly": {
        "read",
        "chat",
    },
}


# 危险命令模式（子串匹配，大小写不敏感）
DANGEROUS_COMMANDS: list[str] = [
    "rm ",
    "git push",
    "rm -rf",
    "DROP",
    "DELETE FROM",
    "format",
    "mkfs",
    "dd if=",
    ":(){:|:&};:",
]


def can(role: str, action: str) -> bool:
    """检查角色是否拥有某操作权限。"""
    return action in PERMISSIONS.get(role, set())


def can_access_project(user_id: str, project_owner: str, role: str) -> bool:
    """检查用户能否访问某个项目。

    - admin 可访问所有项目；
    - dev 只能访问自己的项目；
    - readonly 不可访问他人项目（仅可访问自己的）。
    """
    if role == "admin":
        return True
    if role == "dev":
        return str(user_id) == str(project_owner)
    if role == "readonly":
        return str(user_id) == str(project_owner)
    return False


def is_dangerous(command: str) -> bool:
    """检查命令是否匹配危险模式（大小写不敏感的子串匹配）。"""
    if not command:
        return False
    lowered = command.lower()
    return any(pat.lower() in lowered for pat in DANGEROUS_COMMANDS)

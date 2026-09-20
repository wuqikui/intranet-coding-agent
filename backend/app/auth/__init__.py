"""鉴权模块：用户模型、JWT、本地/LDAP 认证、RBAC。"""
# 显式导入 User，确保其表注册到 Base.metadata，使 init_db() 的 create_all 能发现它。
from app.auth.models import User  # noqa: F401

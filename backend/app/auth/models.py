"""用户 ORM 模型。"""
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db import Base


# 角色常量
ROLE_ADMIN = "admin"
ROLE_DEV = "dev"
ROLE_READONLY = "readonly"
ALL_ROLES = (ROLE_ADMIN, ROLE_DEV, ROLE_READONLY)


class User(Base):
    """系统用户。

    本地账号存 password_hash；LDAP/AD 账号 password_hash 为空字符串，
    无法通过本地密码校验，必须走 LDAP。
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_DEV)
    # 本地账号用；LDAP 用户为空
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username} role={self.role}>"

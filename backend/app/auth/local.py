"""本地账号认证：bcrypt 密码哈希 + DB 校验。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from passlib.context import CryptContext

from app.auth.models import User

# bcrypt 哈希上下文
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """生成密码哈希。"""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文密码与哈希是否匹配；空哈希直接返回 False（LDAP 账号不可本地登录）。"""
    if not hashed:
        return False
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


async def authenticate_local(
    username: str, password: str, session: AsyncSession
) -> User | None:
    """按用户名查 DB 并校验密码，成功返回 User，否则 None。"""
    result = await session.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash or ""):
        return None
    return user

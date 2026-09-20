"""LDAP/AD 认证适配器。

ldap3 为可选依赖：未安装时模块仍可导入，但调用 authenticate_ldap 会抛出清晰错误。
"""
from app.config import get_settings

try:
    import ldap3  # type: ignore
    _LDAP3_AVAILABLE = True
except ImportError:  # ldap3 未安装
    ldap3 = None  # type: ignore
    _LDAP3_AVAILABLE = False


class LDAPNotConfiguredError(RuntimeError):
    """LDAP 不可用（未安装或未配置）。"""


def authenticate_ldap(username: str, password: str) -> bool:
    """连接 LDAP/AD 验证用户凭据，成功返回 True，失败返回 False。

    流程：
    1. 用绑定账号（LDAP_BIND_DN/LDAP_BIND_PASSWORD）连接并搜索用户 DN；
    2. 用用户 DN + 用户密码二次绑定验证密码。

    SSL/TLS 由 ldap3 根据 LDAP_URL 协议头（ldaps://）自动处理。
    """
    if not _LDAP3_AVAILABLE:
        raise LDAPNotConfiguredError("ldap3 未安装，无法使用 LDAP 认证")

    settings = get_settings()
    if not settings.LDAP_URL:
        raise LDAPNotConfiguredError("未配置 LDAP_URL")

    # 用户过滤器中的 {username} 替换为实际用户名
    user_filter = settings.LDAP_USER_FILTER.replace("{username}", username)
    server = ldap3.Server(settings.LDAP_URL)

    # 1. 绑定账号搜索用户 DN
    try:
        with ldap3.Connection(
            server,
            user=settings.LDAP_BIND_DN,
            password=settings.LDAP_BIND_PASSWORD,
            auto_bind=True,
        ) as conn:
            found = conn.search(
                search_base=settings.LDAP_BASE_DN,
                search_filter=user_filter,
                attributes=["dn"],
            )
            if not found or not conn.entries:
                return False
            user_dn = conn.entries[0].entry_dn
    except Exception:
        return False

    # 2. 用用户凭据绑定验证密码
    try:
        with ldap3.Connection(
            server,
            user=user_dn,
            password=password,
            auto_bind=True,
        ):
            return True
    except Exception:
        return False

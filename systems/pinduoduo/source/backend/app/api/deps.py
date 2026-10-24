# -*- coding: utf-8 -*-
"""
backend.app.api.deps —— 请求级依赖注入项
========================================
本文件用途：集中定义 backend 的 FastAPI 依赖注入项，核心为请求级鉴权依赖
``get_current_user``，满足需求 1.3/1.4：

- 解析请求头 ``Authorization: Bearer <token>``；
- 经 common.utils.security.decode_access_token 校验令牌有效性；
- 令牌缺失 / 格式错误 / 无效 / 过期 / 已主动失效（登出）时，抛出 ``AuthError``
  （业务码 40100「未登录或登录已过期」，由统一异常处理器转为 HTTP 200 +
  统一响应体）；
- 校验通过后据 ``sub``（用户 ID）查库取用户，被停用用户（status=0）拒绝
  （需求 2.7）；
- 返回当前登录用户模型 ``SysUser``，供受保护接口注入使用。

另提供 ``get_current_token`` 依赖：仅提取并返回原始 Bearer 令牌字符串，供
登出接口将当前令牌登记失效（需求 1.5）。

实现约束：数据库会话经 common.db.session.get_db 注入；数据访问经
common.db.repository 参数化查询（规范 16）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.business_codes import MSG_ACCOUNT_DISABLED, MSG_AUTH_REQUIRED
from app.core.errors import AuthError
from app.core.token_blacklist import get_token_blacklist
from common.db.repository import Repository
from common.db.session import get_db
from common.models.user_models import SysUser
from common.utils.security import decode_access_token

# 用户启用状态值（与 SysUser.status 约定一致：1=启用，0=停用）。
_USER_STATUS_ENABLED: int = 1
# Authorization 头的 Bearer 方案前缀（大小写不敏感比较）。
_BEARER_PREFIX: str = "bearer"


def get_current_token(request: Request) -> str:
    """从请求头解析并返回原始 Bearer 令牌字符串。

    仅负责提取令牌，不校验其有效性；缺失或格式非法时抛出 ``AuthError``。

    Args:
        request: 当前请求对象。

    Returns:
        提取到的令牌字符串。

    Raises:
        AuthError: Authorization 头缺失或非 Bearer 格式。
    """
    authorization = request.headers.get("Authorization") or ""
    parts = authorization.split(None, 1)
    # 必须为「Bearer <token>」两段式，且方案为 bearer（大小写不敏感）。
    if len(parts) != 2 or parts[0].lower() != _BEARER_PREFIX or not parts[1].strip():
        raise AuthError(MSG_AUTH_REQUIRED)
    return parts[1].strip()


def get_current_user(
    token: str = Depends(get_current_token),
    db: Session = Depends(get_db),
) -> SysUser:
    """请求级鉴权依赖：校验令牌并返回当前登录用户（需求 1.3/1.4）。

    校验流程：
    1. 解码并校验令牌签名与有效期（无效 / 过期返回 None → 判定未登录）；
    2. 校验令牌 ``jti`` 未被登出失效（需求 1.5）；
    3. 据 ``sub`` 取用户 ID 查库，用户不存在 → 判定未登录；
    4. 用户被停用（status != 1）→ 拒绝（需求 2.7）。

    Args:
        token: 由 ``get_current_token`` 提取的 Bearer 令牌。
        db: 数据库会话。

    Returns:
        当前登录用户模型 ``SysUser``。

    Raises:
        AuthError: 令牌无效 / 过期 / 已失效 / 用户不存在 / 用户被停用。
    """
    payload = decode_access_token(token)
    if not payload:
        # 签名错误、格式非法或已过期，统一判定为「未登录或登录已过期」。
        raise AuthError(MSG_AUTH_REQUIRED)

    # 令牌已被主动失效（登出）：拒绝（需求 1.5）。
    jti = payload.get("jti")
    if jti and get_token_blacklist().is_revoked(jti):
        raise AuthError(MSG_AUTH_REQUIRED)

    # 取令牌主体 sub（用户 ID），缺失或非法一律判定未登录。
    user_id = _parse_user_id(payload.get("sub"))
    if user_id is None:
        raise AuthError(MSG_AUTH_REQUIRED)

    user = Repository(SysUser, db).get(user_id)
    if user is None:
        raise AuthError(MSG_AUTH_REQUIRED)

    # 被停用用户拒绝鉴权（需求 2.7）。
    if user.status != _USER_STATUS_ENABLED:
        raise AuthError(MSG_ACCOUNT_DISABLED)

    return user


def _parse_user_id(sub: object) -> Optional[int]:
    """将令牌 ``sub`` 安全解析为用户 ID（整数）。

    Args:
        sub: 令牌主体，签发时统一序列化为字符串形式的用户 ID。

    Returns:
        解析成功返回整数用户 ID；为空或非法返回 None。
    """
    if sub is None:
        return None
    try:
        return int(str(sub))
    except (ValueError, TypeError):
        return None


__all__ = [
    "get_current_token",
    "get_current_user",
]

# -*- coding: utf-8 -*-
"""
channel_pdd.core.credential_store —— 拼多多店铺凭据读写适配
==========================================================
本文件用途：为拼多多基础请求层（BaseRequest）提供「按店铺业务标识加载 / 更新
Cookie 凭据」的统一适配层，桥接 common 公共库的数据模型与可逆加密工具。

职责：
- ``load_account_cookies``：按渠道 + 店铺业务标识（shop_id）+ 归属用户（user_id）
  定位账号记录，解密其 Cookie 密文并解析为字典（供请求携带）。
- ``load_account_credentials``：加载账号的登录凭据（用户名 / 解密后的密码），
  供会话过期时自动重登使用。
- ``update_account_cookies``：会话刷新 / 重登后，将新的 Cookie 以可逆加密回写
  account 表，并保证不返回明文（需求 3.6）。

实现约束（开发规范）：
- 所有数据访问经 common.db.repository 参数化查询，禁止拼接 SQL（规范 16）。
- Cookie / 密码以 common.utils.crypto 可逆加密存储与解密（需求 3.6 / 8.6）。
- 时间统一北京时间；导入置顶；中文注释完善（规范 17 / 51 / 37）。

说明：账号定位遵循 common 数据模型——先按 (owner_user_id, shop_id) 在 shop 表
定位店铺主键 shop_pk，再按 (shop_pk, user_id) 在 account 表定位账号凭据。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from common.db.repository import Repository
from common.db.session import session_scope
from common.models.shop_models import Account, Shop
from common.utils.crypto import encrypt_text, try_decrypt_text
from sqlalchemy import update, exists, select

logger = logging.getLogger("channel_pdd.credential_store")


@dataclass(frozen=True)
class CredentialSnapshot:
    account_id: int
    shop_pk: int
    shop_id: str
    user_id: int
    encrypted: Optional[str] = field(repr=False)
    cookies: Dict[str, Any] = field(repr=False)


def credential_snapshot(shop_id: str, user_id: int):
    with session_scope() as session:
        account = _locate_account(session, shop_id, user_id)
        if account is None:
            return None
        shop = session.get(Shop, account.shop_pk)
        if shop.status != 1 or shop.merged_into_shop_pk is not None:
            return None
        return CredentialSnapshot(account.id, shop.id, str(shop_id), user_id,
            account.cookies_enc, _parse_cookies(try_decrypt_text(account.cookies_enc)))


def save_session_observation(snapshot, state: str, cookies=None):
    """只接受这份凭据的观察；新授权/停用/换归属使迟到结果失效。"""
    if snapshot is None or state not in ('online', 'relogin'):
        return False
    values = {'login_state': state}
    if cookies is not None:
        if not isinstance(cookies, dict) or not cookies:
            return False
        values['cookies_enc'] = encrypt_text(json.dumps(cookies, ensure_ascii=False))
    with session_scope() as session:
        allowed = exists(select(Shop.id).where(Shop.id == snapshot.shop_pk,
            Shop.shop_id == snapshot.shop_id, Shop.owner_user_id == snapshot.user_id,
            Shop.status == 1, Shop.merged_into_shop_pk.is_(None)))
        result = session.execute(update(Account).where(Account.id == snapshot.account_id,
            Account.shop_pk == snapshot.shop_pk, Account.user_id == snapshot.user_id,
            Account.cookies_enc == snapshot.encrypted, allowed).values(**values))
        return result.rowcount == 1


def _parse_cookies(cookies_text: Optional[str]) -> Dict[str, Any]:
    """将 Cookie 文本（JSON 字符串）解析为字典。

    Args:
        cookies_text: Cookie 明文（通常为 JSON 字符串）；None / 空 / 非法时返回空字典。

    Returns:
        解析得到的 Cookie 字典；解析失败返回空字典（不抛异常，避免中断请求）。
    """
    if not cookies_text:
        return {}
    try:
        data = json.loads(cookies_text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Cookie 文本解析失败，返回空 Cookie 字典")
        return {}
    return data if isinstance(data, dict) else {}


def _locate_account(session: Any, shop_id: str, user_id: int) -> Optional[Account]:
    """按店铺业务标识与归属用户定位账号记录。

    先按 (owner_user_id, shop_id) 在 shop 表定位店铺主键 shop_pk，再按
    (shop_pk, user_id) 在 account 表定位账号凭据。

    Args:
        session: 数据库会话。
        shop_id: 拼多多店铺业务标识（业务键）。
        user_id: 归属用户 ID。

    Returns:
        命中的 ``Account`` 实例；店铺或账号不存在时返回 None。
    """
    shop = Repository(Shop, session).get_by(owner_user_id=user_id, shop_id=shop_id)
    if shop is None:
        logger.warning("未找到店铺记录: shop_id=%s, user_id=%s", shop_id, user_id)
        return None
    account = Repository(Account, session).get_by(shop_pk=shop.id, user_id=user_id)
    if account is None:
        logger.warning("未找到账号凭据: shop_pk=%s, user_id=%s", shop.id, user_id)
    return account


def load_account_cookies(shop_id: str, user_id: int, channel_name: str = "pinduoduo") -> Dict[str, Any]:
    """加载并解密指定店铺账号的 Cookie 凭据（需求 3.6）。

    Args:
        shop_id: 拼多多店铺业务标识。
        user_id: 归属用户 ID。
        channel_name: 渠道名称（默认 pinduoduo，预留多渠道扩展）。

    Returns:
        解密并解析后的 Cookie 字典；账号不存在或解密失败时返回空字典。
    """
    with session_scope() as session:
        account = _locate_account(session, shop_id, user_id)
        if account is None:
            return {}
        # 容错解密：历史坏数据 / 密钥不匹配时返回 None，不中断流程。
        plain = try_decrypt_text(account.cookies_enc)
        return _parse_cookies(plain)


def load_account_credentials(
    shop_id: str, user_id: int, channel_name: str = "pinduoduo"
) -> Optional[Tuple[Optional[str], Optional[str]]]:
    """加载账号登录凭据（用户名 + 解密后的密码），供会话过期自动重登使用。

    Args:
        shop_id: 拼多多店铺业务标识。
        user_id: 归属用户 ID。
        channel_name: 渠道名称（默认 pinduoduo）。

    Returns:
        ``(username, password)`` 元组；账号不存在返回 None。密码可能为 None
        （仅导入 Cookie 而未存密码的账号）。
    """
    with session_scope() as session:
        account = _locate_account(session, shop_id, user_id)
        if account is None:
            return None
        username = account.username
        password = try_decrypt_text(account.password_enc)
        return username, password


def update_account_cookies(
    shop_id: str,
    user_id: int,
    new_cookies: Any,
    channel_name: str = "pinduoduo",
) -> bool:
    """将新的 Cookie 以可逆加密回写 account 表（会话刷新 / 重登后调用，需求 3.6）。

    Args:
        shop_id: 拼多多店铺业务标识。
        user_id: 归属用户 ID。
        new_cookies: 新 Cookie（字典或 JSON 字符串）。
        channel_name: 渠道名称（默认 pinduoduo）。

    Returns:
        回写成功返回 True；账号不存在返回 False。
    """
    # 统一序列化为 JSON 字符串后加密存储（与导入时的存储口径一致）。
    if isinstance(new_cookies, dict):
        cookies_text = json.dumps(new_cookies, ensure_ascii=False)
    elif isinstance(new_cookies, str):
        cookies_text = new_cookies
    else:
        logger.error("不支持的 Cookie 数据类型，回写中止: %s", type(new_cookies))
        return False

    with session_scope() as session:
        account = _locate_account(session, shop_id, user_id)
        if account is None:
            return False
        Repository(Account, session).update(account.id, cookies_enc=encrypt_text(cookies_text))
        return True


__all__ = [
    "load_account_cookies",
    "load_account_credentials",
    "update_account_cookies",
]

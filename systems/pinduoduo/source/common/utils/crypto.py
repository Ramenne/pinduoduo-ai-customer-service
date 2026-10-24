# -*- coding: utf-8 -*-
"""
common.utils.crypto —— 敏感字段可逆加密工具
============================================
本文件用途：为「拼多多自动回复」系统提供「可逆」的对称加密能力，专用于对需要
还原使用的敏感字段（如 Cookie 凭据、店铺账号密码）做加密存储与解密读取，满足
需求 3.6 / 8.6「对 Cookie 凭据与密码进行加密存储，且不在列表查询响应中返回明文」。

与 common.utils.security 的区别：
- security 中的密码哈希是 **不可逆** 的（用于用户登录密码校验，绝不还原）；
- 本模块的加密是 **可逆** 的（用于店铺 Cookie / 账号密码等需要解密后再用于登录、
  刷新、建立连接的凭据）。二者用途不同，不可混用。

实现要点：
- 基于 ``cryptography`` 库的 Fernet（AES-128-CBC + HMAC）对称加密，密文自带完整性
  校验与时间戳，篡改可被检测。
- 加密密钥由配置 ``data_encrypt_key``（环境变量 DATA_ENCRYPT_KEY 优先）经 SHA-256
  派生为 32 字节，再 urlsafe-base64 编码为 Fernet 所需密钥，避免要求运维必须提供
  严格的 44 字节 base64 密钥（规范 21：经环境变量管理，禁止写死）。
- 加密结果为字符串（urlsafe base64 文本），可直接落库到 Text 字段。
- 提供 None / 空串的安全处理：加密入参为 None 返回 None；解密入参为 None 返回 None。

注意：加密强度依赖于 ``data_encrypt_key`` 的保密性，生产环境必须经环境变量注入足够
随机的密钥，切勿使用默认占位值。
"""
from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from common.core.config import Settings, get_settings


def _derive_fernet_key(secret: str) -> bytes:
    """将任意长度的配置密钥派生为 Fernet 所需的 32 字节 urlsafe-base64 密钥。

    通过 SHA-256 将任意输入摘要为固定 32 字节，再 urlsafe-base64 编码，得到
    Fernet 规定格式的密钥。这样运维只需提供任意字符串密钥即可，无需手工生成
    严格格式的 base64 密钥。

    Args:
        secret: 配置中的原始密钥字符串（来自环境变量或默认占位值）。

    Returns:
        Fernet 可直接使用的 32 字节 urlsafe-base64 密钥（bytes）。
    """
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache
def _get_fernet() -> Fernet:
    """构造并缓存 Fernet 加密器（进程内单例）。

    密钥取自 common 配置的 ``data_encrypt_key``（环境变量优先）。使用 lru_cache
    避免每次加解密都重复派生密钥。测试或密钥轮换后可调用 ``reset_cipher`` 重置。

    Returns:
        基于派生密钥构造的 ``Fernet`` 实例。
    """
    settings: Settings = get_settings()
    return Fernet(_derive_fernet_key(settings.data_encrypt_key))


def reset_cipher() -> None:
    """清空 Fernet 加密器缓存（供测试在变更密钥后重建，或密钥轮换时调用）。"""
    _get_fernet.cache_clear()


def encrypt_text(plain: Optional[str]) -> Optional[str]:
    """对明文字符串做可逆加密，返回密文字符串。

    Args:
        plain: 待加密的明文；为 None 时原样返回 None（便于可空字段直接透传）。

    Returns:
        加密后的密文字符串（urlsafe base64 文本）；入参为 None 时返回 None。
    """
    if plain is None:
        return None
    token = _get_fernet().encrypt(plain.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_text(token: Optional[str]) -> Optional[str]:
    """对密文字符串做解密，返回原始明文。

    Args:
        token: 待解密的密文字符串；为 None 时原样返回 None。

    Returns:
        解密还原的明文字符串；入参为 None 时返回 None。

    Raises:
        InvalidToken: 当密文非法、被篡改或密钥不匹配时由 Fernet 抛出，调用方
            可据此判定凭据不可用。
    """
    if token is None:
        return None
    plain = _get_fernet().decrypt(token.encode("utf-8"))
    return plain.decode("utf-8")


def try_decrypt_text(token: Optional[str]) -> Optional[str]:
    """对密文做「容错解密」：解密失败（非法 / 篡改）时返回 None，不抛异常。

    适用于读取历史数据或不确定是否为合法密文的场景，避免单条坏数据中断流程。

    Args:
        token: 待解密的密文字符串。

    Returns:
        成功返回明文；入参为 None 或解密失败返回 None。
    """
    if token is None:
        return None
    try:
        return decrypt_text(token)
    except (InvalidToken, ValueError, TypeError):
        # 非法密文 / 密钥不匹配 / 类型错误：一律视为不可用，返回 None
        return None


__all__ = [
    "encrypt_text",
    "decrypt_text",
    "try_decrypt_text",
    "reset_cipher",
]

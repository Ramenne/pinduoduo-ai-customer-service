# -*- coding: utf-8 -*-
"""
common.schemas.sanitize —— 敏感字段脱敏序列化模块
==================================================
本文件用途：为「拼多多自动回复」系统提供对外响应 DTO 序列化时统一移除 /
脱敏敏感字段的机制，供各服务（backend、websocket 等）在返回响应前调用。

依据需求 1.6 / 3.6 / 8.6 / 21.7 与设计文档 Property 9「敏感字段不外泄」：
- 对任意含敏感字段（Cookie、密码、API 密钥、SMTP 密码 等）的记录，其对外
  响应 DTO 序列化结果中不得包含上述敏感字段的明文或哈希值。
- 敏感字段同时覆盖「明文」与「加密 / 哈希」两类命名（如 password 与
  password_hash / password_enc、cookies 与 cookies_enc 等），二者均不外泄。

本模块提供：
- ``DEFAULT_SENSITIVE_FIELDS``：默认敏感字段名集合（小写、可扩展）。
- ``is_sensitive_field``：判断单个字段名是否为敏感字段（大小写不敏感）。
- ``sanitize_sensitive``：对 dict / list / pydantic 模型等结构递归脱敏，
  支持「移除」与「掩码」两种策略，并可通过 ``extra_fields`` 追加字段。
- ``SafeDTO``：pydantic 基类 / mixin，提供 ``safe_dump`` 输出已脱敏的字典。

实现原则：纯逻辑、无副作用，便于属性测试（任务 2.19）覆盖。
"""
from __future__ import annotations

from typing import Any, Iterable

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# 默认敏感字段名集合（统一以「小写」形式维护，匹配时大小写不敏感）。
# 同时覆盖明文与加密 / 哈希命名，确保二者均不外泄。
# ---------------------------------------------------------------------------
DEFAULT_SENSITIVE_FIELDS: frozenset[str] = frozenset(
    {
        # Cookie 凭据：明文与加密形态。
        "cookie",
        "cookies",
        "cookie_enc",
        "cookies_enc",
        "cookie_encrypted",
        "cookies_encrypted",
        # 用户 / 账号密码：明文、哈希、加密形态及常见别名。
        "password",
        "passwd",
        "pwd",
        "password_hash",
        "passwd_hash",
        "password_enc",
        "password_encrypted",
        "hashed_password",
        "pwd_hash",
        # API 密钥（如大语言模型 API Key）：明文与加密形态及常见别名。
        "api_key",
        "apikey",
        "api_key_enc",
        "api_key_encrypted",
        "api_secret",
        "secret_key",
        # SMTP 邮件密码 / 授权码：明文与加密形态。
        "smtp_password",
        "smtp_pwd",
        "smtp_password_enc",
        "smtp_auth_code",
        "smtp_authorization_code",
        # 通用秘密 / 令牌类（防御性补充，避免凭据外泄）。
        "secret",
        "access_token",
        "refresh_token",
        "private_key",
    }
)

# 掩码策略下用于替换敏感值的占位文案。
DEFAULT_MASK_VALUE: str = "******"


def _normalize_fields(extra_fields: Iterable[str] | None) -> frozenset[str]:
    """将默认敏感字段与调用方追加字段合并为统一的小写集合。

    Args:
        extra_fields: 调用方追加的敏感字段名（大小写不限），可为 None。

    Returns:
        合并并统一小写后的敏感字段名集合。
    """
    if not extra_fields:
        return DEFAULT_SENSITIVE_FIELDS
    # 追加字段统一转小写后并入默认集合，保证匹配口径一致。
    extra_lower = {str(name).lower() for name in extra_fields}
    return DEFAULT_SENSITIVE_FIELDS | extra_lower


def is_sensitive_field(name: Any, extra_fields: Iterable[str] | None = None) -> bool:
    """判断给定字段名是否属于敏感字段（大小写不敏感）。

    仅对字符串类型的键进行判定；非字符串键（如 int）一律视为非敏感。

    Args:
        name: 待判定的字段名。
        extra_fields: 追加的敏感字段名集合，可为 None。

    Returns:
        当字段名命中敏感字段集合时返回 True，否则返回 False。
    """
    if not isinstance(name, str):
        return False
    sensitive = _normalize_fields(extra_fields)
    return name.lower() in sensitive


def sanitize_sensitive(
    data: Any,
    extra_fields: Iterable[str] | None = None,
    mask: bool = False,
    mask_value: str = DEFAULT_MASK_VALUE,
) -> Any:
    """对任意数据结构递归脱敏，移除或掩码其中的敏感字段。

    支持的输入类型：
    - pydantic ``BaseModel``：先序列化为 dict 再递归脱敏。
    - ``dict``：对每个键判定是否敏感，敏感键被移除或掩码，其余值递归处理。
    - ``list`` / ``tuple`` / ``set``：对每个元素递归处理（统一返回 list）。
    - 其它标量类型：原样返回。

    脱敏策略：
    - ``mask=False``（默认）：直接「移除」敏感键，序列化结果不含该字段。
    - ``mask=True``：保留键但将其值替换为 ``mask_value`` 掩码文案。

    无论何种策略，输出结果都不再包含敏感字段的原始明文或哈希值，从而满足
    Property 9「敏感字段不外泄」。

    Args:
        data: 待脱敏的数据（模型 / dict / list / 标量等）。
        extra_fields: 追加的敏感字段名，可为 None。
        mask: 是否采用掩码策略；False 为移除，True 为掩码。
        mask_value: 掩码占位文案，仅在 ``mask=True`` 时生效。

    Returns:
        脱敏后的新数据结构（不修改原始入参）。
    """
    sensitive = _normalize_fields(extra_fields)
    return _sanitize(data, sensitive, mask, mask_value)


def _sanitize(
    data: Any,
    sensitive: frozenset[str],
    mask: bool,
    mask_value: str,
) -> Any:
    """递归脱敏的内部实现（敏感字段集合已预先归一化）。

    Args:
        data: 待脱敏数据。
        sensitive: 已归一化（小写）的敏感字段名集合。
        mask: 是否采用掩码策略。
        mask_value: 掩码占位文案。

    Returns:
        脱敏后的新数据结构。
    """
    # pydantic 模型：序列化为 dict 后按 dict 规则递归处理。
    if isinstance(data, BaseModel):
        return _sanitize(data.model_dump(), sensitive, mask, mask_value)

    # 字典：逐键判定敏感性，敏感键移除或掩码，非敏感值递归处理。
    if isinstance(data, dict):
        result: dict[Any, Any] = {}
        for key, value in data.items():
            if isinstance(key, str) and key.lower() in sensitive:
                if mask:
                    # 掩码策略：保留键，值替换为占位文案。
                    result[key] = mask_value
                # 移除策略：直接跳过该键，结果中不出现。
                continue
            result[key] = _sanitize(value, sensitive, mask, mask_value)
        return result

    # 列表 / 元组 / 集合：逐元素递归处理，统一返回 list 以便 JSON 序列化。
    if isinstance(data, (list, tuple, set)):
        return [_sanitize(item, sensitive, mask, mask_value) for item in data]

    # 其它标量类型：原样返回。
    return data


class SafeDTO(BaseModel):
    """对外 DTO 安全序列化基类 / mixin。

    继承本类的 pydantic 模型在对外输出时应使用 ``safe_dump`` 而非裸 ``model_dump``，
    以确保敏感字段（含嵌套结构）被统一脱敏。子类可通过类属性
    ``__sensitive_extra_fields__`` 追加本模型特有的敏感字段名。

    示例：
        class ShopDTO(SafeDTO):
            __sensitive_extra_fields__ = ("anti_content",)
            shop_id: str
            cookies: str | None = None
    """

    # 子类可覆盖，追加本模型特有的敏感字段名（大小写不限）。
    __sensitive_extra_fields__: tuple[str, ...] = ()

    def safe_dump(
        self,
        mask: bool = False,
        mask_value: str = DEFAULT_MASK_VALUE,
        **model_dump_kwargs: Any,
    ) -> dict[str, Any]:
        """输出已脱敏的字典表示，供对外响应使用。

        先调用 ``model_dump`` 序列化，再对结果递归脱敏，自动并入子类声明的
        ``__sensitive_extra_fields__`` 追加字段。

        Args:
            mask: 是否采用掩码策略；False 为移除，True 为掩码。
            mask_value: 掩码占位文案，仅在 ``mask=True`` 时生效。
            **model_dump_kwargs: 透传给 pydantic ``model_dump`` 的其它参数
                （如 ``by_alias``、``exclude_none`` 等）。

        Returns:
            脱敏后的字典。
        """
        raw = self.model_dump(**model_dump_kwargs)
        return sanitize_sensitive(
            raw,
            extra_fields=self.__sensitive_extra_fields__,
            mask=mask,
            mask_value=mask_value,
        )


__all__ = [
    "DEFAULT_SENSITIVE_FIELDS",
    "DEFAULT_MASK_VALUE",
    "is_sensitive_field",
    "sanitize_sensitive",
    "SafeDTO",
]

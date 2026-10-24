# -*- coding: utf-8 -*-
"""
敏感字段不外泄属性测试（common.schemas.sanitize）
==================================================
本文件用途：以 Hypothesis 属性测试验证对外响应 DTO 序列化脱敏机制，
对应设计文档 Property 9「敏感字段不外泄」（Validates: Requirements 1.6, 3.6, 8.6, 21.7）。

即：对任意含敏感字段（Cookie、密码、API 密钥、SMTP 密码 等）的记录，
经 sanitize_sensitive 处理后：
- 移除策略：递归遍历输出，不存在任何敏感字段键；
- 掩码策略：敏感字段键虽保留，但其值被替换为掩码文案，不等于原始明文；
两种策略下普通字段均原样保留。
"""
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from common.schemas.sanitize import (
    DEFAULT_MASK_VALUE,
    DEFAULT_SENSITIVE_FIELDS,
    is_sensitive_field,
    sanitize_sensitive,
)

# 敏感字段名列表（来自被测实现的默认集合），用于取样构造敏感键。
_SENSITIVE_NAMES = sorted(DEFAULT_SENSITIVE_FIELDS)

# 普通字段名生成器：标识符风格字符串，且不命中敏感字段集合（大小写不敏感）。
_normal_key_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=12
).filter(lambda k: not is_sensitive_field(k))

# 敏感字段名生成器：从默认敏感集合取样（可随机大小写变体，验证大小写不敏感匹配）。
_sensitive_key_strategy = st.sampled_from(_SENSITIVE_NAMES).flatmap(
    lambda name: st.sampled_from([name, name.upper(), name.capitalize()])
)

# 敏感字段「明文」值生成器：以唯一前缀标记的非空字符串，作为待保护的原始凭据值。
# 前缀含 "#" 字符，而普通字段值的字母表不含 "#"，从而避免普通值与敏感明文意外相等
# 导致的误报（保证「敏感明文不外泄」断言的准确性）。
_SECRET_PREFIX = "SECRET#"
_secret_value_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=40
).map(lambda s: _SECRET_PREFIX + s)

# 标量叶子值生成器：普通字段可取的简单值（文本字母表刻意排除敏感前缀字符 "#"）。
_scalar_strategy = st.one_of(
    st.integers(),
    st.booleans(),
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_ ", max_size=20),
    st.none(),
)


def _record_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """构造可含嵌套结构、且至少包含一个敏感字段的记录生成器。

    Returns:
        生成 dict 的 Hypothesis 策略：键含随机普通字段与随机敏感字段，
        值可为标量，也可为嵌套的 dict / list（其中也可能包含敏感字段）。
    """
    # 递归定义：叶子为标量，节点可为 dict 或 list，内部允许再次出现敏感字段。
    def extend(children: st.SearchStrategy[Any]) -> st.SearchStrategy[Any]:
        nested_dict = st.dictionaries(
            keys=st.one_of(_normal_key_strategy, _sensitive_key_strategy),
            values=children,
            max_size=4,
        )
        nested_list = st.lists(children, max_size=4)
        return st.one_of(nested_dict, nested_list)

    value_tree = st.recursive(_scalar_strategy, extend, max_leaves=15)

    # 普通字段子字典（键均为非敏感字段）。
    normal_part = st.dictionaries(
        keys=_normal_key_strategy,
        values=value_tree,
        max_size=5,
    )
    # 敏感字段子字典（键均为敏感字段，值为明文凭据），保证至少一个敏感字段存在。
    sensitive_part = st.dictionaries(
        keys=_sensitive_key_strategy,
        values=_secret_value_strategy,
        min_size=1,
        max_size=5,
    )

    # 合并：敏感部分覆盖在普通部分之上，得到含敏感字段的完整记录。
    return st.builds(lambda n, s: {**n, **s}, normal_part, sensitive_part)


def _collect_sensitive_plaintexts(data: Any, found: list[str]) -> None:
    """递归收集原始记录中所有敏感字段的明文值，供后续断言「不外泄」。

    Args:
        data: 原始记录（dict / list / 标量）。
        found: 累积收集到的敏感明文值列表（原地追加）。
    """
    if isinstance(data, dict):
        for key, value in data.items():
            # 仅追踪以 SECRET# 前缀标记的明文凭据：嵌套结构中由递归值生成器产生的
            # 敏感键可能取到空串 / 普通字符串（非真实凭据），这类值会与普通字段值
            # 意外相等，导致「不外泄」断言误报。前缀标记确保被追踪的均为真实待保护
            # 明文，且其字母表不与普通字段值重叠（保证断言准确）。
            if (
                is_sensitive_field(key)
                and isinstance(value, str)
                and value.startswith(_SECRET_PREFIX)
            ):
                found.append(value)
            _collect_sensitive_plaintexts(value, found)
    elif isinstance(data, (list, tuple, set)):
        for item in data:
            _collect_sensitive_plaintexts(item, found)


def _iter_dict_items(data: Any):
    """递归遍历嵌套结构中的所有 (键, 值) 对，用于检查输出中的字段。

    Args:
        data: 待遍历的数据（dict / list / 标量）。

    Yields:
        结构中出现的每个 (key, value) 二元组。
    """
    if isinstance(data, dict):
        for key, value in data.items():
            yield key, value
            yield from _iter_dict_items(value)
    elif isinstance(data, (list, tuple, set)):
        for item in data:
            yield from _iter_dict_items(item)


@settings(max_examples=200)
@given(record=_record_strategy())
def test_sanitize_does_not_leak_sensitive_fields(record):
    """Feature: pdd-auto-reply, Property 9: 敏感字段不外泄

    对任意含敏感字段（Cookie、密码、API 密钥、SMTP 密码 等）的记录：
    - 移除策略：脱敏输出中递归遍历不存在任何敏感字段键；
    - 掩码策略：敏感字段键保留但值被替换为掩码文案，不等于原始明文；
    两种策略下普通字段均原样保留，从而保证敏感明文不外泄。
    """
    # 收集原始记录中所有敏感字段的明文值。
    secrets: list[str] = []
    _collect_sensitive_plaintexts(record, secrets)
    # 生成器保证至少包含一个敏感字段。
    assert secrets, "测试前置条件：记录中应至少包含一个敏感字段"

    # ---- 策略一：移除 ----
    removed = sanitize_sensitive(record, mask=False)
    for key, value in _iter_dict_items(removed):
        # 输出中不应再出现任何敏感字段键。
        assert not is_sensitive_field(key), f"移除策略下仍存在敏感字段键: {key}"
    # 普通字段保留：顶层非敏感键应原样存在且值经递归脱敏后不变（不含敏感子键）。
    for key in record:
        if not is_sensitive_field(key):
            assert key in removed, f"移除策略下普通字段丢失: {key}"

    # ---- 策略二：掩码 ----
    masked = sanitize_sensitive(record, mask=True)
    for key, value in _iter_dict_items(masked):
        if is_sensitive_field(key):
            # 敏感字段值必须被掩码，不等于任何原始明文。
            assert value == DEFAULT_MASK_VALUE, f"掩码策略下敏感字段未掩码: {key}={value!r}"
            assert value not in secrets, f"掩码策略下敏感明文外泄: {key}"
    # 掩码策略保留所有顶层键（普通字段与敏感字段键均在）。
    for key in record:
        assert key in masked, f"掩码策略下字段丢失: {key}"

    # ---- 两种策略均不得在任意值中泄露敏感明文 ----
    for secret in secrets:
        for _, value in _iter_dict_items(removed):
            assert value != secret, "移除策略下敏感明文外泄"
        for _, value in _iter_dict_items(masked):
            assert value != secret, "掩码策略下敏感明文外泄"

# -*- coding: utf-8 -*-
"""
密码哈希属性测试（common.utils.security）
=========================================
本文件用途：以 Hypothesis 属性测试验证 common.utils.security 中密码哈希与校验
能力的正确性，对应设计文档 Property 1「密码哈希不可逆且可校验」，校验需求
1.6（密码以哈希存储、不返回明文/哈希）与需求 22.2（个人设置修改密码校验）。

测试策略：
- 统一采用 Hypothesis，每个属性最少 100 次迭代（max_examples=100）。
- 生成器覆盖非空密码字符串，含 unicode 字符与较长字符串等代表性场景；
  实现采用 bcrypt_sha256（先 SHA-256 预哈希再交 bcrypt），已规避原生 bcrypt
  72 字节截断问题，故生成器可放宽长度上限以覆盖长密码，同时限制极端长度避免
  测试过慢。
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from common.utils.security import hash_password, verify_password

# 非空密码生成器：长度 1~256，包含任意可打印/unicode 字符（覆盖中文、emoji、
# 长字符串等代表性场景）。限制上限为 256 以兼顾覆盖度与执行速度。
_password_strategy = st.text(min_size=1, max_size=256)


# Feature: pdd-auto-reply, Property 1: 密码哈希不可逆且可校验
# 对任意非空密码字符串，其哈希值不等于明文，使用同一密码校验该哈希应通过，
# 使用不同密码校验应失败。
# Validates: Requirements 1.6, 22.2
#
# 说明：bcrypt（bcrypt_sha256）属于「故意计算昂贵」的密码哈希算法，单次哈希/
# 校验耗时较高（数百毫秒），因此关闭 Hypothesis 默认 200ms 截止时间
# （deadline=None），避免将「算法本就慢」误判为属性失败；max_examples=100
# 满足「最少 100 次迭代」要求。
@settings(max_examples=100, deadline=None)
@given(password=_password_strategy, other=_password_strategy)
def test_password_hash_irreversible_and_verifiable(password: str, other: str) -> None:
    """Property 1：哈希不等于明文、同密码校验通过、不同密码校验失败。"""
    hashed = hash_password(password)

    # 1）不可逆性的基本体现：哈希值不等于明文（需求 1.6）
    assert hashed != password

    # 2）可校验性：使用同一密码校验该哈希应通过（需求 22.2）
    assert verify_password(password, hashed) is True

    # 3）不同密码校验应失败：仅当两个密码确实不同时才断言失败，
    #    避免生成器偶然产生相同字符串导致误判。
    if other != password:
        assert verify_password(other, hashed) is False

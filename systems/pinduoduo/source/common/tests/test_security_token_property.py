# -*- coding: utf-8 -*-
"""
登录令牌往返属性测试（common.utils.security）
=============================================
本文件用途：以 Hypothesis 属性测试验证 JWT 令牌的「签发-校验往返」正确性，
对应设计文档 Property 2（Validates: Requirements 1.3）。即：对任意有效用户
身份签发访问令牌后再校验，应能还原出同一用户身份（sub）且令牌有效（非 None）。

说明：实现 create_access_token 会将令牌主体 sub 统一序列化为字符串，因此本
测试断言解码后的 sub 等于「原始身份的字符串形式」。
"""
from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from common.utils.security import create_access_token, decode_access_token

# 用户身份 sub 生成器：覆盖「整数用户 ID」与「非空用户名字符串」两类有效身份
_subject_strategy = st.one_of(
    st.integers(min_value=1, max_value=10**12),
    st.text(min_size=1, max_size=64),
)

# 额外自定义声明生成器：键为标识符风格字符串，值为字符串/整数/布尔，
# 排除会与标准声明冲突的保留键，避免覆盖 sub/iat/exp/jti/ver
_reserved_claims = {"sub", "iat", "exp", "jti", "ver", "nbf", "aud", "iss"}
_extra_claims_strategy = st.dictionaries(
    keys=st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=12).filter(
        lambda k: k not in _reserved_claims
    ),
    values=st.one_of(st.text(max_size=32), st.integers(), st.booleans()),
    max_size=4,
)


@settings(max_examples=200)
@given(subject=_subject_strategy, extra_claims=_extra_claims_strategy)
def test_login_token_sign_verify_roundtrip(subject, extra_claims):
    """Feature: pdd-auto-reply, Property 2: 登录令牌签发-校验往返

    对任意有效用户身份（整数 ID 或非空用户名）及任意额外声明：
    签发令牌后再校验，应还原出同一用户身份（sub 的字符串形式）且令牌有效（非 None）。
    """
    # 使用足够长的过期时间（1 小时），避免测试期间令牌过期导致校验失败
    token = create_access_token(
        subject,
        expires_delta=timedelta(hours=1),
        extra_claims=extra_claims,
    )

    # 令牌应为非空字符串
    assert isinstance(token, str) and token

    payload = decode_access_token(token)

    # 令牌有效：校验通过返回载荷字典（非 None）
    assert payload is not None
    # 还原同一用户身份：实现将 sub 统一序列化为字符串
    assert payload["sub"] == str(subject)
    # 额外声明应原样保留在载荷中
    for key, value in extra_claims.items():
        assert payload[key] == value

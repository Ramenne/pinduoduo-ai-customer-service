# -*- coding: utf-8 -*-
"""
test_anti_content —— anti-content 签名缺失/失效检测单元与属性测试
================================================================
本文件用途：验证 channel_pdd.core.anti_content 的签名检测逻辑（需求 26.1/26.2）：
- extract_anti_content / has_valid_anti_content：从 Cookie 提取并判定签名有效性，
  兼容 anti_content 与 anti-content 两种命名，空值/缺失/非字符串均视为无效。
- is_signature_invalid_response：从响应体识别签名校验失败 / 风控拦截。

含一个属性测试（Hypothesis，最少 100 次迭代）：
**Property: anti-content 有效性判定与 Cookie 是否携带非空签名一致**
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from channel_pdd.core.anti_content import (
    AntiContentMissingError,
    SIGNATURE_INVALID_ERROR_CODES,
    SIGNATURE_MISSING_MESSAGE,
    extract_anti_content,
    has_valid_anti_content,
    is_signature_invalid_response,
)


# ----------------------------------------------------------------------
# 单元测试：extract_anti_content / has_valid_anti_content
# ----------------------------------------------------------------------
def test_extract_anti_content_underscore_key():
    """anti_content（下划线）键应被正确提取。"""
    assert extract_anti_content({"anti_content": "sig123"}) == "sig123"


def test_extract_anti_content_dash_key():
    """anti-content（连字符）键应被正确提取。"""
    assert extract_anti_content({"anti-content": "sigABC"}) == "sigABC"


def test_extract_anti_content_strips_whitespace():
    """提取的签名应去除首尾空白。"""
    assert extract_anti_content({"anti_content": "  sig  "}) == "sig"


def test_extract_anti_content_missing_returns_none():
    """缺失签名键时返回 None。"""
    assert extract_anti_content({"foo": "bar"}) is None


def test_extract_anti_content_empty_value_returns_none():
    """空字符串签名视为无效，返回 None。"""
    assert extract_anti_content({"anti_content": "   "}) is None


def test_extract_anti_content_non_mapping_returns_none():
    """非映射输入（None / 列表）返回 None。"""
    assert extract_anti_content(None) is None
    assert extract_anti_content(["anti_content"]) is None


def test_has_valid_anti_content():
    """has_valid_anti_content 与是否携带非空签名一致。"""
    assert has_valid_anti_content({"anti_content": "x"}) is True
    assert has_valid_anti_content({}) is False
    assert has_valid_anti_content(None) is False


# ----------------------------------------------------------------------
# 单元测试：is_signature_invalid_response
# ----------------------------------------------------------------------
def test_signature_invalid_by_error_code():
    """命中签名失效错误码应判定为签名失效。"""
    a_code = next(iter(SIGNATURE_INVALID_ERROR_CODES))
    assert is_signature_invalid_response({"error_code": a_code}) is True


def test_signature_invalid_by_camel_case_code():
    """兼容驼峰 errorCode 命名。"""
    a_code = next(iter(SIGNATURE_INVALID_ERROR_CODES))
    assert is_signature_invalid_response({"errorCode": a_code}) is True


def test_signature_invalid_by_keyword():
    """错误信息含签名/风控关键字应判定为签名失效。"""
    assert is_signature_invalid_response({"error_msg": "anti-content 校验失败"}) is True
    assert is_signature_invalid_response({"message": "触发风控拦截"}) is True


def test_signature_valid_normal_response():
    """正常成功响应不应判定为签名失效。"""
    assert is_signature_invalid_response({"success": True, "result": {}}) is False


def test_signature_invalid_non_mapping():
    """非映射输入不判定为签名失效。"""
    assert is_signature_invalid_response(None) is False


def test_anti_content_missing_error_default_message():
    """领域异常默认携带统一中文提示。"""
    err = AntiContentMissingError()
    assert err.message == SIGNATURE_MISSING_MESSAGE
    assert SIGNATURE_MISSING_MESSAGE in str(err)


# ----------------------------------------------------------------------
# 属性测试（Hypothesis）
# Feature: pdd-auto-reply, Property: anti-content 有效性判定与携带非空签名一致
# Validates: Requirements 26.2
# ----------------------------------------------------------------------
@settings(max_examples=200)
@given(
    sig=st.one_of(st.none(), st.text()),
    use_dash=st.booleans(),
    noise=st.dictionaries(
        st.text(min_size=1).filter(lambda s: s not in ("anti_content", "anti-content")),
        st.text(),
        max_size=3,
    ),
)
def test_property_anti_content_validity(sig, use_dash, noise):
    """has_valid_anti_content 为真当且仅当 Cookie 携带「非空白字符串」签名。

    构造任意 Cookie 字典（含随机噪声键），注入 anti_content / anti-content 键为
    随机文本或 None，断言有效性判定与「签名去空白后非空」严格一致。
    """
    cookies = dict(noise)
    key = "anti-content" if use_dash else "anti_content"
    if sig is not None:
        cookies[key] = sig

    expected_valid = isinstance(sig, str) and sig.strip() != ""
    assert has_valid_anti_content(cookies) is expected_valid
    if expected_valid:
        assert extract_anti_content(cookies) == sig.strip()
    else:
        # 噪声键不含签名键，故无签名时必为 None
        assert extract_anti_content(cookies) is None

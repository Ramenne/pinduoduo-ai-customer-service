# -*- coding: utf-8 -*-
"""
common.tests.test_response_property —— 统一响应体构造属性测试
==============================================================
本文件用途：以 Hypothesis 属性测试验证 `common.schemas.common` 中统一响应体
构造逻辑的正确性（设计 Property 21，验证 Requirements 24.1、24.2）。

测试目标（Property 21：统一响应体构造正确）：
- 对任意业务结果输入 (code, message, data)，构造的响应体必含
  code/success/message/data 四个字段；
- success 为真当且仅当 code 表示成功语义（code == 0）；
- 失败时 success 为假、data 为 None 且 message 为非空中文信息。
"""
from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from common.schemas.common import (
    DEFAULT_ERROR_MESSAGE,
    build_response,
    is_success_code,
)

# 业务码生成器：覆盖成功语义码 0 与大量非 0 失败码，确保两类分支均被探索。
code_strategy = st.one_of(
    st.just(0),
    st.integers(min_value=-10000, max_value=10000),
)

# 消息生成器：覆盖空串、纯空白、ASCII 与中文文本，验证失败时的非空回退逻辑。
message_strategy = st.one_of(
    st.just(""),
    st.text(),
    st.text(alphabet="操作失败网络异常参数错误权限不足系统繁忙", min_size=1, max_size=20),
)

# 业务数据生成器：覆盖 None 与多种常见结构，验证失败时 data 被强制置空。
data_strategy = st.one_of(
    st.none(),
    st.integers(),
    st.text(),
    st.dictionaries(st.text(max_size=5), st.integers(), max_size=3),
    st.lists(st.integers(), max_size=5),
)


@settings(max_examples=200)
@given(code=code_strategy, message=message_strategy, data=data_strategy)
def test_build_response_unified_structure(code: int, message: str, data: Any) -> None:
    """Feature: pdd-auto-reply, Property 21: 统一响应体构造正确

    对任意 (code, message, data)，验证统一响应体的结构与语义不变式。
    """
    resp = build_response(code=code, message=message, data=data)
    payload = resp.model_dump()

    # 1) 四个字段恒在。
    assert set(payload.keys()) == {"code", "success", "message", "data"}

    # 2) success 为真当且仅当 code 表示成功语义（code == 0）。
    assert resp.success == is_success_code(code)
    assert resp.success == (code == 0)

    # 3) code 字段原样保留。
    assert resp.code == code

    if resp.success:
        # 成功：data 原样保留，message 为非空（空串回退默认成功文案）。
        assert resp.data == data
        assert resp.message != ""
    else:
        # 4) 失败：success 为假、data 强制为 None、message 为非空中文信息。
        assert resp.data is None
        assert resp.message != ""
        # 调用方未提供 message 时回退默认中文失败文案。
        assert resp.message == (message if message else DEFAULT_ERROR_MESSAGE)

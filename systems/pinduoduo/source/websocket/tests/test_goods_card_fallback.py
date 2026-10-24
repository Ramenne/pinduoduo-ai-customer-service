# -*- coding: utf-8 -*-
"""
test_goods_card_fallback —— 商品卡片签名缺失降级为文本回复单元测试
================================================================
本文件用途：验证 agent.goods_card_fallback 的降级逻辑（需求 26.3/26.4/26.5/26.6）：
- 签名可用时正常发送商品卡片（不降级）；
- 签名缺失 / 失效时降级为文本回复商品信息，携带中文降级原因，且不中断会话；
- 商品文本格式化覆盖完整字段与缺字段兜底。
"""
from __future__ import annotations

from agent.goods_card_fallback import (
    REPLY_GOODS_CARD,
    REPLY_TEXT,
    GoodsCardFallbackResult,
    build_goods_text,
    is_signature_available,
    resolve_goods_card_reply,
)
from channel_pdd.core.anti_content import SIGNATURE_MISSING_MESSAGE

# 代表性商品上下文（含完整字段）。
_GOODS = {
    "goods_id": "G123",
    "goods_name": "测试商品",
    "goods_price": "9.9",
    "goods_spec": "红色/L",
    "link_url": "https://example.com/g/123",
}

# 携带有效签名的 Cookie。
_COOKIES_OK = {"anti_content": "valid-sig-xyz", "PASS_ID": "p"}
# 缺少签名的 Cookie。
_COOKIES_MISSING = {"PASS_ID": "p"}


# ----------------------------------------------------------------------
# build_goods_text
# ----------------------------------------------------------------------
def test_build_goods_text_full_fields():
    """完整字段应拼接名称/价格/规格/编号/链接。"""
    text = build_goods_text(_GOODS)
    assert "测试商品" in text
    assert "9.9" in text
    assert "红色/L" in text
    assert "G123" in text
    assert "https://example.com/g/123" in text


def test_build_goods_text_partial_fields():
    """仅部分字段时只拼接存在的字段。"""
    text = build_goods_text({"goods_name": "仅名称"})
    assert "仅名称" in text
    assert "商品价格" not in text


def test_build_goods_text_empty_fallback():
    """无任何字段时返回通用兜底文本，保证非空。"""
    assert build_goods_text({}) == "为您推荐相关商品，详情请咨询客服。"
    assert build_goods_text(None) == "为您推荐相关商品，详情请咨询客服。"


# ----------------------------------------------------------------------
# is_signature_available
# ----------------------------------------------------------------------
def test_signature_available_true():
    """携带有效签名且响应未识别为失败时签名可用。"""
    assert is_signature_available(cookies=_COOKIES_OK) is True
    assert is_signature_available(cookies=_COOKIES_OK, response_data={"success": True}) is True


def test_signature_available_false_missing_cookie():
    """缺少签名 Cookie 时不可用。"""
    assert is_signature_available(cookies=_COOKIES_MISSING) is False
    assert is_signature_available(cookies=None) is False


def test_signature_available_false_invalid_response():
    """有签名但响应被识别为签名校验失败时也不可用。"""
    resp = {"error_msg": "anti-content 校验失败"}
    assert is_signature_available(cookies=_COOKIES_OK, response_data=resp) is False


# ----------------------------------------------------------------------
# resolve_goods_card_reply —— 正常发卡
# ----------------------------------------------------------------------
def test_resolve_normal_send_card():
    """签名可用时返回商品卡片决策，不降级、不中断。"""
    result = resolve_goods_card_reply(_GOODS, cookies=_COOKIES_OK)
    assert isinstance(result, GoodsCardFallbackResult)
    assert result.degraded is False
    assert result.reply_type == REPLY_GOODS_CARD
    assert result.goods_id == "G123"
    assert result.text_content is None
    assert result.degrade_reason is None
    assert result.interrupted is False


# ----------------------------------------------------------------------
# resolve_goods_card_reply —— 降级为文本
# ----------------------------------------------------------------------
def test_resolve_degrade_on_missing_signature():
    """签名缺失时降级为文本回复，携带中文降级原因，不中断会话。"""
    result = resolve_goods_card_reply(_GOODS, cookies=_COOKIES_MISSING)
    assert result.degraded is True
    assert result.reply_type == REPLY_TEXT
    assert result.goods_id == "G123"
    assert result.text_content is not None and "测试商品" in result.text_content
    assert result.degrade_reason is not None
    assert SIGNATURE_MISSING_MESSAGE in result.degrade_reason
    assert "降级为文本" in result.degrade_reason
    assert result.interrupted is False


def test_resolve_degrade_on_invalid_response():
    """接口返回签名校验失败时同样降级为文本。"""
    resp = {"error_code": 54001}
    result = resolve_goods_card_reply(_GOODS, cookies=_COOKIES_OK, response_data=resp)
    assert result.degraded is True
    assert result.reply_type == REPLY_TEXT
    assert result.interrupted is False


def test_resolve_degrade_custom_cause():
    """自定义降级原因前缀应体现在中文降级原因中。"""
    result = resolve_goods_card_reply(
        _GOODS, cookies=_COOKIES_MISSING, cause_message="商品卡片接口暂不可用"
    )
    assert result.degraded is True
    assert "商品卡片接口暂不可用" in result.degrade_reason
    assert "降级为文本" in result.degrade_reason


def test_resolve_degrade_empty_goods_still_text():
    """无商品字段时降级文本回退为兜底文本，仍不中断。"""
    result = resolve_goods_card_reply({}, cookies=_COOKIES_MISSING)
    assert result.degraded is True
    assert result.reply_type == REPLY_TEXT
    assert result.text_content == "为您推荐相关商品，详情请咨询客服。"
    assert result.interrupted is False

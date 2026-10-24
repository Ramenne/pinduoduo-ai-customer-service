# -*- coding: utf-8 -*-
"""
websocket.tests.test_keyword_matcher —— 关键词规则匹配单元测试
==============================================================
本文件用途：验证 websocket.engine.keyword_matcher 纯逻辑，覆盖需求 6 运行时
匹配核心场景：

- 匹配方式（需求 6.2/6.3）：full 全匹配 / contains 包含 / regex 正则的命中与不命中；
- 优先级唯一命中（需求 6.4）：命中多条仅返回 priority 最高一条；并列取靠前者；
- 回复类型（需求 6.5）：图片回复类型原样返回 reply_type=image 与内容；
- 停用规则不参与匹配（需求 6.7）；
- 非法正则不命中（不抛异常）；空文本/空规则集合返回 None；
- 模型对象与 dict 两种入参形态均可用。

测试框架：pytest。
"""
import pytest

from engine.keyword_matcher import (
    MATCH_CONTAINS,
    MATCH_FULL,
    MATCH_REGEX,
    REPLY_IMAGE,
    REPLY_TEXT,
    KeywordHit,
    match_keyword,
)


def _rule(
    keyword,
    match_type,
    reply_content="回复内容",
    reply_type=REPLY_TEXT,
    priority=0,
    enabled=True,
    rid=None,
):
    """构造关键词规则 dict 入参。"""
    return {
        "id": rid,
        "keyword": keyword,
        "match_type": match_type,
        "reply_type": reply_type,
        "reply_content": reply_content,
        "priority": priority,
        "enabled": enabled,
    }


# ----------------------------------------------------------------------
# 匹配方式（需求 6.2 / 6.3）
# ----------------------------------------------------------------------
def test_full_hit():
    """full：消息与关键词完全相等（去空白）时命中。"""
    rules = [_rule("你好", MATCH_FULL, rid=1)]
    hit = match_keyword("  你好  ", rules)
    assert hit is not None
    assert hit.rule_id == 1
    assert hit.match_type == MATCH_FULL


def test_full_miss():
    """full：仅包含关键词但不完全相等时不命中。"""
    rules = [_rule("你好", MATCH_FULL)]
    assert match_keyword("你好呀", rules) is None


def test_contains_hit():
    """contains：关键词为消息子串时命中。"""
    rules = [_rule("发货", MATCH_CONTAINS, rid=2)]
    hit = match_keyword("什么时候发货呢", rules)
    assert hit is not None
    assert hit.rule_id == 2


def test_contains_empty_keyword_miss():
    """contains：空关键词不命中，避免误全量命中。"""
    rules = [_rule("", MATCH_CONTAINS)]
    assert match_keyword("任意消息", rules) is None


def test_regex_hit():
    """regex：关键词作为正则搜索命中。"""
    rules = [_rule(r"\d{4}", MATCH_REGEX, rid=3)]
    hit = match_keyword("订单号 1234 请查询", rules)
    assert hit is not None
    assert hit.rule_id == 3


def test_invalid_regex_miss_no_raise():
    """regex：非法正则视为不命中且不抛异常。"""
    rules = [_rule("[未闭合", MATCH_REGEX)]
    assert match_keyword("任意消息", rules) is None


# ----------------------------------------------------------------------
# 优先级唯一命中（需求 6.4）
# ----------------------------------------------------------------------
def test_priority_highest_wins():
    """命中多条时仅返回 priority 最高的一条。"""
    rules = [
        _rule("发货", MATCH_CONTAINS, reply_content="低", priority=1, rid=1),
        _rule("发货", MATCH_CONTAINS, reply_content="高", priority=10, rid=2),
        _rule("发货", MATCH_CONTAINS, reply_content="中", priority=5, rid=3),
    ]
    hit = match_keyword("发货了吗", rules)
    assert hit is not None
    assert hit.rule_id == 2
    assert hit.reply_content == "高"
    assert hit.priority == 10


def test_priority_tie_takes_first():
    """priority 并列最高时取入参顺序靠前者，保证确定性。"""
    rules = [
        _rule("退款", MATCH_CONTAINS, reply_content="第一", priority=5, rid=1),
        _rule("退款", MATCH_CONTAINS, reply_content="第二", priority=5, rid=2),
    ]
    hit = match_keyword("我要退款", rules)
    assert hit is not None
    assert hit.rule_id == 1
    assert hit.reply_content == "第一"


# ----------------------------------------------------------------------
# 回复类型（需求 6.5）
# ----------------------------------------------------------------------
def test_image_reply_type_returned():
    """图片回复类型原样返回 reply_type=image 与图片地址。"""
    rules = [
        _rule(
            "图片",
            MATCH_CONTAINS,
            reply_content="https://img.example.com/a.png",
            reply_type=REPLY_IMAGE,
            rid=9,
        )
    ]
    hit = match_keyword("发个图片看看", rules)
    assert hit is not None
    assert hit.reply_type == REPLY_IMAGE
    assert hit.reply_content == "https://img.example.com/a.png"


# ----------------------------------------------------------------------
# 停用规则不参与匹配（需求 6.7）
# ----------------------------------------------------------------------
def test_disabled_rule_excluded():
    """停用规则不参与匹配；停用最高优先级时回退到启用的次优规则。"""
    rules = [
        _rule("发货", MATCH_CONTAINS, reply_content="停用", priority=10, enabled=False, rid=1),
        _rule("发货", MATCH_CONTAINS, reply_content="启用", priority=1, enabled=True, rid=2),
    ]
    hit = match_keyword("发货了吗", rules)
    assert hit is not None
    assert hit.rule_id == 2
    assert hit.reply_content == "启用"


def test_all_disabled_returns_none():
    """全部规则停用时无命中。"""
    rules = [_rule("发货", MATCH_CONTAINS, enabled=False)]
    assert match_keyword("发货了吗", rules) is None


# ----------------------------------------------------------------------
# 边界场景
# ----------------------------------------------------------------------
def test_empty_text_and_rules():
    """空文本或空规则集合返回 None。"""
    assert match_keyword(None, None) is None
    assert match_keyword("", []) is None
    assert match_keyword(None, [_rule("发货", MATCH_CONTAINS)]) is None


def test_model_like_object_input():
    """支持属性访问的模型对象入参（非 dict）。"""

    class _Obj:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    rule = _Obj(
        id=7,
        keyword="你好",
        match_type=MATCH_FULL,
        reply_type=REPLY_TEXT,
        reply_content="您好",
        priority=3,
        enabled=True,
    )
    hit = match_keyword("你好", [rule])
    assert isinstance(hit, KeywordHit)
    assert hit.rule_id == 7
    assert hit.reply_content == "您好"

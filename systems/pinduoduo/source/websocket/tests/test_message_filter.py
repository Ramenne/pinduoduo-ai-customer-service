# -*- coding: utf-8 -*-
"""
websocket.tests.test_message_filter —— 消息过滤与黑名单判断单元测试
==================================================================
本文件用途：验证 websocket.engine.message_filter 纯逻辑，覆盖需求 12 运行时
判定核心场景：

- 过滤命中（需求 12.2）：contains / regex / msg_type 三类条件命中与不命中；
- 停用规则不参与判断；非法正则不命中（不抛异常）；
- 黑名单判断（需求 12.4）：有效黑名单命中、空标识不命中；
- 移出失效（需求 12.5）：is_active=False 的记录不参与黑名单判断；
- 模型对象与 dict 两种入参形态均可用。

测试框架：pytest。
"""
import pytest

from engine.message_filter import (
    CONDITION_CONTAINS,
    CONDITION_MSG_TYPE,
    CONDITION_REGEX,
    is_blacklisted,
    is_filtered,
    match_filter_rules,
)


def _rule(condition_type, condition_value, enabled=True, rid=None):
    """构造过滤规则 dict 入参。"""
    return {
        "id": rid,
        "condition_type": condition_type,
        "condition_value": condition_value,
        "enabled": enabled,
    }


def _bl(customer_uid, is_active=True):
    """构造黑名单记录 dict 入参。"""
    return {"customer_uid": customer_uid, "is_active": is_active}


# ----------------------------------------------------------------------
# 过滤规则命中（需求 12.2）
# ----------------------------------------------------------------------
def test_contains_hit():
    """contains：条件值为消息子串时命中。"""
    rules = [_rule(CONDITION_CONTAINS, "广告", rid=1)]
    hit = match_filter_rules("这是一条广告消息", rules)
    assert hit is not None
    assert hit.condition_type == CONDITION_CONTAINS
    assert hit.rule_id == 1


def test_contains_miss():
    """contains：条件值非子串时不命中。"""
    rules = [_rule(CONDITION_CONTAINS, "广告")]
    assert is_filtered("正常咨询消息", rules) is False


def test_contains_empty_value_not_hit():
    """contains：空条件值不命中（避免空串恒为子串导致误拦截）。"""
    rules = [_rule(CONDITION_CONTAINS, "")]
    assert is_filtered("任意内容", rules) is False


def test_regex_hit():
    """regex：正则搜索到即命中。"""
    rules = [_rule(CONDITION_REGEX, r"^spam\d+")]
    assert is_filtered("spam123 你好", rules) is True


def test_regex_invalid_not_raise():
    """regex：非法正则视为不命中且不抛异常。"""
    rules = [_rule(CONDITION_REGEX, "[未闭合")]
    assert is_filtered("任意内容", rules) is False


def test_msg_type_hit_and_miss():
    """msg_type：消息类型等于条件值时命中。"""
    rules = [_rule(CONDITION_MSG_TYPE, "image")]
    assert is_filtered("", rules, msg_type="image") is True
    assert is_filtered("", rules, msg_type="text") is False


def test_disabled_rule_skipped():
    """停用规则不参与命中判断。"""
    rules = [_rule(CONDITION_CONTAINS, "广告", enabled=False)]
    assert is_filtered("这是广告", rules) is False


def test_unknown_condition_type_not_hit():
    """未知条件类型一律不命中（向后兼容）。"""
    rules = [_rule("unknown", "x")]
    assert is_filtered("xxx", rules) is False


def test_first_hit_returned_in_order():
    """多条规则命中时按入参顺序返回首个命中。"""
    rules = [
        _rule(CONDITION_CONTAINS, "甲", rid=1),
        _rule(CONDITION_CONTAINS, "乙", rid=2),
    ]
    hit = match_filter_rules("甲乙都在", rules)
    assert hit.rule_id == 1


def test_empty_rules_no_hit():
    """空规则集合不命中。"""
    assert match_filter_rules("内容", None) is None
    assert match_filter_rules("内容", []) is None


# ----------------------------------------------------------------------
# 黑名单判断（需求 12.4 / 12.5）
# ----------------------------------------------------------------------
def test_blacklist_active_hit():
    """有效黑名单记录命中（需求 12.4）。"""
    bl = [_bl("uid_1", is_active=True)]
    assert is_blacklisted("uid_1", bl) is True


def test_blacklist_inactive_skipped():
    """移出失效（is_active=False）记录不命中（需求 12.5）。"""
    bl = [_bl("uid_1", is_active=False)]
    assert is_blacklisted("uid_1", bl) is False


def test_blacklist_empty_uid():
    """空客户标识一律不在黑名单。"""
    bl = [_bl("uid_1", is_active=True)]
    assert is_blacklisted("", bl) is False
    assert is_blacklisted(None, bl) is False


def test_blacklist_not_present():
    """不在黑名单集合中的客户标识不命中。"""
    bl = [_bl("uid_1", is_active=True)]
    assert is_blacklisted("uid_2", bl) is False


def test_blacklist_mixed_active_inactive():
    """同一客户存在失效记录但另有有效记录时命中。"""
    bl = [_bl("uid_1", is_active=False), _bl("uid_1", is_active=True)]
    assert is_blacklisted("uid_1", bl) is True


# ----------------------------------------------------------------------
# 模型对象入参兼容
# ----------------------------------------------------------------------
class _Obj:
    """模拟 SQLAlchemy 模型对象（属性访问）。"""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_object_style_input_supported():
    """模型对象（属性访问）入参与 dict 入参等价。"""
    rules = [_Obj(id=9, condition_type=CONDITION_CONTAINS, condition_value="违禁", enabled=True)]
    hit = match_filter_rules("含违禁词", rules)
    assert hit is not None and hit.rule_id == 9

    bl = [_Obj(customer_uid="uid_x", is_active=True)]
    assert is_blacklisted("uid_x", bl) is True

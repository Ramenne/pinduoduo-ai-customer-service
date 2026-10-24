# -*- coding: utf-8 -*-
"""
websocket.tests.test_reply_engine —— 自动回复决策链编排单元测试
==============================================================
本文件用途：验证 websocket.engine.reply_engine.decide_reply 的固定优先级决策逻辑，
覆盖 Property 13（自动回复决策优先级链）涉及的需求：

固定优先级：黑名单 → 过滤 → 非营业时间 → 风控 → 关键词 → 商品专属 → AI → 默认回复 → 无匹配规则。

覆盖场景：
- 黑名单短路（需求 12.4）优先于过滤 / 营业时间 / 风控 / 关键词；
- 过滤命中（需求 12.2）优先于营业时间 / 风控 / 关键词；
- 非营业时间（需求 11.3）优先于风控 / 关键词；
- 风控达上限（需求 13.2）优先于关键词；
- 关键词命中（需求 6.3）优先于商品专属 / AI / 默认回复；
- 商品专属（需求 7.4）优先于 AI / 默认回复；
- AI 启用（需求 8.1）优先于默认回复，且交由 AI 生成（should_reply=False）；
- 默认回复（需求 7.1）兜底；
- 无任何回复可用（需求 7.2）→ no_match，不发送。

测试框架：pytest。
"""
from datetime import datetime, timedelta

from engine.keyword_matcher import MATCH_CONTAINS, REPLY_IMAGE, REPLY_TEXT
from engine.message_filter import CONDITION_CONTAINS
from engine.reply_engine import (
    ACTION_AI,
    ACTION_BLACKLISTED,
    ACTION_DEFAULT,
    ACTION_FILTERED,
    ACTION_GOODS_SPECIFIC,
    ACTION_KEYWORD,
    ACTION_NO_MATCH,
    ACTION_OFF_HOURS,
    ACTION_RISK_BLOCKED,
    RESULT_AI_REPLY,
    RESULT_AUTO_REPLY,
    RESULT_BLACKLISTED,
    RESULT_FILTERED,
    RESULT_NON_BUSINESS_HOURS,
    RESULT_NO_MATCH,
    RESULT_RISK_PAUSED,
    ReplyRules,
    ShopConfig,
    decide_reply,
)

# 固定参考时刻（北京时间口径，朴素 datetime），便于营业时间 / 风控判定可控。
NOW = datetime(2024, 1, 1, 10, 0, 0)  # 10:00


# ----------------------------------------------------------------------
# 构造辅助
# ----------------------------------------------------------------------
def _ctx(text="你好", *, from_uid="cust_1", goods_id=None, msg_type="text"):
    """构造文本消息上下文 dict（兼容 decide_reply 的取值口径）。"""
    goods_context = {"goods_id": goods_id} if goods_id is not None else None
    return {
        "type": msg_type,
        "content": text,
        "goods_context": goods_context,
        "order_context": None,
        "kwargs": {"from_uid": from_uid},
    }


def _kw_rule(keyword="你好", reply="关键词回复", priority=1, rid=1):
    """构造命中文本的关键词规则 dict。"""
    return {
        "id": rid,
        "keyword": keyword,
        "match_type": MATCH_CONTAINS,
        "reply_type": REPLY_TEXT,
        "reply_content": reply,
        "priority": priority,
        "enabled": True,
    }


def _filter_rule(value="你好", rid=10):
    """构造命中文本的过滤规则 dict。"""
    return {"id": rid, "condition_type": CONDITION_CONTAINS, "condition_value": value, "enabled": True}


def _blacklist(uid="cust_1"):
    """构造有效黑名单记录 dict。"""
    return [{"customer_uid": uid, "is_active": True}]


def _goods_reply(goods_id="g1", reply="商品专属回复", rid=20):
    """构造商品专属回复 dict。"""
    return {"id": rid, "goods_id": goods_id, "reply_type": REPLY_TEXT, "reply_content": reply, "enabled": True}


# ----------------------------------------------------------------------
# 优先级：黑名单最高
# ----------------------------------------------------------------------
def test_blacklist_wins_over_everything():
    """黑名单命中时短路，优先于过滤 / 营业时间 / 风控 / 关键词。"""
    rules = ReplyRules(
        keyword_rules=[_kw_rule()],
        filter_rules=[_filter_rule()],
        blacklist=_blacklist("cust_1"),
    )
    cfg = ShopConfig(shop_pk=1)
    decision = decide_reply(_ctx(from_uid="cust_1"), cfg, rules, now=NOW)
    assert decision.action == ACTION_BLACKLISTED
    assert decision.log_result == RESULT_BLACKLISTED
    assert decision.should_reply is False
    assert decision.content is None


# ----------------------------------------------------------------------
# 优先级：过滤 > 营业时间 / 风控 / 关键词
# ----------------------------------------------------------------------
def test_filter_wins_over_keyword():
    """过滤命中时短路，优先于关键词。"""
    rules = ReplyRules(keyword_rules=[_kw_rule()], filter_rules=[_filter_rule("你好")])
    decision = decide_reply(_ctx("你好"), ShopConfig(shop_pk=1), rules, now=NOW)
    assert decision.action == ACTION_FILTERED
    assert decision.log_result == RESULT_FILTERED
    assert decision.matched_rule_id == 10
    assert decision.should_reply is False


# ----------------------------------------------------------------------
# 优先级：非营业时间 > 风控 / 关键词
# ----------------------------------------------------------------------
def test_off_hours_wins_over_keyword():
    """非营业时间短路，优先于关键词。营业 08:00~09:00，10:00 不在区间。"""
    rules = ReplyRules(keyword_rules=[_kw_rule()])
    cfg = ShopConfig(shop_pk=1, business_enabled=True, business_start="08:00", business_end="09:00")
    decision = decide_reply(_ctx("你好"), cfg, rules, now=NOW)
    assert decision.action == ACTION_OFF_HOURS
    assert decision.log_result == RESULT_NON_BUSINESS_HOURS
    assert decision.should_reply is False


def test_within_business_hours_continues():
    """营业时间内（08:00~12:00 含 10:00）继续向下判定，命中关键词。"""
    rules = ReplyRules(keyword_rules=[_kw_rule()])
    cfg = ShopConfig(shop_pk=1, business_enabled=True, business_start="08:00", business_end="12:00")
    decision = decide_reply(_ctx("你好"), cfg, rules, now=NOW)
    assert decision.action == ACTION_KEYWORD


# ----------------------------------------------------------------------
# 优先级：风控 > 关键词
# ----------------------------------------------------------------------
def test_risk_blocked_wins_over_keyword():
    """会话窗口内回复已达上限时风控暂停，优先于关键词，并生成风控日志。"""
    # 单会话上限 2，窗口 60s，窗口内已有 2 条回复 → 达上限
    recent = [NOW - timedelta(seconds=10), NOW - timedelta(seconds=20)]
    cfg = ShopConfig(
        shop_pk=7,
        risk_enabled=True,
        session_reply_limit=2,
        window_seconds=60,
        session_reply_times=recent,
    )
    rules = ReplyRules(keyword_rules=[_kw_rule()])
    decision = decide_reply(_ctx("你好"), cfg, rules, now=NOW)
    assert decision.action == ACTION_RISK_BLOCKED
    assert decision.log_result == RESULT_RISK_PAUSED
    assert decision.risk_log is not None
    assert decision.risk_log.shop_pk == 7
    assert decision.should_reply is False


def test_risk_not_exceeded_continues():
    """风控未达上限时继续向下判定，命中关键词。"""
    cfg = ShopConfig(
        shop_pk=7,
        risk_enabled=True,
        session_reply_limit=5,
        window_seconds=60,
        session_reply_times=[NOW - timedelta(seconds=10)],
    )
    rules = ReplyRules(keyword_rules=[_kw_rule()])
    decision = decide_reply(_ctx("你好"), cfg, rules, now=NOW)
    assert decision.action == ACTION_KEYWORD


# ----------------------------------------------------------------------
# 优先级：关键词 > 商品专属 / AI / 默认
# ----------------------------------------------------------------------
def test_keyword_wins_over_goods_ai_default():
    """关键词命中时优先于商品专属、AI 与默认回复。"""
    rules = ReplyRules(
        keyword_rules=[_kw_rule(reply="关键词内容")],
        goods_replies=[_goods_reply("g1")],
    )
    cfg = ShopConfig(shop_pk=1, ai_enabled=True, default_reply_content="默认")
    decision = decide_reply(_ctx("你好", goods_id="g1"), cfg, rules, now=NOW)
    assert decision.action == ACTION_KEYWORD
    assert decision.content == "关键词内容"
    assert decision.reply_type == REPLY_TEXT
    assert decision.log_result == RESULT_AUTO_REPLY
    assert decision.should_reply is True


def test_keyword_image_reply_type():
    """关键词图片回复类型原样返回。"""
    rule = _kw_rule(reply="https://img/a.png")
    rule["reply_type"] = REPLY_IMAGE
    rules = ReplyRules(keyword_rules=[rule])
    decision = decide_reply(_ctx("你好"), ShopConfig(shop_pk=1), rules, now=NOW)
    assert decision.action == ACTION_KEYWORD
    assert decision.reply_type == REPLY_IMAGE
    assert decision.content == "https://img/a.png"


# ----------------------------------------------------------------------
# 优先级：商品专属 > AI / 默认
# ----------------------------------------------------------------------
def test_goods_specific_wins_over_ai_default():
    """商品专属回复命中时优先于 AI 与默认回复（需求 7.4）。"""
    rules = ReplyRules(goods_replies=[_goods_reply("g1", reply="专属内容", rid=20)])
    cfg = ShopConfig(shop_pk=1, ai_enabled=True, default_reply_content="默认")
    # 文本不命中关键词（无关键词规则），但商品上下文携带 g1
    decision = decide_reply(_ctx("随便问问", goods_id="g1"), cfg, rules, now=NOW)
    assert decision.action == ACTION_GOODS_SPECIFIC
    assert decision.content == "专属内容"
    assert decision.matched_rule_id == 20
    assert decision.should_reply is True


def test_goods_specific_miss_when_goods_id_differs():
    """商品专属回复 goods_id 不匹配时不命中，回退到 AI / 默认。"""
    rules = ReplyRules(goods_replies=[_goods_reply("g1")])
    cfg = ShopConfig(shop_pk=1, ai_enabled=False, default_reply_content="默认")
    decision = decide_reply(_ctx("随便", goods_id="g2"), cfg, rules, now=NOW)
    assert decision.action == ACTION_DEFAULT


# ----------------------------------------------------------------------
# 优先级：AI > 默认
# ----------------------------------------------------------------------
def test_ai_wins_over_default_and_not_should_reply():
    """AI 启用且前述未命中时决策 ai，交由 AI 生成（should_reply=False）。"""
    cfg = ShopConfig(shop_pk=1, ai_enabled=True, default_reply_content="默认")
    decision = decide_reply(_ctx("随便问问"), cfg, ReplyRules(), now=NOW)
    assert decision.action == ACTION_AI
    assert decision.log_result == RESULT_AI_REPLY
    assert decision.should_reply is False
    assert decision.content is None


# ----------------------------------------------------------------------
# 默认回复兜底（需求 7.1）
# ----------------------------------------------------------------------
def test_default_reply_fallback():
    """前述均未命中且 AI 未启用、配置了默认回复 → 返回默认回复。"""
    cfg = ShopConfig(shop_pk=1, ai_enabled=False, default_reply_content="默认回复内容")
    decision = decide_reply(_ctx("随便问问"), cfg, ReplyRules(), now=NOW)
    assert decision.action == ACTION_DEFAULT
    assert decision.content == "默认回复内容"
    assert decision.reply_type == REPLY_TEXT
    assert decision.log_result == RESULT_AUTO_REPLY
    assert decision.should_reply is True


# ----------------------------------------------------------------------
# 无匹配规则（需求 7.2）
# ----------------------------------------------------------------------
def test_no_match_when_nothing_applies():
    """无任何回复可用（无关键词 / 商品专属 / AI / 默认）→ no_match 且不发送。"""
    cfg = ShopConfig(shop_pk=1, ai_enabled=False, default_reply_content=None)
    decision = decide_reply(_ctx("随便问问"), cfg, ReplyRules(), now=NOW)
    assert decision.action == ACTION_NO_MATCH
    assert decision.log_result == RESULT_NO_MATCH
    assert decision.should_reply is False
    assert decision.content is None


def test_empty_default_treated_as_unconfigured():
    """默认回复为空串视为未配置 → no_match（需求 7.2）。"""
    cfg = ShopConfig(shop_pk=1, ai_enabled=False, default_reply_content="")
    decision = decide_reply(_ctx("随便问问"), cfg, ReplyRules(), now=NOW)
    assert decision.action == ACTION_NO_MATCH


# ----------------------------------------------------------------------
# 默认全天营业（未配置营业时间）继续判定
# ----------------------------------------------------------------------
def test_business_hours_unconfigured_defaults_open():
    """营业时间未配置时默认全天营业，继续向下命中关键词（需求 11.4）。"""
    rules = ReplyRules(keyword_rules=[_kw_rule()])
    cfg = ShopConfig(shop_pk=1, business_enabled=True, business_start=None, business_end=None)
    decision = decide_reply(_ctx("你好"), cfg, rules, now=NOW)
    assert decision.action == ACTION_KEYWORD


# ----------------------------------------------------------------------
# 默认回复「只回复一次」（需求 7.1）
# ----------------------------------------------------------------------
def test_default_reply_once_skips_when_already_sent():
    """开启只回复一次且该客户已收到过默认回复 → 不再发送，记 no_match。"""
    cfg = ShopConfig(
        shop_pk=1,
        ai_enabled=False,
        default_reply_content="默认回复内容",
        default_reply_once=True,
        default_reply_already_sent=True,
    )
    decision = decide_reply(_ctx("随便问问"), cfg, ReplyRules(), now=NOW)
    assert decision.action == ACTION_NO_MATCH
    assert decision.should_reply is False
    assert decision.content is None


def test_default_reply_once_sends_when_not_sent_yet():
    """开启只回复一次但该客户尚未收到过默认回复 → 正常发送默认回复。"""
    cfg = ShopConfig(
        shop_pk=1,
        ai_enabled=False,
        default_reply_content="默认回复内容",
        default_reply_once=True,
        default_reply_already_sent=False,
    )
    decision = decide_reply(_ctx("随便问问"), cfg, ReplyRules(), now=NOW)
    assert decision.action == ACTION_DEFAULT
    assert decision.should_reply is True
    assert decision.content == "默认回复内容"


def test_default_reply_without_once_always_sends():
    """未开启只回复一次时，即便有发送记录也照常返回默认回复（兜底语义不变）。"""
    cfg = ShopConfig(
        shop_pk=1,
        ai_enabled=False,
        default_reply_content="默认回复内容",
        default_reply_once=False,
        default_reply_already_sent=True,
    )
    decision = decide_reply(_ctx("随便问问"), cfg, ReplyRules(), now=NOW)
    assert decision.action == ACTION_DEFAULT
    assert decision.should_reply is True

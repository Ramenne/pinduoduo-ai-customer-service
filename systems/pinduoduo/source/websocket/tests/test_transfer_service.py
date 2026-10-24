# -*- coding: utf-8 -*-
"""
test_transfer_service —— 转人工与商品卡片发送服务单元测试
==========================================================
本文件用途：验证 channel_pdd.transfer_service 的核心行为（需求 16.1-16.5）：
- evaluate_transfer 纯逻辑：命中转人工关键词 / AI 判定需人工 → 应转人工并暂停回复。
- get_cs_list：成功返回客服列表；底层异常时安全返回 None（失败不抛）。
- transfer_to_human：成功记「已转人工」；无客服 / 接口失败 / 异常均记失败原因且不抛。
- send_goods_card：成功返回；签名缺失 → downgrade=True；其它失败记失败原因且不抛。

测试不依赖真实数据库或网络：
- 注入假 SendMessage（sender）替换网络调用；
- monkeypatch 替换服务的 _resolve_shop_pk / _record_message_log，避免触库；
- evaluate 通过替换 load_transfer_keywords 注入关键词。
"""
from __future__ import annotations

import channel_pdd.transfer_service as ts_mod
from channel_pdd.core.anti_content import AntiContentMissingError
from channel_pdd.transfer_service import (
    GOODS_CARD_FAILED_PREFIX,
    PROCESS_RESULT_TRANSFERRED,
    TRANSFER_FAILED_PREFIX,
    TransferService,
    evaluate_transfer,
)


class _FakeSender:
    """假 SendMessage：记录调用并按配置返回预设结果 / 抛异常。"""

    def __init__(
        self,
        cs_list=None,
        move_result=None,
        card_result=None,
        card_exc=None,
        cs_exc=None,
    ):
        self._cs_list = cs_list
        self._move_result = move_result
        self._card_result = card_result
        self._card_exc = card_exc
        self._cs_exc = cs_exc
        self.calls = []

    def get_assign_cs_list(self):
        self.calls.append("get_assign_cs_list")
        if self._cs_exc is not None:
            raise self._cs_exc
        return self._cs_list

    def move_conversation(self, recipient_uid, cs_uid, remark="无原因直接转移"):
        self.calls.append(("move_conversation", recipient_uid, cs_uid))
        return self._move_result

    def send_mall_goods_card(self, recipient_uid, goods_id, biz_type=2):
        self.calls.append(("send_mall_goods_card", recipient_uid, goods_id))
        if self._card_exc is not None:
            raise self._card_exc
        return self._card_result


def _make_service(sender, shop_pk=10):
    """构造注入假 sender 的 TransferService，并 stub 掉 DB 相关方法。"""
    svc = TransferService(shop_id="shop_a", user_id=1, sender=sender)
    # 避免触库：固定 shop_pk，记录的消息日志收集到列表。
    svc._logged = []
    svc._resolve_shop_pk = lambda: shop_pk
    svc._record_message_log = lambda uid, content, result: svc._logged.append(
        (uid, content, result)
    )
    return svc


# ----------------------------------------------------------------------
# evaluate_transfer 纯逻辑（需求 16.3）
# ----------------------------------------------------------------------
def test_evaluate_keyword_hit():
    """命中转人工关键词 → 应转人工并暂停自动回复。"""
    decision = evaluate_transfer("请帮我转人工客服", ["转人工", "人工"])
    assert decision.should_transfer is True
    assert decision.pause_auto_reply is True
    assert decision.matched_keyword == "转人工"


def test_evaluate_ai_needs_human():
    """AI 判定需人工介入 → 应转人工并暂停自动回复。"""
    decision = evaluate_transfer("普通咨询", [], ai_needs_human=True)
    assert decision.should_transfer is True
    assert decision.pause_auto_reply is True
    assert "AI" in decision.reason


def test_evaluate_no_trigger():
    """无关键词命中且 AI 不需人工 → 不转人工。"""
    decision = evaluate_transfer("你好在吗", ["转人工"])
    assert decision.should_transfer is False
    assert decision.pause_auto_reply is False


def test_evaluate_empty_text_no_trigger():
    """空文本不触发关键词转人工。"""
    decision = evaluate_transfer("", ["转人工"])
    assert decision.should_transfer is False


def test_service_evaluate_uses_loaded_keywords(monkeypatch):
    """service.evaluate 结合店铺启用关键词判定。"""
    sender = _FakeSender()
    svc = _make_service(sender)
    svc.load_transfer_keywords = lambda: ["人工"]
    decision = svc.evaluate("我要找人工")
    assert decision.should_transfer is True


# ----------------------------------------------------------------------
# get_cs_list（需求 16.1）
# ----------------------------------------------------------------------
def test_get_cs_list_success():
    """成功返回客服列表。"""
    cs_list = [{"csid": "cs1", "name": "客服一"}]
    svc = _make_service(_FakeSender(cs_list=cs_list))
    assert svc.get_cs_list() == cs_list


def test_get_cs_list_exception_returns_none():
    """底层异常时安全返回 None（失败不抛）。"""
    svc = _make_service(_FakeSender(cs_exc=RuntimeError("网络错误")))
    assert svc.get_cs_list() is None


# ----------------------------------------------------------------------
# transfer_to_human（需求 16.2 / 16.5）
# ----------------------------------------------------------------------
def test_transfer_success_records_transferred():
    """转人工成功，记消息日志为「已转人工」。"""
    sender = _FakeSender(move_result={"success": True})
    svc = _make_service(sender)
    result = svc.transfer_to_human("uid_1", cs_uid="cs1", message_content="转人工")
    assert result.success is True
    assert svc._logged[-1][2] == PROCESS_RESULT_TRANSFERRED


def test_transfer_auto_pick_default_cs():
    """未指定客服时自动取客服列表首位。"""
    sender = _FakeSender(
        cs_list=[{"csid": "cs_first"}], move_result={"success": True}
    )
    svc = _make_service(sender)
    result = svc.transfer_to_human("uid_1")
    assert result.success is True
    assert ("move_conversation", "uid_1", "cs_first") in sender.calls


def test_transfer_auto_pick_uses_normalized_cs_uid():
    """规整后的客服项以 cs_uid 为标识时，自动选取首位的 cs_uid 转移。"""
    sender = _FakeSender(
        cs_list=[
            {"cs_uid": "cs_shop_a_2", "cs_name": "客服二"},
            {"cs_uid": "cs_shop_a_3", "cs_name": "客服三"},
        ],
        move_result={"success": True},
    )
    svc = _make_service(sender)
    result = svc.transfer_to_human("uid_1")
    assert result.success is True
    assert ("move_conversation", "uid_1", "cs_shop_a_2") in sender.calls


def test_transfer_no_cs_records_failure():
    """无可分配客服时记失败原因且不抛。"""
    sender = _FakeSender(cs_list=[])
    svc = _make_service(sender)
    result = svc.transfer_to_human("uid_1")
    assert result.success is False
    assert TRANSFER_FAILED_PREFIX in result.message
    assert TRANSFER_FAILED_PREFIX in svc._logged[-1][2]


def test_transfer_interface_failure_records_failure():
    """会话转移接口失败时记失败原因。"""
    sender = _FakeSender(move_result=None)
    svc = _make_service(sender)
    result = svc.transfer_to_human("uid_1", cs_uid="cs1")
    assert result.success is False
    assert TRANSFER_FAILED_PREFIX in result.message


def test_transfer_exception_does_not_raise():
    """转移过程中抛异常时被捕获并记失败原因（不中断）。"""
    class _Boom(_FakeSender):
        def move_conversation(self, *a, **k):
            raise RuntimeError("接口异常")

    svc = _make_service(_Boom())
    result = svc.transfer_to_human("uid_1", cs_uid="cs1")
    assert result.success is False
    assert TRANSFER_FAILED_PREFIX in result.message


# ----------------------------------------------------------------------
# send_goods_card（需求 16.4 / 16.5 / 26.3）
# ----------------------------------------------------------------------
def test_send_goods_card_success():
    """商品卡片发送成功。"""
    sender = _FakeSender(card_result={"success": True})
    svc = _make_service(sender)
    result = svc.send_goods_card("uid_1", "goods_99")
    assert result.success is True
    assert result.downgrade is False


def test_send_goods_card_signature_missing_downgrade():
    """签名缺失时返回 downgrade=True 并记失败原因（供降级为文本）。"""
    sender = _FakeSender(card_exc=AntiContentMissingError())
    svc = _make_service(sender)
    result = svc.send_goods_card("uid_1", "goods_99")
    assert result.success is False
    assert result.downgrade is True
    assert GOODS_CARD_FAILED_PREFIX in svc._logged[-1][2]


def test_send_goods_card_interface_failure():
    """商品卡片接口失败（返回 None）时记失败原因，非降级。"""
    sender = _FakeSender(card_result=None)
    svc = _make_service(sender)
    result = svc.send_goods_card("uid_1", "goods_99")
    assert result.success is False
    assert result.downgrade is False
    assert GOODS_CARD_FAILED_PREFIX in result.message


def test_send_goods_card_exception_does_not_raise():
    """商品卡片发送抛非签名异常时被捕获并记失败原因（不中断）。"""
    sender = _FakeSender(card_exc=RuntimeError("接口异常"))
    svc = _make_service(sender)
    result = svc.send_goods_card("uid_1", "goods_99")
    assert result.success is False
    assert result.downgrade is False

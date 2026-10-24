# -*- coding: utf-8 -*-
"""
test_pdd_message —— 拼多多消息解析与上下文提取单元测试
====================================================
本文件用途：验证 channel_pdd.pdd_message 的解析逻辑（需求 17.1 / 17.2）：
- 文本 / 图片 / 视频 / 表情 / 撤回 / 转接等类型解析为正确的 ContextType 与内容；
- 订单消息提取订单上下文（订单号 / 商品名 / goods_id / 规格 / 售后状态 / 售后类型）；
- 商品咨询 / 商品规格消息提取商品上下文（goods_id / 商品名 / 价格 / 缩略图 / 规格）；
- to_context() 正确填充 order_context / goods_context 与元信息。
"""
from __future__ import annotations

from channel_pdd.pdd_message import (
    Context,
    ContextType,
    MessageTypeHandler,
    PDDChatMessage,
    PDDMsgType,
    PDDSubType,
    _safe_get,
)


def _push_msg(msg_type, sub_type=None, message_extra=None):
    """构造一条 push 报文。"""
    message = {"type": msg_type}
    if sub_type is not None:
        message["sub_type"] = sub_type
    if message_extra:
        message.update(message_extra)
    return {"response": "push", "message": message}


def test_handle_text():
    """文本消息解析为 TEXT 类型并取出 content。"""
    msg = _push_msg(PDDMsgType.TEXT, sub_type=99, message_extra={"content": "你好"})
    chat = PDDChatMessage(msg)
    assert chat.user_msg_type == ContextType.TEXT
    assert chat.content == "你好"


def test_handle_image_and_video():
    """图片 / 视频消息解析为对应类型并取出 content。"""
    img = PDDChatMessage(_push_msg(PDDMsgType.IMAGE, message_extra={"content": "u.png"}))
    assert img.user_msg_type == ContextType.IMAGE
    assert img.content == "u.png"

    video = PDDChatMessage(_push_msg(PDDMsgType.VIDEO, message_extra={"content": "v.mp4"}))
    assert video.user_msg_type == ContextType.VIDEO
    assert video.content == "v.mp4"


def test_handle_emotion_and_withdraw():
    """表情 / 撤回消息从顶层 info 取出描述 / 提示（与拼多多实测报文一致）。

    注意：表情 / 撤回消息的 ``info`` 挂在报文顶层（与 ``message`` 平级），
    而非 ``message.info``（区别于订单 / 商品咨询消息），与参照项目 Customer-Agent
    口径一致。
    """
    emo = PDDChatMessage(
        {
            "response": "push",
            "message": {"type": PDDMsgType.EMOTION},
            "info": {"description": "微笑"},
        }
    )
    assert emo.user_msg_type == ContextType.EMOTION
    assert emo.content == "微笑"

    wd = PDDChatMessage(
        {
            "response": "push",
            "message": {"type": PDDMsgType.WITHDRAW},
            "info": {"withdraw_hint": "对方撤回了一条消息"},
        }
    )
    assert wd.user_msg_type == ContextType.WITHDRAW
    assert wd.content == "对方撤回了一条消息"


def test_handle_order_info_extracts_order_context():
    """订单消息提取完整订单上下文（需求 17.1）。"""
    info = {
        "orderSequenceNo": "ORDER123",
        "goodsID": "G1",
        "goodsName": "测试商品",
        "afterSalesStatus": "申请退款",
        "afterSalesType": "仅退款",
        "spec": "红色/L",
    }
    msg = _push_msg(PDDMsgType.TEXT, sub_type=PDDSubType.ORDER_INFO, message_extra={"info": info})
    chat = PDDChatMessage(msg)
    assert chat.user_msg_type == ContextType.ORDER_INFO
    assert chat.content == {
        "order_id": "ORDER123",
        "goods_id": "G1",
        "goods_name": "测试商品",
        "afterSalesStatus": "申请退款",
        "afterSalesType": "仅退款",
        "spec": "红色/L",
    }

    ctx = chat.to_context(shop_id="S1", shop_name="店铺")
    assert ctx.order_context == chat.content
    assert ctx.goods_context is None
    assert ctx.kwargs["shop_id"] == "S1"


def test_handle_goods_inquiry_extracts_goods_context():
    """商品咨询消息提取商品上下文（需求 17.2）。"""
    info = {
        "goodsID": "G2",
        "goodsName": "咨询商品",
        "goodsPrice": "99.00",
        "goodsThumbUrl": "thumb.png",
        "linkUrl": "http://x",
    }
    msg = _push_msg(
        PDDMsgType.TEXT, sub_type=PDDSubType.GOODS_INQUIRY, message_extra={"info": info}
    )
    chat = PDDChatMessage(msg)
    assert chat.user_msg_type == ContextType.GOODS_INQUIRY
    assert chat.content["goods_id"] == "G2"
    assert chat.content["goods_price"] == "99.00"
    assert chat.content["goods_thumb_url"] == "thumb.png"

    ctx = chat.to_context()
    assert ctx.goods_context == chat.content
    assert ctx.order_context is None


def test_handle_goods_spec_extracts_goods_context():
    """商品规格消息提取商品上下文（需求 17.2）。"""
    info = {"data": {"goodsID": "G3", "goodsName": "规格商品", "goodsPrice": "12", "spec": "蓝色"}}
    msg = _push_msg(PDDMsgType.GOODS_SPEC, message_extra={"info": info})
    chat = PDDChatMessage(msg)
    assert chat.user_msg_type == ContextType.GOODS_SPEC
    assert chat.content == {
        "goods_id": "G3",
        "goods_name": "规格商品",
        "goods_price": "12",
        "goods_spec": "蓝色",
    }
    assert chat.to_context().goods_context == chat.content


def test_handle_transfer():
    """转接消息提取收发方 uid。"""
    msg = _push_msg(
        PDDMsgType.TRANSFER,
        message_extra={"from": {"uid": "u1"}, "to": {"uid": "u2"}},
    )
    chat = PDDChatMessage(msg)
    assert chat.user_msg_type == ContextType.TRANSFER
    assert chat.content == {"from_uid": "u1", "to_uid": "u2"}


def test_mall_cs_message_short_circuit():
    """商城客服消息直接取内容，类型为 MALL_CS。"""
    msg = {
        "response": "push",
        "message": {"from": {"role": "mall_cs"}, "content": "客服消息"},
    }
    chat = PDDChatMessage(msg)
    assert chat.user_msg_type == ContextType.MALL_CS
    assert chat.content == "客服消息"


def test_unsupported_type_falls_back_to_system_status():
    """未知消息类型回退为 SYSTEM_STATUS。"""
    chat = PDDChatMessage(_push_msg(99999))
    assert chat.user_msg_type == ContextType.SYSTEM_STATUS
    assert chat.content == "[平台扩展消息]"

    unknown = PDDChatMessage({"response": "unknown"})
    assert unknown.user_msg_type == ContextType.SYSTEM_STATUS


def test_safe_get_handles_missing_and_non_dict():
    """_safe_get 对缺失键与非字典中间值安全返回默认值。"""
    assert _safe_get({"a": {"b": 1}}, "a", "b") == 1
    assert _safe_get({"a": {"b": 1}}, "a", "c") is None
    assert _safe_get({"a": 5}, "a", "b") is None
    assert _safe_get(None, "a") is None
    assert _safe_get({}, "a", default="d") == "d"


def test_handle_order_info_missing_fields_default_none():
    """订单字段缺失时各上下文字段为 None，不抛异常。"""
    msg = _push_msg(PDDMsgType.TEXT, sub_type=PDDSubType.ORDER_INFO, message_extra={})
    chat = PDDChatMessage(msg)
    assert chat.content["order_id"] is None
    assert chat.content["goods_id"] is None


def test_to_context_returns_context_dataclass():
    """to_context 返回 Context 数据类并携带元信息。"""
    msg = _push_msg(PDDMsgType.TEXT, message_extra={"content": "hi", "msg_id": "m1"})
    ctx = PDDChatMessage(msg).to_context()
    assert isinstance(ctx, Context)
    assert ctx.type == ContextType.TEXT
    assert ctx.content == "hi"
    assert ctx.kwargs["msg_id"] == "m1"


def test_handle_static_methods_directly():
    """直接调用静态解析器返回 (ContextType, content)。"""
    t, c = MessageTypeHandler.handle_text({"message": {"content": "x"}})
    assert t == ContextType.TEXT and c == "x"

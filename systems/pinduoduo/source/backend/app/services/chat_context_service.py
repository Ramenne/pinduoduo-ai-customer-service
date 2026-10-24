# -*- coding: utf-8 -*-
"""
backend.app.services.chat_context_service —— 会话订单/商品上下文业务服务
======================================================================
本文件用途：实现 backend 服务「会话订单上下文」业务逻辑（需求 17），供
chat_context 路由复用，覆盖：

- ``record_context_message(...)``：将一条会话消息及其**订单上下文**（订单号、
  商品名称、goods_id、规格、售后状态、售后类型）与**商品上下文**（goods_id、
  商品名称、价格、缩略图）随会话消息记录到 ``chat_message`` 与消息日志
  ``message_log``（需求 17.1）；同时按 (shop_pk, customer_uid) upsert 会话
  ``conversation`` 并刷新最近消息时间。
- ``get_conversation_context(...)``：在线聊天查看某会话时，展示该会话**已记录**
  的订单与商品上下文（需求 17.3）；售后状态 / 售后类型从数据字典 ``sys_dict``
  查出中文文案一并返回（需求 17.4）；全部时间字段以北京时间记录与展示
  （需求 17.5）。

实现约束（开发规范）：
- 统一响应体由 common.schemas.common 构造，HTTP 恒 200（规范 1-3）。
- 所有数据访问经 common.db.repository 参数化查询，禁止拼接 SQL（规范 16）。
- 数据范围隔离统一经 app.core.data_scope（规范 42 集中判权 / 需求 3.7）：会话
  归属其店铺 ``shop.owner_user_id``，非管理员仅可见本人 / 被授权店铺的会话。
- 时间统一北京时间（规范 17 / 需求 24.8 / 17.5）；导入置顶（规范 51）；
  中文注释（规范 37）；售后状态 / 类型枚举入字典（规范 15 / 需求 17.4）。
- 禁止物理删除业务数据（规范 11 / 需求 19.5）：本服务仅新增与查询。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.business_codes import CODE_FORBIDDEN, CODE_NOT_FOUND, MSG_FORBIDDEN
from app.core.data_scope import build_data_scope, is_in_scope
from common.db.repository import Repository
from common.models.log_models import ChatMessage, Conversation, MessageLog
from common.models.shop_models import Shop
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response, success_response
from common.services.dict_service import DictService
from common.utils.time_utils import now_beijing_naive, parse_beijing, safe_isoformat

# 售后状态 / 售后类型字典类型键（与 dict_seed_data 登记一致，需求 17.4）。
DICT_AFTERSALE_STATUS: str = "aftersale_status"
DICT_AFTERSALE_TYPE: str = "aftersale_type"

# 默认消息方向：in=接收（枚举入字典 msg_direction，需求 17.x）。
DEFAULT_DIRECTION: str = "in"


# ----------------------------------------------------------------------
# 序列化辅助
# ----------------------------------------------------------------------
def _loads_context(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """将存库的上下文 JSON 文本安全解析为字典。

    Args:
        raw: ``chat_message`` 中存储的订单 / 商品上下文 JSON 文本，可能为 None。

    Returns:
        解析后的字典；为空或解析失败时返回 None（不抛异常，保证展示健壮）。
    """
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _dumps_context(context: Optional[Dict[str, Any]]) -> Optional[str]:
    """将上下文字典序列化为存库 JSON 文本（保留中文，便于排查）。

    Args:
        context: 订单 / 商品上下文字典；None 表示无上下文。

    Returns:
        紧凑的 JSON 文本；context 为空时返回 None。
    """
    if not context:
        return None
    return json.dumps(context, ensure_ascii=False)


def _enrich_order_context(
    dict_service: DictService, order_context: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """为订单上下文补充售后状态 / 类型的中文文案（需求 17.4）。

    在原订单上下文基础上追加 ``aftersale_status_label`` / ``aftersale_type_label``
    两个字段，取值来自数据字典 ``sys_dict``；未登记的键回退为原始键值，避免
    展示空白。

    Args:
        dict_service: 数据字典服务实例。
        order_context: 订单上下文字典（订单号 / 商品名 / goods_id / 规格 /
            售后状态 / 售后类型）；None 表示无订单上下文。

    Returns:
        补充中文文案后的订单上下文字典；入参为 None 时返回 None。
    """
    if not order_context:
        return None
    enriched = dict(order_context)
    status_key = order_context.get("aftersale_status")
    type_key = order_context.get("aftersale_type")
    if status_key is not None:
        enriched["aftersale_status_label"] = dict_service.get_label(
            DICT_AFTERSALE_STATUS, str(status_key), default=str(status_key)
        )
    if type_key is not None:
        enriched["aftersale_type_label"] = dict_service.get_label(
            DICT_AFTERSALE_TYPE, str(type_key), default=str(type_key)
        )
    return enriched


def serialize_context_message(
    message: ChatMessage, dict_service: DictService
) -> Dict[str, Any]:
    """将一条携带上下文的聊天消息序列化为对外字典（需求 17.3/17.4/17.5）。

    输出消息主键、方向、类型、内容、订单 / 商品上下文（订单上下文含售后中文
    文案）与北京时间消息时间。

    Args:
        message: 聊天消息模型实例。
        dict_service: 数据字典服务实例（用于售后枚举中文转换）。

    Returns:
        聊天消息上下文字典。
    """
    order_context = _enrich_order_context(
        dict_service, _loads_context(message.order_context)
    )
    goods_context = _loads_context(message.goods_context)
    return {
        "id": message.id,
        "shop_pk": message.shop_pk,
        "customer_uid": message.customer_uid,
        "direction": message.direction,
        "msg_type": message.msg_type,
        "content": message.content,
        "order_context": order_context,
        "goods_context": goods_context,
        # 时间统一北京时间展示（需求 17.5）。
        "msg_time": safe_isoformat(message.msg_time),
    }


# ----------------------------------------------------------------------
# 数据范围校验辅助
# ----------------------------------------------------------------------
def _resolve_shop_in_scope(
    session: Session, user: SysUser, shop_pk: int
) -> Tuple[Optional[Shop], Optional[ApiResponse]]:
    """校验店铺存在且在当前用户数据范围内，返回 (店铺, 失败响应)。

    依据需求 3.7：管理员不受限；非管理员仅可操作本人创建或被授权的店铺。
    店铺不存在返回 NOT_FOUND；越权返回「无访问权限」。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        shop_pk: 店铺主键 shop.id。

    Returns:
        二元组 (店铺实例, 失败响应)：校验通过时第二项为 None；失败时第一项为 None。
    """
    shop = Repository(Shop, session).get(shop_pk)
    if shop is None or shop.merged_into_shop_pk is not None:
        return None, error_response(CODE_NOT_FOUND, "店铺不存在")
    scope = build_data_scope(user, session=session)
    if not is_in_scope(scope, shop.owner_user_id):
        return None, error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)
    return shop, None


def _parse_msg_time(msg_time: Optional[str]) -> datetime:
    """将入参的北京时间字符串解析为去时区的北京时间（需求 17.5）。

    入参为空或解析失败时回退为当前北京时间，保证消息时间始终为北京时间口径。

    Args:
        msg_time: 北京时间字符串（``%Y-%m-%d %H:%M:%S`` 或 ISO 格式）；可为 None。

    Returns:
        去时区的北京时间 datetime。
    """
    if not msg_time:
        return now_beijing_naive()
    # 优先按标准北京时间格式解析；失败再尝试 ISO 格式；仍失败回退当前北京时间。
    for parser in (_safe_parse_beijing, _parse_iso_as_beijing):
        parsed = parser(msg_time)
        if parsed is not None:
            return parsed.replace(tzinfo=None)
    return now_beijing_naive()


def _parse_iso_as_beijing(value: str) -> Optional[datetime]:
    """尝试以 ISO 8601 解析时间字符串（解析失败返回 None）。"""
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _safe_parse_beijing(value: str) -> Optional[datetime]:
    """以标准北京时间格式安全解析（parse_beijing 抛错时返回 None）。"""
    try:
        return parse_beijing(value)
    except (ValueError, TypeError):
        return None


# ----------------------------------------------------------------------
# 记录会话订单 / 商品上下文（需求 17.1 / 17.5）
# ----------------------------------------------------------------------
def record_context_message(
    session: Session,
    shop_pk: int,
    customer_uid: str,
    direction: str = DEFAULT_DIRECTION,
    msg_type: Optional[str] = None,
    content: Optional[str] = None,
    order_context: Optional[Dict[str, Any]] = None,
    goods_context: Optional[Dict[str, Any]] = None,
    nickname: Optional[str] = None,
    msg_time: Optional[str] = None,
    process_result: Optional[str] = None,
    user: Optional[SysUser] = None,
) -> ApiResponse:
    """将一条会话消息及其订单 / 商品上下文记录到消息日志（需求 17.1 / 17.5）。

    流程：
    1. （可选）数据范围隔离校验：当传入登录用户时，校验其对店铺的可见性
       （需求 3.7）；服务间内部调用可不传 user。
    2. 按 (shop_pk, customer_uid) upsert 会话 ``conversation``，刷新最近消息
       时间（北京时间）与昵称。
    3. 新增聊天消息 ``chat_message``，订单 / 商品上下文以 JSON 文本随消息存储。
    4. 新增消息日志 ``message_log``，记录原始消息内容与处理结果（北京时间）。

    Args:
        session: 数据库会话。
        shop_pk: 店铺主键 shop.id。
        customer_uid: 客户唯一标识。
        direction: 消息方向（in=收 / out=发，枚举入字典），默认 in。
        msg_type: 消息类型（text/order/goods_inquiry 等，枚举入字典）。
        content: 消息文本内容。
        order_context: 订单上下文字典（订单号/商品名/goods_id/规格/售后状态/
            售后类型），None 表示无订单上下文。
        goods_context: 商品上下文字典（goods_id/商品名/价格/缩略图），None 表示无。
        nickname: 客户昵称（用于会话列表展示）。
        msg_time: 消息北京时间字符串；None 时取当前北京时间。
        process_result: 消息处理结果（枚举入字典）；None 表示不记录处理结果。
        user: 触发记录的登录用户；传入时做数据范围隔离校验，内部调用可为 None。

    Returns:
        统一响应体：成功返回 {conversation_id, message_id, log_id}。
    """
    # 仅在带登录用户上下文时做数据范围隔离（服务间内部记录可不传 user）。
    if user is not None:
        _, denied = _resolve_shop_in_scope(session, user, shop_pk)
        if denied is not None:
            return denied
    elif Repository(Shop, session).get(shop_pk) is None:
        return error_response(CODE_NOT_FOUND, "店铺不存在")

    beijing_time = _parse_msg_time(msg_time)

    # 按 (shop_pk, customer_uid) upsert 会话，刷新最近消息时间与昵称（需求 17.5）。
    conv_values: Dict[str, Any] = {"last_msg_at": beijing_time}
    if nickname is not None:
        conv_values["nickname"] = nickname
    conversation = Repository(Conversation, session).upsert(
        biz_keys={"shop_pk": shop_pk, "customer_uid": customer_uid},
        values=conv_values,
    )

    # 新增聊天消息：订单 / 商品上下文随消息以 JSON 文本存储（需求 17.1）。
    message = Repository(ChatMessage, session).create(
        shop_pk=shop_pk,
        customer_uid=customer_uid,
        direction=direction,
        msg_type=msg_type,
        content=content,
        order_context=_dumps_context(order_context),
        goods_context=_dumps_context(goods_context),
        msg_time=beijing_time,
        created_by=user.id if user is not None else None,
    )

    # 新增消息日志：记录原始消息与处理结果（北京时间，禁止物理删除，需求 19.5）。
    log = Repository(MessageLog, session).create(
        shop_pk=shop_pk,
        customer_uid=customer_uid,
        message_content=content,
        process_result=process_result,
        reply_content=None,
        log_time=beijing_time,
        created_by=user.id if user is not None else None,
    )

    data = {
        "conversation_id": conversation.id,
        "message_id": message.id,
        "log_id": log.id,
    }
    return success_response(data=data, message="记录成功")


# ----------------------------------------------------------------------
# 展示会话已记录的订单 / 商品上下文（需求 17.3 / 17.4 / 17.5）
# ----------------------------------------------------------------------
def get_conversation_context(
    session: Session, user: SysUser, conversation_id: int
) -> ApiResponse:
    """展示某会话已记录的订单与商品上下文信息（需求 17.3/17.4/17.5）。

    据会话主键定位会话，做数据范围隔离校验后，查询该会话（shop_pk +
    customer_uid 维度）中携带订单 / 商品上下文的聊天消息，按消息时间倒序返回：
    - ``latest_order_context``：最近一条订单上下文（含售后中文文案，需求 17.4）；
    - ``latest_goods_context``：最近一条商品上下文；
    - ``context_messages``：全部携带上下文的消息列表（北京时间，需求 17.5）。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        conversation_id: 会话主键 conversation.id。

    Returns:
        统一响应体：data 含会话信息与已记录的订单 / 商品上下文。
    """
    conversation = Repository(Conversation, session).get(conversation_id)
    if conversation is None:
        return error_response(CODE_NOT_FOUND, "会话不存在")

    # 数据范围隔离：非管理员仅可查看本人 / 被授权店铺的会话（需求 3.7）。
    _, denied = _resolve_shop_in_scope(session, user, conversation.shop_pk)
    if denied is not None:
        return denied

    dict_service = DictService(session)

    # 查询该会话携带上下文的消息，按消息时间倒序（最近在前，需求 17.3/17.5）。
    messages = Repository(ChatMessage, session).list(
        filters={
            "shop_pk": conversation.shop_pk,
            "customer_uid": conversation.customer_uid,
        },
        order_by=ChatMessage.msg_time,
        desc_order=True,
    )

    context_messages: List[Dict[str, Any]] = []
    latest_order_context: Optional[Dict[str, Any]] = None
    latest_goods_context: Optional[Dict[str, Any]] = None
    for message in messages:
        # 仅保留携带订单或商品上下文的消息。
        if not message.order_context and not message.goods_context:
            continue
        serialized = serialize_context_message(message, dict_service)
        context_messages.append(serialized)
        # 取首个（最近）非空订单 / 商品上下文作为「已记录上下文」展示。
        if latest_order_context is None and serialized["order_context"] is not None:
            latest_order_context = serialized["order_context"]
        if latest_goods_context is None and serialized["goods_context"] is not None:
            latest_goods_context = serialized["goods_context"]

    data = {
        "conversation": {
            "id": conversation.id,
            "shop_pk": conversation.shop_pk,
            "customer_uid": conversation.customer_uid,
            "nickname": conversation.nickname,
            "last_msg_at": safe_isoformat(conversation.last_msg_at),
        },
        "latest_order_context": latest_order_context,
        "latest_goods_context": latest_goods_context,
        "context_messages": context_messages,
    }
    return success_response(data=data, message="查询成功")


__all__ = [
    "DICT_AFTERSALE_STATUS",
    "DICT_AFTERSALE_TYPE",
    "DEFAULT_DIRECTION",
    "serialize_context_message",
    "record_context_message",
    "get_conversation_context",
]

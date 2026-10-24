# -*- coding: utf-8 -*-
"""
channel_pdd.chat_event_forwarder —— 在线聊天事件转发器（websocket → backend）
==========================================================================
本文件用途：在线聊天实时推送（方案 2）链路中，websocket 服务收到拼多多客户消息后，
经本转发器把消息事件推送给 **backend** 的内部接口，由 backend 广播给订阅了对应店铺
的浏览器 WebSocket（参照 xianyu-auto-reply-wangpan 的实时推送）。

设计要点：
- 经 common 统一服务间 HTTP 客户端 ``service_client`` 调 backend 内部接口
  ``/api/v1/internal/chat-events``，地址经环境变量配置（禁止写死 localhost，规范 21）。
- 携带共享密钥 ``X-Internal-Token``（环境变量 ``INTERNAL_SERVICE_TOKEN``）供 backend
  校验来源。
- 健壮性兜底：推送失败仅记日志，绝不抛异常打断消息处理主链路（需求 26）；同步 HTTP
  调用经 ``asyncio.to_thread`` 丢入线程池，避免阻塞事件循环。
- 仅转发「客户发来」的消息（in 方向），客服自身回复不回推（前端发送后自行刷新）。

实现约束（开发规范）：导入置顶（51）、中文注释（37）、文件名用下划线（40）、
单文件 ≤500 行（35）、日志禁用 debug（38）、全中文（50）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from common.core.config import get_settings
from common.services import service_client

logger = logging.getLogger("channel_pdd.chat_event_forwarder")

# backend 内部聊天事件接口相对路径（与 backend 路由约定一致）。
_CHAT_EVENTS_PATH: str = service_client.backend_api_path("/internal/chat-events")

# 推送超时（秒）：尽力推送，避免阻塞消息处理。
_PUSH_TIMEOUT_SECONDS: float = 5.0


def _push_sync(shop_pk: int, customer_uid: str, message: Dict[str, Any], event='new_message') -> None:
    """同步推送一条聊天事件到 backend 内部接口（供线程池调用，异常仅记日志）。

    Args:
        shop_pk: 店铺主键 shop.id。
        customer_uid: 客户唯一标识。
        message: 已解析的消息字典（direction / content / msg_type / ts 等）。
    """
    token = get_settings().internal_service_token
    response = service_client.post_json(
        service_client.backend_base_url(),
        _CHAT_EVENTS_PATH,
        {
            "shop_pk": shop_pk,
            "event": event,
            "customer_uid": customer_uid,
            "message": message,
        },
        timeout=_PUSH_TIMEOUT_SECONDS,
        headers={"X-Internal-Token": token},
    )
    if not response.success:
        logger.warning(
            "推送聊天事件到 backend 失败：shop_pk=%s, err=%s",
            shop_pk, response.message or response.error,
        )


async def forward_new_message(
    shop_pk: Optional[int], customer_uid: Optional[str], message: Dict[str, Any]
) -> None:
    """异步转发一条「客户新消息」事件到 backend（不阻塞事件循环，失败不抛）。

    Args:
        shop_pk: 店铺主键 shop.id（缺失则跳过推送）。
        customer_uid: 客户唯一标识（缺失则跳过推送）。
        message: 已解析的消息字典。
    """
    if shop_pk is None or not customer_uid:
        return
    try:
        await asyncio.to_thread(_push_sync, int(shop_pk), str(customer_uid), message)
    except Exception as exc:  # noqa: BLE001 - 推送失败不影响消息处理主链路
        logger.warning("转发聊天事件异常：shop_pk=%s, %s", shop_pk, exc)


__all__ = ["forward_new_message"]


async def forward_control_state(shop_pk, customer_uid, state):
    try:
        await asyncio.to_thread(_push_sync, shop_pk, str(customer_uid), state, 'conversation_control')
    except Exception:
        logger.warning('接管状态通知暂未送达，页面重连后按持久状态恢复：shop_pk=%s', shop_pk)

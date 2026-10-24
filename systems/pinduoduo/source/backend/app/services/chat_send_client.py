# -*- coding: utf-8 -*-
"""
backend.app.services.chat_send_client —— 手动发送消息（经统一服务间客户端调用 websocket）
======================================================================================
本文件用途：在线聊天「手动发送消息」（需求 14.3）时，由 **websocket 服务**复用拼多多
WebSocket 能力将消息发送至对应客户会话。按设计「多服务拆分架构」，backend 不直接
维护与拼多多的长连接，而是通过 **HTTP 调用** websocket 服务的发送接口完成下发，地址
经环境变量 ``WEBSOCKET_SERVICE_URL`` 配置，**禁止写死 localhost**（规范 21）。

发送结果统一规整为 ``ManualSendResult``：
- ``ok``：是否发送成功（websocket 服务回报已下发）。
- ``message``：失败原因（中文）；成功时为空字符串。

实现要点（任务 19.1 统一）：
- 复用 common 统一服务间 HTTP 客户端 ``common.services.service_client``（规范 36/52），
  不再各自维护 urllib 客户端。
- 网络不可达 / 超时 / 非 2xx / 解析失败一律视为「发送失败」，返回 ok=False 并附中文
  原因，不抛异常打断 backend 主流程（健壮性兜底，需求 26）；记录消息日志由调用方处理。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from common.services import service_client

logger = logging.getLogger(__name__)

# 手动发送消息请求超时（秒）：下发涉及外部 WebSocket，给予有界但合理的超时。
_SEND_TIMEOUT_SECONDS: float = 15.0

# websocket 服务「手动发送消息」接口的相对路径（与 websocket 服务路由约定一致）。
_SEND_PATH: str = "/api/v1/messages/send"


@dataclass
class ManualSendResult:
    """手动发送消息结果（对 websocket 服务响应的统一规整）。

    Attributes:
        ok: 是否成功下发至客户会话。
        message: 失败原因（中文）；成功时为空字符串。
    """

    ok: bool = False
    message: str = ""
    message_id: str | None = None
    delivery_state: str = "accepted"


def send_manual_message(
    shop_pk: int,
    shop_id: str,
    owner_user_id: Optional[int],
    recipient_uid: str,
    content: str,
    operation_key: Optional[str] = None,
    control_revision: Optional[int] = None,
    history_boundary_id: Optional[int] = None,
) -> ManualSendResult:
    """经统一服务间客户端向指定客户会话发送一条文本消息（需求 14.3）。

    地址由环境变量配置（禁止写死 localhost）。无论 websocket 服务是否可达，本函数
    都不抛异常：成功返回 ok=True；失败返回 ok=False 并附中文原因，由调用方据此
    记录消息日志（发送成功 / 失败均需记日志）。

    Args:
        shop_pk: 店铺主键（shop.id）。
        shop_id: 拼多多店铺业务标识。
        owner_user_id: 店铺归属用户 ID（用于 websocket 侧定位连接 / 凭据）。
        recipient_uid: 接收消息的客户唯一标识。
        content: 待发送的文本内容。

    Returns:
        规整后的 ``ManualSendResult``。
    """
    response = service_client.post_json(
        service_client.websocket_base_url(),
        _SEND_PATH,
        {
            "shop_pk": shop_pk,
            "shop_id": shop_id,
            "owner_user_id": owner_user_id,
            "recipient_uid": recipient_uid,
            "content": content,
            "operation_key": operation_key or str(uuid.uuid4()),
            "control_revision":control_revision,
            "history_boundary_id":history_boundary_id,
        },
        timeout=_SEND_TIMEOUT_SECONDS,
    )

    if not response.ok or response.body is None:
        logger.warning(
            "调用 websocket 发送消息失败：shop_id=%s err=%s",
            shop_id,
            response.message or response.error,
        )
        return ManualSendResult(ok=False, message="发送结果待核对，请勿重复发送")

    if response.success:
        return ManualSendResult(ok=True, message_id=(response.data or {}).get("message_id"),
            delivery_state=(response.data or {}).get("delivery_state", "accepted"))
    message = response.message or "消息发送失败"
    logger.warning("店铺 shop_id=%s 手动发送消息失败：%s", shop_id, message)
    return ManualSendResult(ok=False, message=str(message))


__all__ = ["ManualSendResult", "send_manual_message"]

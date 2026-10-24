# -*- coding: utf-8 -*-
"""
backend.app.api.routes.internal_chat —— 在线聊天内部事件接收路由（websocket 服务回调）
====================================================================================
本文件用途：提供「在线聊天实时推送」链路中 **websocket 服务 → backend** 的内部回调
接口。websocket 服务收到拼多多客户消息时，经本接口把消息事件推给 backend，由
``ChatPushHub`` 广播给订阅了对应店铺的浏览器 WebSocket（方案 2 实时推送，需求 14）。

端点：
- ``POST /internal/chat-events``：接收一条聊天事件并广播给前端订阅者。

来源鉴权：本接口为**服务间内部接口**，经共享密钥 ``X-Internal-Token`` 校验来源
（密钥经环境变量 ``INTERNAL_SERVICE_TOKEN`` 配置），避免被外部直接调用。校验失败
返回失败响应（HTTP 恒 200，规范 1-3）。

实现约束（开发规范）：导入置顶（51）、中文注释（37）、文件名用下划线（40）、
日志禁用 debug（38）、全中文（50）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from app.services.chat_push_hub import get_chat_push_hub
from common.core.config import get_settings
from common.schemas.common import ApiResponse, error_response, success_response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["内部接口"])


class ChatEventRequest(BaseModel):
    """在线聊天内部事件请求体（websocket 服务推送）。"""

    shop_pk: int = Field(..., description="店铺主键 shop.id")
    event: str = Field("new_message", description="事件类型（new_message 等）")
    customer_uid: Optional[str] = Field(None, description="客户唯一标识")
    message: Optional[Dict[str, Any]] = Field(None, description="消息内容（已解析）")


@router.post(
    "/internal/chat-events",
    response_model=ApiResponse,
    summary="接收 websocket 服务推送的聊天事件并广播给前端",
)
async def receive_chat_event(
    payload: ChatEventRequest,
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
) -> ApiResponse:
    """接收一条聊天事件并广播给订阅该店铺的浏览器 WebSocket（需求 14）。

    Args:
        payload: 聊天事件请求体。
        x_internal_token: 服务间共享密钥（请求头 X-Internal-Token）。

    Returns:
        统一响应体：广播成功返回推送到的连接数；密钥不符返回失败。
    """
    expected = get_settings().internal_service_token
    if not x_internal_token or x_internal_token != expected:
        logger.warning("内部聊天事件接口鉴权失败：来源密钥不符")
        return error_response(-1, "无权访问")

    event = {
        "event": payload.event,
        "shop_pk": payload.shop_pk,
        "customer_uid": payload.customer_uid,
        "message": payload.message,
    }
    sent = await get_chat_push_hub().broadcast(payload.shop_pk, event)
    return success_response(data={"delivered": sent}, message="已广播")


__all__ = ["router"]

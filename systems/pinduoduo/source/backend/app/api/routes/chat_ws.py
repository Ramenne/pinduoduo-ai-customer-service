# -*- coding: utf-8 -*-
"""
backend.app.api.routes.chat_ws —— 在线聊天前端实时推送 WebSocket 路由
====================================================================
本文件用途：提供「在线聊天」前端实时消息推送的 WebSocket 端点（方案 2，参照
xianyu-auto-reply-wangpan 的 chat-new WebSocket）。前端按店铺订阅，连接建立后由
``ChatPushHub`` 在拼多多新消息到达时实时转发给浏览器。

端点：
- ``WS /chat/ws/{shop_pk}?token=<JWT>``：订阅指定店铺的实时消息推送。

鉴权与数据范围隔离（需求 1.3 / 3.7）：
- WebSocket 无法走 Authorization 头（浏览器原生 WS 不支持自定义头），故令牌经查询
  参数 ``token`` 传入，复用 ``decode_access_token`` 校验；无效 / 过期 / 用户停用拒绝。
- 校验用户对资源 ``chat`` 的 view 权限（需求 2.4）与对该店铺的数据范围可见性
  （需求 3.7）；越权直接关闭连接。

前端可发送 ``{"type":"ping"}`` 心跳，服务端回 ``{"event":"pong"}`` 保活。

实现约束（开发规范）：导入置顶（51）、中文注释（37）、文件名用下划线（40）、
日志禁用 debug（38）、全中文（50）。
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core import permission
from app.core.data_scope import build_data_scope, is_in_scope
from app.core.token_blacklist import get_token_blacklist
from app.services.chat_push_hub import get_chat_push_hub
from common.db.repository import Repository
from common.db.session import session_scope
from common.models.shop_models import Shop
from common.models.user_models import SysUser
from common.utils.security import decode_access_token

logger = logging.getLogger(__name__)

router = APIRouter(tags=["在线聊天"])

# 受保护资源键（与 sys_permission.resource_key 对齐）。
RESOURCE_CHAT: str = "chat"
# 用户启用状态值（与 SysUser.status 约定：1=启用）。
_USER_STATUS_ENABLED: int = 1
# WebSocket 关闭码：鉴权失败 / 越权（自定义业务关闭码，1008=策略违规）。
_WS_CLOSE_POLICY: int = 1008


def _authorize(token: Optional[str], shop_pk: Optional[int] = None) -> Optional[int]:
    """校验令牌与店铺可见性，返回通过校验的用户 ID；不通过返回 None。

    在独立的数据库会话中完成：解码令牌 → 取用户 → 校验启用 → 校验 chat.view 权限
    → 校验店铺存在且在数据范围内（需求 3.7）。

    Args:
        token: 查询参数传入的 JWT 令牌。
        shop_pk: 订阅的店铺主键。

    Returns:
        校验通过返回用户 ID；任一校验失败返回 None。
    """
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload:
        return None
    # 令牌已被主动失效（登出 / 改密）：拒绝订阅，与 HTTP 接口口径一致（需求 1.5）。
    jti = payload.get("jti")
    if jti and get_token_blacklist().is_revoked(jti):
        return None
    try:
        user_id = int(str(payload.get("sub")))
    except (TypeError, ValueError):
        return None

    with session_scope() as session:
        user = Repository(SysUser, session).get(user_id)
        if user is None or user.status != _USER_STATUS_ENABLED:
            return None
        # 权限：需对 chat 资源有 view 权限。
        if not permission.check(user, RESOURCE_CHAT, "view", session=session):
            return None
        if shop_pk is None:
            return user.id
        # 数据范围：店铺须存在且在该用户可见范围内（需求 3.7）。
        shop = Repository(Shop, session).get(shop_pk)
        if shop is None or shop.merged_into_shop_pk is not None:
            return None
        scope = build_data_scope(user, session=session)
        if not is_in_scope(scope, shop.owner_user_id):
            return None
        return user.id


@router.websocket('/chat/attention-ws')
async def attention_websocket(websocket: WebSocket):
    """一个用户级提醒通道，只发失效信号；每次广播仍校验当前店铺权限。"""
    token = websocket.query_params.get('token')
    if _authorize(token) is None:
        await websocket.close(code=_WS_CLOSE_POLICY); return
    await websocket.accept()
    hub = get_chat_push_hub()
    await hub.register_attention(websocket, token)
    try:
        await websocket.send_json({'event':'attention_changed'})
        while True:
            await websocket.receive_text()
            if _authorize(token) is None:
                await websocket.close(code=_WS_CLOSE_POLICY); break
            await websocket.send_json({'event':'pong'})
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unregister_attention(websocket)


@router.websocket("/chat/ws/{shop_pk}")
async def chat_push_websocket(websocket: WebSocket, shop_pk: int) -> None:
    """在线聊天前端实时推送 WebSocket（按店铺订阅，需求 14）。

    Args:
        websocket: 浏览器 WebSocket 连接。
        shop_pk: 订阅的店铺主键 shop.id。
    """
    token = websocket.query_params.get("token")
    user_id = _authorize(token, shop_pk)
    if user_id is None:
        # 鉴权 / 越权失败：以策略违规关闭，不进入订阅。
        await websocket.close(code=_WS_CLOSE_POLICY)
        return

    await websocket.accept()
    hub = get_chat_push_hub()
    await hub.register(shop_pk, websocket)
    try:
        await websocket.send_text(
            json.dumps(
                {"event": "connected", "shop_pk": shop_pk}, ensure_ascii=False
            )
        )
        # 持续接收前端消息（心跳）；前端断开时退出循环。
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "ping":
                await websocket.send_text(
                    json.dumps({"event": "pong"}, ensure_ascii=False)
                )
    except WebSocketDisconnect:
        logger.info("在线聊天前端 WebSocket 已断开：shop_pk=%s", shop_pk)
    except Exception as exc:  # noqa: BLE001 - 连接异常不外抛，统一清理
        logger.warning("在线聊天前端 WebSocket 异常：shop_pk=%s, %s", shop_pk, exc)
    finally:
        await hub.unregister(shop_pk, websocket)


__all__ = ["router"]

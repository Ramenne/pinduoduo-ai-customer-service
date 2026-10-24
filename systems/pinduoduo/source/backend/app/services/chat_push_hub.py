# -*- coding: utf-8 -*-
"""
backend.app.services.chat_push_hub —— 在线聊天前端实时推送中枢（进程内）
======================================================================
本文件用途：实现「在线聊天」前端实时消息推送（方案 2，参照 xianyu-auto-reply-wangpan
的 chat-new WebSocket 推送）。按本系统「多服务拆分架构」，与拼多多的长连接在
**websocket 服务**进程内；当其收到客户消息时，经内部 HTTP 接口转发给 **backend**，
由本中枢广播给订阅了对应店铺的浏览器 WebSocket 客户端。

完整链路：
    拼多多 → websocket 服务 message_consumer 收到客户消息
           → 内部 HTTP POST /api/v1/internal/chat-events（共享密钥鉴权）
           → backend ChatPushHub.broadcast(shop_pk, event)
           → 订阅该 shop_pk 的浏览器 WebSocket 实时收到推送

设计要点：
- 进程内单例 ``ChatPushHub``：维护「shop_pk → 该店铺的浏览器 WS 连接集合」；
  浏览器按 shop_pk 订阅（每个已连接店铺一条浏览器 WS，参照闲鱼版多账号各自一条）。
- 仅做内存级连接管理与广播，不持久化；线程/协程安全由 asyncio 单事件循环保证，
  广播对单个失效连接的发送异常做兜底，不影响其它连接。
- 数据范围隔离在「浏览器 WS 建连鉴权」阶段完成（见 routes/chat_ws.py）：用户只能
  订阅自己有权访问的店铺，故 broadcast 只按 shop_pk 分发即可。

实现约束（开发规范）：导入置顶（51）、中文注释（37）、文件名用下划线（40）、
单文件 ≤500 行（35）、日志禁用 debug（38）、全中文（50）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ChatPushHub:
    """在线聊天前端实时推送中枢（进程内单例）。

    维护「店铺主键 shop_pk → 订阅该店铺的浏览器 WebSocket 连接集合」，提供注册 /
    注销 / 广播能力。广播对单个失效连接的发送异常做兜底，确保不影响其它连接。
    """

    def __init__(self) -> None:
        """初始化空的订阅表。"""
        # shop_pk → 浏览器 WebSocket 连接集合。
        self._subscribers: Dict[int, Set[WebSocket]] = {}
        # 保护订阅表的异步锁（注册 / 注销 / 广播快照期间使用）。
        self._lock = asyncio.Lock()
        self._attention = {}

    async def register_attention(self, websocket, token):
        async with self._lock:
            self._attention[websocket] = token

    async def unregister_attention(self, websocket):
        async with self._lock:
            self._attention.pop(websocket, None)

    async def register(self, shop_pk: int, websocket: WebSocket) -> None:
        """登记一条订阅指定店铺的浏览器 WebSocket 连接。

        Args:
            shop_pk: 订阅的店铺主键 shop.id。
            websocket: 已 accept 的浏览器 WebSocket 连接。
        """
        async with self._lock:
            self._subscribers.setdefault(shop_pk, set()).add(websocket)
        logger.info("在线聊天前端订阅已登记：shop_pk=%s", shop_pk)

    async def unregister(self, shop_pk: int, websocket: WebSocket) -> None:
        """注销一条浏览器 WebSocket 订阅（连接断开时调用）。

        Args:
            shop_pk: 订阅的店铺主键。
            websocket: 待注销的浏览器 WebSocket 连接。
        """
        async with self._lock:
            conns = self._subscribers.get(shop_pk)
            if conns is not None:
                conns.discard(websocket)
                if not conns:
                    self._subscribers.pop(shop_pk, None)
        logger.info("在线聊天前端订阅已注销：shop_pk=%s", shop_pk)

    async def broadcast(self, shop_pk: int, event: Dict[str, Any]) -> int:
        """向订阅指定店铺的全部浏览器 WebSocket 广播一条事件。

        对单个连接的发送异常做兜底（视为已断开，从订阅表移除），不影响其它连接，
        不向上抛出（健壮性兜底，需求 26）。

        Args:
            shop_pk: 目标店铺主键。
            event: 待推送的事件字典（将序列化为 JSON 文本）。

        Returns:
            成功推送到的连接数。
        """
        async with self._lock:
            targets = list(self._subscribers.get(shop_pk, set()))
            attention_targets = list(self._attention.items())

        if event.get('event') == 'conversation_control':
            from app.api.routes.chat_ws import _authorize
            for ws, token in attention_targets:
                try:
                    if await asyncio.to_thread(_authorize, token, shop_pk) is not None:
                        await ws.send_json({'event':'attention_changed'})
                except Exception:
                    await self.unregister_attention(ws)

        payload = json.dumps(event, ensure_ascii=False)
        dead: list[WebSocket] = []
        sent = 0
        for ws in targets:
            try:
                await ws.send_text(payload)
                sent += 1
            except Exception:  # noqa: BLE001 - 单连接失败视为断开，不影响其它
                dead.append(ws)

        if dead:
            async with self._lock:
                conns = self._subscribers.get(shop_pk)
                if conns is not None:
                    for ws in dead:
                        conns.discard(ws)
                    if not conns:
                        self._subscribers.pop(shop_pk, None)
        return sent


# 进程内单例：backend 全局共享一个推送中枢。
_hub = ChatPushHub()


def get_chat_push_hub() -> ChatPushHub:
    """返回进程内的在线聊天推送中枢单例。"""
    return _hub


__all__ = ["ChatPushHub", "get_chat_push_hub"]

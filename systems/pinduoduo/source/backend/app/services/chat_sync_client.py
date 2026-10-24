# -*- coding: utf-8 -*-
"""
backend.app.services.chat_sync_client —— 在线聊天「实时同步」服务间客户端（方案 A）
=================================================================================
本文件用途：在线聊天采用「方案 A：实时调拼多多接口」拉取会话列表与历史聊天记录。
按设计「多服务拆分架构」，backend 不直接维护与拼多多的长连接，而是经 **HTTP 调用**
websocket 服务的实时拉取接口完成：

- ``fetch_conversations(...)``：调 websocket ``/messages/conversations`` 实时拉取
  某店铺的拼多多会话列表（在线聊天左侧列表数据源）。
- ``fetch_history(...)``：调 websocket ``/messages/history`` 循环翻页拉取某客户会话
  的全部历史聊天记录，并由 websocket 侧按 msg_id 去重落库到本地表。

地址经环境变量 ``WEBSOCKET_SERVICE_URL`` 配置，**禁止写死 localhost**（规范 21）。
复用 common 统一服务间 HTTP 客户端 ``service_client``（规范 36/52）。网络不可达 /
超时 / 非 2xx / 解析失败一律规整为失败结果，不抛异常打断 backend 主流程（需求 26）。

实现约束（开发规范）：导入置顶（51）、中文注释（37）、文件名用下划线（40）、
单文件 ≤500 行（35）、日志禁用 debug（38）、全中文（50）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from common.services import service_client

logger = logging.getLogger(__name__)

# 实时拉取超时（秒）：会话列表较快；历史记录因循环翻页较慢，给予较宽裕的超时。
_CONV_TIMEOUT_SECONDS: float = 30.0
_HISTORY_TIMEOUT_SECONDS: float = 90.0

# websocket 服务实时拉取接口相对路径（与 websocket 路由约定一致）。
_CONVERSATIONS_PATH: str = "/api/v1/messages/conversations"
_HISTORY_PATH: str = "/api/v1/messages/history"


@dataclass
class SyncResult:
    """实时同步结果（对 websocket 服务响应的统一规整）。

    Attributes:
        ok: 是否成功（websocket 业务层成功）。
        message: 失败原因（中文）；成功时为空字符串。
        items: 拉取到的数据列表（会话列表或消息列表）。
        persisted: 落库新增条数（仅历史记录拉取时有意义）。
    """

    ok: bool = False
    message: str = ""
    items: List[Dict[str, Any]] = field(default_factory=list)
    persisted: int = 0
    complete: bool = True
    has_more: bool = False
    next_cursor: Any = None
    sync_error: str = ""


def fetch_conversations(
    shop_id: str, owner_user_id: Optional[int], fetch_all: bool = False, *, page: int = 1
) -> SyncResult:
    """实时拉取某店铺的拼多多会话列表（方案 A，需求 14.1）。

    Args:
        shop_id: 拼多多店铺业务标识。
        owner_user_id: 店铺归属用户 ID（websocket 侧据此加载 Cookie）。
        fetch_all: 是否翻页拉取全部会话（默认仅首页）。

    Returns:
        规整后的 ``SyncResult``：成功时 items 为规整后的会话列表。
    """
    response = service_client.post_json(
        service_client.websocket_base_url(),
        _CONVERSATIONS_PATH,
        {
            "shop_id": shop_id,
            "owner_user_id": owner_user_id,
            "fetch_all": fetch_all, "page": page,
        },
        timeout=_CONV_TIMEOUT_SECONDS,
    )
    if not response.ok or response.body is None:
        logger.warning(
            "调用 websocket 拉取会话列表失败：shop_id=%s err=%s",
            shop_id, response.message or response.error,
        )
        return SyncResult(ok=False, message="会话同步服务暂不可用，请稍后重试")

    if not response.success:
        return SyncResult(ok=False, message=str(response.message or "获取会话列表失败"))

    data = response.data or {}
    return SyncResult(ok=True, items=list(data.get("conversations") or []), complete=data.get("complete", True), has_more=data.get("has_more", False), next_cursor=data.get("next_cursor"), sync_error=data.get("sync_error", ""))


def fetch_history(
    shop_pk: int,
    shop_id: str,
    owner_user_id: Optional[int],
    customer_uid: str,
    persist: bool = True, *, cursor: Optional[str] = None, max_pages: int = 100,
) -> SyncResult:
    """实时拉取某客户会话的全部历史聊天记录并落库（方案 A，需求 14.2 / 17）。

    Args:
        shop_pk: 本地店铺主键 shop.id（websocket 侧落库用）。
        shop_id: 拼多多店铺业务标识。
        owner_user_id: 店铺归属用户 ID（websocket 侧据此加载 Cookie）。
        customer_uid: 客户唯一标识。
        persist: 是否将历史记录落库到本地表（默认 True）。

    Returns:
        规整后的 ``SyncResult``：成功时 items 为消息列表，persisted 为落库新增条数。
    """
    response = service_client.post_json(
        service_client.websocket_base_url(),
        _HISTORY_PATH,
        {
            "shop_pk": shop_pk,
            "shop_id": shop_id,
            "owner_user_id": owner_user_id,
            "customer_uid": customer_uid,
            "persist": persist, "cursor": cursor, "max_pages": max_pages,
        },
        timeout=_HISTORY_TIMEOUT_SECONDS,
    )
    if not response.ok or response.body is None:
        logger.warning(
            "调用 websocket 拉取聊天记录失败：shop_id=%s customer=%s err=%s",
            shop_id, customer_uid, response.message or response.error,
        )
        return SyncResult(ok=False, message="聊天记录同步服务暂不可用，请稍后重试")

    if not response.success:
        return SyncResult(ok=False, message=str(response.message or "获取聊天记录失败"))

    data = response.data or {}
    return SyncResult(
        ok=True,
        items=list(data.get("messages") or []),
        persisted=int(data.get("persisted") or 0), complete=data.get("complete", True),
        has_more=data.get("has_more", False), next_cursor=data.get("next_cursor"),
        sync_error=data.get("storage_error") or data.get("sync_error", ""),
    )


__all__ = ["SyncResult", "fetch_conversations", "fetch_history"]

# -*- coding: utf-8 -*-
"""
backend.app.api.routes.chat —— 在线聊天接口路由
===============================================
本文件用途：提供 backend 服务的「在线聊天」REST 接口，满足需求 14（在线聊天）：

- ``GET  /chat/conversations``                会话列表（后端分页，需求 14.1）。
- ``GET  /chat/conversations/{id}/messages``  会话历史消息（北京时间，需求 14.2）。
- ``POST /chat/conversations/{id}/send``       手动发送消息（需求 14.3）。
- ``GET  /chat/hints``                         新消息提示数据（需求 14.4）。

手动发送消息（需求 14.3）：经 HTTP 调用 websocket 服务通过 WebSocket 将消息下发至
对应客户会话，并记录消息日志（发送成功 / 失败均记，需求 14.3 / 19.1）。

权限控制（需求 2.4）：所有接口先经统一权限模块 ``permission.check`` 判断当前用户
对资源 ``chat`` 的对应操作是否被授权；未授权返回 success=false、message「无访问
权限」的统一响应体（HTTP 恒 200）。数据范围隔离在服务层按店铺归属统一处理
（需求 14.1 / 3.7）。

接口约定（开发规范 1-3）：所有接口 HTTP 恒返回 200，业务成败由统一响应体
{code, success, message, data} 表达；业务逻辑委托 app.services.chat_service。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import permission
from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import chat_service
from common.db.session import get_db
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response

# 在线聊天路由：标签便于 OpenAPI 文档分组；前缀由聚合层添加。
router = APIRouter(tags=["在线聊天"])

# 受保护资源键约定（与 sys_permission.resource_key 对齐）。
RESOURCE_CHAT: str = "chat"


def _ensure_permission(
    user: SysUser, action: str, session: Session
) -> Optional[ApiResponse]:
    """统一权限校验：未授权时返回「无访问权限」响应，授权时返回 None。

    Args:
        user: 当前登录用户。
        action: 操作（view / send）。
        session: 数据库会话。

    Returns:
        未授权时返回失败响应体；已授权返回 None。
    """
    if permission.check(user, RESOURCE_CHAT, action, session=session):
        return None
    return error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)


# ----------------------------------------------------------------------
# 请求体模型
# ----------------------------------------------------------------------
class SendMessageRequest(BaseModel):
    """手动发送消息请求体。"""

    content: str = Field(..., description="待发送的消息内容")
    operation_key: Optional[str] = Field(None, min_length=8, max_length=100)


class SyncHistoryRequest(BaseModel):
    """实时同步某客户会话历史聊天记录请求体（方案 A）。"""

    shop_pk: int = Field(..., description="店铺主键 shop.id")
    customer_uid: str = Field(..., description="客户唯一标识")


class SendByUidRequest(BaseModel):
    """按 (店铺, 客户 uid) 手动发送消息请求体（实时会话直接发送）。"""

    shop_pk: int = Field(..., description="店铺主键 shop.id")
    customer_uid: str = Field(..., description="客户唯一标识")
    content: str = Field(..., description="待发送的消息内容")
    operation_key: Optional[str] = Field(None, min_length=8, max_length=100)


# ----------------------------------------------------------------------
# 在线聊天店铺列表 + 连接管理（参照闲鱼版账号列表，支持多店铺同时连接）
# ----------------------------------------------------------------------
@router.get(
    "/chat/shops",
    response_model=ApiResponse,
    summary="在线聊天店铺列表（含实时连接状态，后端分页）",
)
def list_chat_shops(
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """在线聊天店铺列表：返回当前用户可见店铺及其实时连接状态（需求 14.1）。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return chat_service.list_chat_shops(
        db, current_user, page=page, page_size=page_size
    )


@router.post(
    "/chat/shops/{shop_pk}/connect",
    response_model=ApiResponse,
    summary="连接指定店铺的拼多多长连接（在线聊天）",
)
def connect_shop(
    shop_pk: int,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """连接指定店铺的拼多多长连接（支持多店铺同时连接，需求 5.1）。"""
    denied = _ensure_permission(current_user, "send", db)
    if denied is not None:
        return denied
    return chat_service.connect_shop(db, current_user, shop_pk=shop_pk)


@router.post(
    "/chat/shops/{shop_pk}/disconnect",
    response_model=ApiResponse,
    summary="断开指定店铺的拼多多长连接（在线聊天）",
)
def disconnect_shop(
    shop_pk: int,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """断开指定店铺的拼多多长连接（需求 3.5）。"""
    denied = _ensure_permission(current_user, "send", db)
    if denied is not None:
        return denied
    return chat_service.disconnect_shop(db, current_user, shop_pk=shop_pk)


@router.post(
    "/chat/send-by-uid",
    response_model=ApiResponse,
    summary="按 (店铺, 客户 uid) 手动发送消息（实时会话）",
)
def send_by_uid(
    payload: SendByUidRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """按 (店铺, 客户 uid) 手动发送消息（实时会话直接发送，需求 14.3）。"""
    denied = _ensure_permission(current_user, "send", db)
    if denied is not None:
        return denied
    return chat_service.send_message_by_uid(
        db,
        current_user,
        shop_pk=payload.shop_pk,
        customer_uid=payload.customer_uid,
        content=payload.content,
        operation_key=payload.operation_key,
    )


# ----------------------------------------------------------------------
# 实时同步：会话列表（方案 A —— 实时调拼多多接口，需求 14.1）
# ----------------------------------------------------------------------
@router.get(
    "/chat/shops/{shop_pk}/sync-conversations",
    response_model=ApiResponse,
    summary="实时同步店铺会话列表（方案 A）",
)
def sync_conversations(
    shop_pk: int,
    fetch_all: bool = Query(False, description="是否翻页拉取全部会话"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """实时拉取指定店铺的拼多多会话列表（方案 A，需求 14.1）。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return chat_service.sync_conversations(
        db, current_user, shop_pk=shop_pk, fetch_all=fetch_all
    )


# ----------------------------------------------------------------------
# 实时同步：会话历史聊天记录（方案 A —— 实时调拼多多接口 + 落库，需求 14.2）
# ----------------------------------------------------------------------
@router.post(
    "/chat/sync-history",
    response_model=ApiResponse,
    summary="实时同步某客户会话的全部历史聊天记录（方案 A）",
)
def sync_history(
    payload: SyncHistoryRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """实时拉取某客户会话的全部历史聊天记录并落库（方案 A，需求 14.2 / 17）。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return chat_service.sync_history(
        db,
        current_user,
        shop_pk=payload.shop_pk,
        customer_uid=payload.customer_uid,
    )


# ----------------------------------------------------------------------
# 会话列表接口（需求 14.1）
# ----------------------------------------------------------------------
@router.get(
    "/chat/conversations", response_model=ApiResponse, summary="会话列表（后端分页）"
)
def list_conversations(
    shop_pk: Optional[int] = Query(None, description="可选店铺主键，仅看该店铺会话"),
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """分页查询当前用户有权访问店铺的会话列表（需求 14.1）。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return chat_service.list_conversations(
        db,
        current_user,
        shop_pk=shop_pk,
        page=page,
        page_size=page_size,
    )


# ----------------------------------------------------------------------
# 会话历史消息接口（需求 14.2）
# ----------------------------------------------------------------------
@router.get(
    "/chat/conversations/{conversation_id}/messages",
    response_model=ApiResponse,
    summary="会话历史消息（北京时间）",
)
def list_messages(
    conversation_id: int,
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """查询某会话的历史消息记录（需求 14.2）。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return chat_service.list_messages(
        db,
        current_user,
        conversation_id=conversation_id,
        page=page,
        page_size=page_size,
    )


# ----------------------------------------------------------------------
# 手动发送消息接口（需求 14.3）
# ----------------------------------------------------------------------
@router.post(
    "/chat/conversations/{conversation_id}/send",
    response_model=ApiResponse,
    summary="手动发送消息",
)
def send_message(
    conversation_id: int,
    payload: SendMessageRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """在某会话中手动发送消息（经 WebSocket 下发并记消息日志，需求 14.3）。"""
    denied = _ensure_permission(current_user, "send", db)
    if denied is not None:
        return denied
    return chat_service.send_manual_message(
        db,
        current_user,
        conversation_id=conversation_id,
        content=payload.content,
        operation_key=payload.operation_key,
    )


# ----------------------------------------------------------------------
# 新消息提示接口（需求 14.4）
# ----------------------------------------------------------------------
@router.get("/chat/hints", response_model=ApiResponse, summary="新消息提示数据")
def new_message_hints(
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """返回当前用户可见会话的新消息提示数据（未读汇总，需求 14.4）。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return chat_service.new_message_hints(db, current_user)


__all__ = ["router"]

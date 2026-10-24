# -*- coding: utf-8 -*-
"""
backend.app.api.routes.chat_context —— 会话订单/商品上下文接口路由
================================================================
本文件用途：提供 backend 服务的「会话订单上下文」REST 接口，满足需求 17：

- ``POST /chat/context``                            记录会话订单/商品上下文（需求 17.1/17.5）。
- ``GET  /chat/conversations/{conversation_id}/context``  展示会话已记录的上下文（需求 17.3/17.4/17.5）。

权限控制（需求 2.4）：接口先经统一权限模块 ``permission.check`` 判断当前用户对资源
``chat`` 的对应操作是否被授权；未授权返回 success=false、message「无访问权限」的统一
响应体（HTTP 恒 200）。数据范围隔离在服务层按会话所属店铺归属统一处理（需求 3.7）。

接口约定（开发规范 1-3）：所有接口 HTTP 恒返回 200，业务成败由统一响应体
{code, success, message, data} 表达；售后状态/类型从数据字典查中文文案（需求 17.4）；
时间统一北京时间（需求 17.5）；业务逻辑委托 app.services.chat_context_service。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import permission
from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import chat_context_service
from common.db.session import get_db
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response

# 会话上下文路由：标签便于 OpenAPI 文档分组；前缀由聚合层添加。
router = APIRouter(tags=["会话订单上下文"])

# 受保护资源键约定（与 sys_permission.resource_key 对齐）。
RESOURCE_CHAT: str = "chat"


def _ensure_permission(
    user: SysUser, action: str, session: Session
) -> Optional[ApiResponse]:
    """统一权限校验：未授权时返回「无访问权限」响应，授权时返回 None。

    Args:
        user: 当前登录用户。
        action: 操作（view / create）。
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
class RecordContextRequest(BaseModel):
    """记录会话订单/商品上下文请求体（需求 17.1/17.5）。"""

    shop_pk: int = Field(..., description="店铺主键 shop.id")
    customer_uid: str = Field(..., description="客户唯一标识")
    direction: str = Field("in", description="消息方向：in=收/out=发（枚举入字典）")
    msg_type: Optional[str] = Field(None, description="消息类型（枚举入字典）")
    content: Optional[str] = Field(None, description="消息内容")
    order_context: Optional[Dict[str, Any]] = Field(
        None,
        description="订单上下文：订单号/商品名/goods_id/规格/售后状态/售后类型",
    )
    goods_context: Optional[Dict[str, Any]] = Field(
        None, description="商品上下文：goods_id/商品名/价格/缩略图"
    )
    nickname: Optional[str] = Field(None, description="客户昵称")
    msg_time: Optional[str] = Field(None, description="消息北京时间字符串；空则取当前北京时间")
    process_result: Optional[str] = Field(None, description="消息处理结果（枚举入字典）")


# ----------------------------------------------------------------------
# 记录会话订单/商品上下文接口（需求 17.1/17.5）
# ----------------------------------------------------------------------
@router.post("/chat/context", response_model=ApiResponse, summary="记录会话订单/商品上下文")
def record_context(
    payload: RecordContextRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """记录一条会话消息及其订单/商品上下文到消息日志（需求 17.1/17.5）。"""
    denied = _ensure_permission(current_user, "create", db)
    if denied is not None:
        return denied
    return chat_context_service.record_context_message(
        db,
        shop_pk=payload.shop_pk,
        customer_uid=payload.customer_uid,
        direction=payload.direction,
        msg_type=payload.msg_type,
        content=payload.content,
        order_context=payload.order_context,
        goods_context=payload.goods_context,
        nickname=payload.nickname,
        msg_time=payload.msg_time,
        process_result=payload.process_result,
        user=current_user,
    )


# ----------------------------------------------------------------------
# 展示会话已记录的订单/商品上下文接口（需求 17.3/17.4/17.5）
# ----------------------------------------------------------------------
@router.get(
    "/chat/conversations/{conversation_id}/context",
    response_model=ApiResponse,
    summary="展示会话已记录的订单/商品上下文",
)
def get_conversation_context(
    conversation_id: int,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """展示某会话已记录的订单与商品上下文（含售后中文文案，需求 17.3/17.4/17.5）。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return chat_context_service.get_conversation_context(
        db, current_user, conversation_id=conversation_id
    )


__all__ = ["router"]

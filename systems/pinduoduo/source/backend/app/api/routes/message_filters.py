# -*- coding: utf-8 -*-
"""
backend.app.api.routes.message_filters —— 消息过滤规则接口路由
=============================================================
本文件用途：提供 backend 服务的「消息过滤规则」REST 接口，满足需求 12
（消息过滤与黑名单）中过滤规则部分：

- ``POST   /message-filters``                创建过滤规则（需求 12.1）。
- ``GET    /message-filters``                过滤规则列表（后端分页，需求 12.6）。
- ``PUT    /message-filters/{rule_id}``      修改过滤规则（条件类型/值/启停用）。
- ``PUT    /message-filters/{rule_id}/status`` 启用 / 停用过滤规则（停用即逻辑
  删除，禁止物理删除）。

权限控制（需求 2.4）：所有接口先经统一权限模块 ``permission.check`` 判断当前
用户对资源 ``message_filter`` 的对应操作是否被授权；未授权返回 success=false、
message「无访问权限」的统一响应体（HTTP 恒 200）。

接口约定（开发规范 1-3）：
- 所有接口 HTTP 恒返回 200，业务成败由统一响应体 {code, success, message,
  data} 表达。
- 业务逻辑委托 app.services.filter_service，路由层仅负责入参解析、鉴权依赖与
  权限判断；数据库会话经 common.db.session.get_db 注入。
- 数据范围隔离（非管理员仅可操作本人店铺数据）在服务层统一处理（需求 3.7）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import permission
from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import filter_service
from common.db.session import get_db
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response

# 消息过滤规则路由：标签「消息过滤」便于 OpenAPI 文档分组；前缀由聚合层添加。
router = APIRouter(tags=["消息过滤"])

# 受保护资源键：消息过滤为「店铺级设置」，统一归属店铺管理（shop）资源判权——
# 入口收敛到店铺管理页后，有店铺管理权限即可操作店铺级设置（与前端入口一致）。
RESOURCE_MESSAGE_FILTER: str = "shop"


def _ensure_permission(
    user: SysUser,
    action: str,
    session: Session,
) -> Optional[ApiResponse]:
    """统一权限校验：未授权时返回「无访问权限」响应，授权时返回 None。

    Args:
        user: 当前登录用户。
        action: 操作（view / create / update / disable）。
        session: 数据库会话。

    Returns:
        未授权时返回失败响应体；已授权返回 None。
    """
    if permission.check(user, RESOURCE_MESSAGE_FILTER, action, session=session):
        return None
    return error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)


# ----------------------------------------------------------------------
# 请求体模型
# ----------------------------------------------------------------------
class CreateFilterRuleRequest(BaseModel):
    """创建消息过滤规则请求体。"""

    shop_pk: int = Field(..., description="关联店铺主键 shop.id")
    condition_type: str = Field(
        ..., description="过滤条件类型（枚举入字典：contains/regex/msg_type）"
    )
    condition_value: str = Field(..., description="过滤条件值")
    enabled: bool = Field(True, description="是否启用，默认启用")


class UpdateFilterRuleRequest(BaseModel):
    """修改消息过滤规则请求体（仅更新显式传入字段）。"""

    condition_type: Optional[str] = Field(None, description="新的过滤条件类型")
    condition_value: Optional[str] = Field(None, description="新的过滤条件值")
    enabled: Optional[bool] = Field(None, description="新的启停用状态")


class UpdateFilterStatusRequest(BaseModel):
    """过滤规则启停用请求体。"""

    enabled: bool = Field(..., description="True=启用，False=停用（逻辑删除）")


# ----------------------------------------------------------------------
# 接口
# ----------------------------------------------------------------------
@router.post(
    "/message-filters", response_model=ApiResponse, summary="创建消息过滤规则"
)
def create_filter_rule(
    payload: CreateFilterRuleRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """创建消息过滤规则（需求 12.1）。"""
    denied = _ensure_permission(current_user, "create", db)
    if denied is not None:
        return denied
    return filter_service.create_filter_rule(
        db,
        shop_pk=payload.shop_pk,
        condition_type=payload.condition_type,
        condition_value=payload.condition_value,
        operator_id=current_user.id,
        enabled=payload.enabled,
    )


@router.get(
    "/message-filters", response_model=ApiResponse, summary="过滤规则列表（后端分页）"
)
def list_filter_rules(
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    shop_pk: Optional[int] = Query(None, description="按店铺筛选"),
    enabled: Optional[bool] = Query(None, description="按启停用筛选"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """分页查询消息过滤规则列表（需求 12.6）。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return filter_service.list_filter_rules(
        db,
        page=page,
        page_size=page_size,
        operator_id=current_user.id,
        shop_pk=shop_pk,
        enabled=enabled,
    )


@router.put(
    "/message-filters/{rule_id}",
    response_model=ApiResponse,
    summary="修改消息过滤规则",
)
def update_filter_rule(
    rule_id: int,
    payload: UpdateFilterRuleRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """修改消息过滤规则（条件类型 / 条件值 / 启停用）。"""
    denied = _ensure_permission(current_user, "update", db)
    if denied is not None:
        return denied
    return filter_service.update_filter_rule(
        db,
        rule_id=rule_id,
        operator_id=current_user.id,
        condition_type=payload.condition_type,
        condition_value=payload.condition_value,
        enabled=payload.enabled,
    )


@router.put(
    "/message-filters/{rule_id}/status",
    response_model=ApiResponse,
    summary="启用 / 停用消息过滤规则",
)
def update_filter_rule_status(
    rule_id: int,
    payload: UpdateFilterStatusRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """启用或停用消息过滤规则（停用即逻辑删除，禁止物理删除）。"""
    # 停用对应「disable」操作，启用对应「update」操作。
    action = "update" if payload.enabled else "disable"
    denied = _ensure_permission(current_user, action, db)
    if denied is not None:
        return denied
    return filter_service.set_filter_rule_status(
        db, rule_id=rule_id, enabled=payload.enabled, operator_id=current_user.id
    )


__all__ = ["router"]

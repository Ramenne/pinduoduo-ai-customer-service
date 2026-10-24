# -*- coding: utf-8 -*-
"""
backend.app.api.routes.blacklist —— 黑名单接口路由
=================================================
本文件用途：提供 backend 服务的「黑名单」REST 接口，满足需求 12（消息过滤与
黑名单）中黑名单部分：

- ``POST   /blacklist``              将客户加入黑名单（需求 12.3，幂等）。
- ``GET    /blacklist``              黑名单列表（后端分页，需求 12.6）。
- ``PUT    /blacklist/{blacklist_id}/remove`` 将客户移出黑名单（逻辑失效，禁止
  物理删除，需求 12.5）。

权限控制（需求 2.4）：所有接口先经统一权限模块 ``permission.check`` 判断当前
用户对资源 ``blacklist`` 的对应操作是否被授权；未授权返回 success=false、
message「无访问权限」的统一响应体（HTTP 恒 200）。

接口约定（开发规范 1-3）：
- 所有接口 HTTP 恒返回 200，业务成败由统一响应体表达。
- 业务逻辑委托 app.services.filter_service，路由层仅负责入参解析、鉴权依赖与
  权限判断；数据库会话经 common.db.session.get_db 注入。
- 黑名单移出经 ``is_active=False`` 逻辑失效，禁止物理删除（需求 12.5）。
- 数据范围隔离在服务层统一处理（需求 3.7）。
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

# 黑名单路由：标签「黑名单」便于 OpenAPI 文档分组；前缀由聚合层添加。
router = APIRouter(tags=["黑名单"])

# 受保护资源键：黑名单为「店铺级设置」，统一归属店铺管理（shop）资源判权——
# 入口收敛到店铺管理页后，有店铺管理权限即可操作店铺级设置（与前端入口一致）。
RESOURCE_BLACKLIST: str = "shop"


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
    if permission.check(user, RESOURCE_BLACKLIST, action, session=session):
        return None
    return error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)


# ----------------------------------------------------------------------
# 请求体模型
# ----------------------------------------------------------------------
class AddBlacklistRequest(BaseModel):
    """加入黑名单请求体。"""

    shop_pk: int = Field(..., description="关联店铺主键 shop.id")
    customer_uid: str = Field(..., description="客户唯一标识 customer_uid")


# ----------------------------------------------------------------------
# 接口
# ----------------------------------------------------------------------
@router.post("/blacklist", response_model=ApiResponse, summary="加入黑名单")
def add_to_blacklist(
    payload: AddBlacklistRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """将客户加入黑名单（需求 12.3）。重复加入幂等，不新建重复记录。"""
    denied = _ensure_permission(current_user, "create", db)
    if denied is not None:
        return denied
    return filter_service.add_to_blacklist(
        db,
        shop_pk=payload.shop_pk,
        customer_uid=payload.customer_uid,
        operator_id=current_user.id,
    )


@router.get("/blacklist", response_model=ApiResponse, summary="黑名单列表（后端分页）")
def list_blacklist(
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    shop_pk: Optional[int] = Query(None, description="按店铺筛选"),
    is_active: Optional[bool] = Query(
        None, description="按有效性筛选：True=仅有效，False=仅失效"
    ),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """分页查询黑名单列表（需求 12.6）。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return filter_service.list_blacklist(
        db,
        page=page,
        page_size=page_size,
        operator_id=current_user.id,
        shop_pk=shop_pk,
        is_active=is_active,
    )


@router.put(
    "/blacklist/{blacklist_id}/remove",
    response_model=ApiResponse,
    summary="移出黑名单（逻辑失效）",
)
def remove_from_blacklist(
    blacklist_id: int,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """将客户移出黑名单——逻辑失效，禁止物理删除数据（需求 12.5）。"""
    # 移出对应「disable」操作（逻辑删除语义）。
    denied = _ensure_permission(current_user, "disable", db)
    if denied is not None:
        return denied
    return filter_service.remove_from_blacklist(
        db, blacklist_id=blacklist_id, operator_id=current_user.id
    )


__all__ = ["router"]

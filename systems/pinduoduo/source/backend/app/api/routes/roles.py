# -*- coding: utf-8 -*-
"""
backend.app.api.routes.roles —— 角色与权限分配接口路由
=====================================================
本文件用途：提供 backend 服务的「角色管理与权限分配」REST 接口，补齐需求 2
（用户、角色与权限管理）中此前缺失的角色增删改与权限配置能力：

- ``GET    /roles``                      角色列表（后端分页）。
- ``GET    /roles/{role_id}``            查询单个角色。
- ``POST   /roles``                      新增角色（需求 2.3）。
- ``PUT    /roles/{role_id}``            修改角色名 / 启停用（需求 2.3）。
- ``PUT    /roles/{role_id}/status``     启用 / 停用角色（需求 2.3）。
- ``PUT    /roles/{role_id}/default``    设为注册默认角色（规范 41）。
- ``GET    /roles/{role_id}/permissions`` 查询角色已授予权限 id 列表。
- ``PUT    /roles/{role_id}/permissions`` 重设角色权限集合（需求 2.4）。
- ``GET    /permissions``                列出全部权限点（按资源分组，供勾选）。

权限控制（需求 2.4）：所有接口先经统一权限模块 ``permission.check`` 判断当前
用户对资源 ``role`` 的对应操作是否被授权；未授权返回 success=false、message
「无访问权限」的统一响应体（HTTP 恒 200）。

接口约定（开发规范 1-3）：HTTP 恒返回 200，业务成败由统一响应体表达；业务逻辑
委托 app.services.role_service / user_service，路由层仅做入参解析、鉴权与判权。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import permission
from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import role_service, user_service
from common.db.session import get_db
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response

# 角色与权限管理路由：标签便于 OpenAPI 分组；前缀由聚合层添加。
router = APIRouter(tags=["角色与权限"])

# 受保护资源键约定（与 sys_permission.resource_key 对齐）。
RESOURCE_ROLE: str = "role"


def _ensure_permission(
    user: SysUser,
    action: str,
    session: Session,
) -> Optional[ApiResponse]:
    """统一权限校验：未授权时返回「无访问权限」响应，授权时返回 None（需求 2.4）。"""
    if permission.check(user, RESOURCE_ROLE, action, session=session):
        return None
    return error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)


# ----------------------------------------------------------------------
# 请求体模型
# ----------------------------------------------------------------------
class CreateRoleRequest(BaseModel):
    """新增角色请求体。"""

    role_name: str = Field(..., description="角色名称（全局唯一）")


class UpdateRoleRequest(BaseModel):
    """修改角色请求体（角色名 / 启停用）。"""

    role_name: Optional[str] = Field(None, description="新角色名称；为空表示不修改")
    enabled: Optional[bool] = Field(None, description="启停用；为空表示不修改")


class RoleStatusRequest(BaseModel):
    """角色启停用请求体。"""

    enabled: bool = Field(..., description="True=启用，False=停用")


class AssignPermissionsRequest(BaseModel):
    """重设角色权限集合请求体。"""

    permission_ids: List[int] = Field(
        default_factory=list, description="目标权限 id 列表（作为权限全集）"
    )


# ----------------------------------------------------------------------
# 角色查询
# ----------------------------------------------------------------------
@router.get("/roles", response_model=ApiResponse, summary="角色列表（后端分页）")
def list_roles(
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    status: Optional[int] = Query(None, description="按状态筛选：1=启用，0=停用"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """分页查询角色列表。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return user_service.list_roles(db, page=page, page_size=page_size, status=status)


@router.get("/permissions", response_model=ApiResponse, summary="权限点列表（按资源分组）")
def list_permissions(
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """列出系统全部权限点（按资源分组并附中文名，供权限分配勾选，需求 2.3）。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return role_service.list_permissions(db)


@router.get("/roles/{role_id}", response_model=ApiResponse, summary="查询单个角色")
def get_role(
    role_id: int,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """查询单个角色。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return user_service.get_role(db, role_id=role_id)


@router.get(
    "/roles/{role_id}/permissions",
    response_model=ApiResponse,
    summary="查询角色已授予权限",
)
def get_role_permissions(
    role_id: int,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """查询角色当前已授予（有效）的权限 id 列表（需求 2.4）。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return role_service.get_role_permissions(db, role_id=role_id)


# ----------------------------------------------------------------------
# 角色增改 / 启停 / 默认
# ----------------------------------------------------------------------
@router.post("/roles", response_model=ApiResponse, summary="新增角色")
def create_role(
    payload: CreateRoleRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """新增角色（需求 2.3）。角色名全局唯一，新角色为非管理员且初始无权限。"""
    denied = _ensure_permission(current_user, "create", db)
    if denied is not None:
        return denied
    return role_service.create_role(
        db, role_name=payload.role_name, operator_id=current_user.id
    )


@router.put("/roles/{role_id}", response_model=ApiResponse, summary="修改角色")
def update_role(
    role_id: int,
    payload: UpdateRoleRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """修改角色名称 / 启停用状态（需求 2.3）。管理员角色不可修改。"""
    denied = _ensure_permission(current_user, "update", db)
    if denied is not None:
        return denied
    return role_service.update_role(
        db, role_id, role_name=payload.role_name, enabled=payload.enabled
    )


@router.put("/roles/{role_id}/status", response_model=ApiResponse, summary="启用 / 停用角色")
def set_role_status(
    role_id: int,
    payload: RoleStatusRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """启用或停用角色（需求 2.3）。停用经状态字段逻辑删除（规范 11）。"""
    action = "update" if payload.enabled else "disable"
    denied = _ensure_permission(current_user, action, db)
    if denied is not None:
        return denied
    return role_service.set_role_status(db, role_id, enabled=payload.enabled)


@router.put("/roles/{role_id}/default", response_model=ApiResponse, summary="设为默认注册角色")
def set_default_role(
    role_id: int,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """设置某角色为注册默认角色，同一时刻仅一个（规范 41）。"""
    denied = _ensure_permission(current_user, "update", db)
    if denied is not None:
        return denied
    return role_service.set_default_role(db, role_id)


@router.put(
    "/roles/{role_id}/permissions",
    response_model=ApiResponse,
    summary="重设角色权限集合",
)
def assign_permissions(
    role_id: int,
    payload: AssignPermissionsRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """为角色重设权限集合（需求 2.4）。取消授权经软删除实现（规范 11）。"""
    denied = _ensure_permission(current_user, "update", db)
    if denied is not None:
        return denied
    return role_service.assign_permissions(db, role_id, payload.permission_ids)


__all__ = ["router"]

# -*- coding: utf-8 -*-
"""
backend.app.api.routes.users —— 用户与角色管理接口路由
=====================================================
本文件用途：提供 backend 服务的「用户与角色管理」REST 接口，满足需求 2
（用户、角色与权限管理）：

- ``POST   /users``                创建用户并指定角色（需求 2.1）。
- ``GET    /users``                用户列表（后端分页，需求 2.1 配套）。
- ``PUT    /users/{user_id}/role`` 修改用户角色（需求 2.2）。
- ``PUT    /users/{user_id}/status`` 启用 / 停用用户（停用即逻辑删除且令牌
  失效，需求 2.7 / 2.8）。

权限控制（需求 2.4）：所有接口先经统一权限模块 ``permission.check`` 判断当前
用户对资源 ``user`` 的对应操作是否被授权；未授权时返回
``success=false``、``message``「无访问权限」的统一响应体（HTTP 恒 200）。

说明：角色管理与权限分配接口已拆分至 ``routes/roles.py``（角色增删改 / 权限
分配 / 权限点列表），本模块仅保留用户维度接口。

接口约定（开发规范 1-3）：
- 所有接口 HTTP 恒返回 200，业务成败由统一响应体 {code, success, message,
  data} 表达。
- 业务逻辑委托 app.services.user_service，路由层仅负责入参解析、鉴权依赖与
  权限判断；数据库会话经 common.db.session.get_db 注入。
- 返回的用户信息均经脱敏，绝不含密码明文 / 哈希（需求 1.6）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import permission
from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import user_service
from common.db.session import get_db
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response

# 用户与角色管理路由：标签「用户与角色」便于 OpenAPI 文档分组；前缀由聚合层添加。
router = APIRouter(tags=["用户与角色"])

# 受保护资源键约定（与 sys_permission.resource_key 对齐）。
RESOURCE_USER: str = "user"


def _ensure_permission(
    user: SysUser,
    resource_key: str,
    action: str,
    session: Session,
) -> Optional[ApiResponse]:
    """统一权限校验：未授权时返回「无访问权限」响应，授权时返回 None。

    依据需求 2.4，集中经 ``permission.check`` 判断；未授权返回 success=false、
    message「无访问权限」的统一响应体，由调用方直接作为接口返回。

    Args:
        user: 当前登录用户。
        resource_key: 受保护资源键（user / role）。
        action: 操作（view / create / update / disable）。
        session: 数据库会话。

    Returns:
        未授权时返回失败响应体；已授权返回 None。
    """
    if permission.check(user, resource_key, action, session=session):
        return None
    return error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)


# ----------------------------------------------------------------------
# 请求体模型
# ----------------------------------------------------------------------
class CreateUserRequest(BaseModel):
    """创建用户请求体。"""

    username: str = Field(..., description="登录用户名（全局唯一）")
    password: str = Field(..., description="明文密码（仅用于哈希，不落库明文）")
    role_id: Optional[int] = Field(None, description="指定角色 ID")
    wechat: Optional[str] = Field(None, description="个人联系方式：微信")
    qq: Optional[str] = Field(None, description="个人联系方式：QQ")


class UpdateRoleRequest(BaseModel):
    """修改用户角色请求体。"""

    role_id: Optional[int] = Field(None, description="新角色 ID；为空表示清除角色")


class UpdateStatusRequest(BaseModel):
    """启停用请求体。"""

    enabled: bool = Field(..., description="True=启用，False=停用（逻辑删除）")


# ----------------------------------------------------------------------
# 用户接口
# ----------------------------------------------------------------------
@router.post("/users", response_model=ApiResponse, summary="创建用户并指定角色")
def create_user(
    payload: CreateUserRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """创建用户并指定角色（需求 2.1）。密码经哈希存储，不返回明文 / 哈希。"""
    denied = _ensure_permission(current_user, RESOURCE_USER, "create", db)
    if denied is not None:
        return denied
    return user_service.create_user(
        db,
        username=payload.username,
        password=payload.password,
        role_id=payload.role_id,
        operator_id=current_user.id,
        wechat=payload.wechat,
        qq=payload.qq,
    )


@router.get("/users", response_model=ApiResponse, summary="用户列表（后端分页）")
def list_users(
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    status: Optional[int] = Query(None, description="按状态筛选：1=启用，0=停用"),
    role_id: Optional[int] = Query(None, description="按角色筛选"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """分页查询用户列表（需求 2.1 配套）。返回的用户信息均经脱敏。"""
    denied = _ensure_permission(current_user, RESOURCE_USER, "view", db)
    if denied is not None:
        return denied
    return user_service.list_users(
        db, page=page, page_size=page_size, status=status, role_id=role_id
    )


@router.put(
    "/users/{user_id}/role",
    response_model=ApiResponse,
    summary="修改用户角色",
)
def update_user_role(
    user_id: int,
    payload: UpdateRoleRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """修改用户角色（需求 2.2），变更在该用户下次鉴权时生效。"""
    denied = _ensure_permission(current_user, RESOURCE_USER, "update", db)
    if denied is not None:
        return denied
    return user_service.update_user_role(db, user_id=user_id, role_id=payload.role_id)


@router.put(
    "/users/{user_id}/status",
    response_model=ApiResponse,
    summary="启用 / 停用用户",
)
def update_user_status(
    user_id: int,
    payload: UpdateStatusRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """启用或停用用户（需求 2.7 / 2.8）。

    停用经状态字段逻辑删除实现（禁止物理删除）；停用后该用户现有令牌即失效
    （鉴权依赖每次校验 status，停用用户被直接拒绝）。
    """
    # 停用对应「disable」操作，启用对应「update」操作，统一以 update 资源粒度判权。
    action = "update" if payload.enabled else "disable"
    denied = _ensure_permission(current_user, RESOURCE_USER, action, db)
    if denied is not None:
        return denied
    return user_service.set_user_status(db, user_id=user_id, enabled=payload.enabled)


__all__ = ["router"]

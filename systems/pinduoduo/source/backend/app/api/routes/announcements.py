# -*- coding: utf-8 -*-
"""
backend.app.api.routes.announcements —— 公告接口路由
====================================================
本文件用途：提供 backend 服务的「公告」REST 接口，覆盖任务 8.4（公告新增 /
编辑 / 启停用 / 列表分页与用户端展示，逻辑删除），满足需求 21.3（管理员发布
公告并在前端向用户展示）：

管理端（仅管理员可访问）：
- ``POST   /announcements``            新增公告（需求 21.3）。
- ``PUT    /announcements/{id}``       编辑公告标题 / 正文。
- ``PUT    /announcements/{id}/status`` 启用 / 停用公告（逻辑上下线）。
- ``DELETE /announcements/{id}``       逻辑删除公告（禁止物理删除，规范 11）。
- ``GET    /announcements``            管理端公告列表（后端分页，默认排除已删除）。
- ``GET    /announcements/{id}``       查询单条公告详情。

用户端（所有登录用户可访问）：
- ``GET    /announcements/visible``    用户端公告展示（仅启用且未删除，分页）。

权限控制（需求 21.10 / 21.17）：公告管理为管理员专属，经统一权限模块判断当前
用户是否管理员（``permission.load_auth_context(...).is_admin``）；非管理员访问
管理端接口一律返回「无访问权限」统一响应体（HTTP 恒 200）。公告展示对所有登录
用户开放。

接口约定（开发规范 1-3）：
- 所有接口 HTTP 恒返回 200，业务成败由统一响应体 {code, success, message, data}
  表达；业务逻辑委托 app.services.announcement_service，路由层仅负责入参解析、
  依赖注入与权限判断；数据库会话经 get_db 注入。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import permission
from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import announcement_service
from common.db.session import get_db
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response

# 公告路由：标签「公告」便于 OpenAPI 文档分组；前缀由聚合层统一添加。
router = APIRouter(tags=["公告"])


def _ensure_admin(user: SysUser, session: Session) -> Optional[ApiResponse]:
    """统一权限校验：公告管理仅管理员可访问（需求 21.17）。

    经统一权限模块装配授权上下文判断是否管理员；非管理员返回「无访问权限」
    统一响应体，由调用方直接作为接口返回。

    Args:
        user: 当前登录用户。
        session: 数据库会话。

    Returns:
        非管理员返回失败响应体；管理员返回 None。
    """
    context = permission.load_auth_context(user, session)
    if context.is_admin:
        return None
    return error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)


# ----------------------------------------------------------------------
# 请求体模型
# ----------------------------------------------------------------------
class AnnouncementCreateRequest(BaseModel):
    """新增公告请求体。"""

    title: str = Field(..., description="公告标题")
    content: str = Field(..., description="公告正文")
    enabled: bool = Field(True, description="是否启用（上线即对用户可见）")


class AnnouncementUpdateRequest(BaseModel):
    """编辑公告请求体（字段可选，None 表示不修改）。"""

    title: Optional[str] = Field(None, description="公告标题")
    content: Optional[str] = Field(None, description="公告正文")


class AnnouncementStatusRequest(BaseModel):
    """公告启停用请求体。"""

    enabled: bool = Field(..., description="True=启用（上线）/ False=停用（下线）")


# ----------------------------------------------------------------------
# 用户端展示（所有登录用户，需求 21.3）—— 须置于 /{ann_id} 之前避免路由冲突
# ----------------------------------------------------------------------
@router.get(
    "/announcements/visible", response_model=ApiResponse, summary="用户端公告展示"
)
def list_visible_announcements(
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """用户端公告展示列表：仅启用且未逻辑删除，后端分页（需求 21.3）。"""
    return announcement_service.list_visible_announcements(
        db, page=page, page_size=page_size
    )


# ----------------------------------------------------------------------
# 管理端：新增（需求 21.3）
# ----------------------------------------------------------------------
@router.post("/announcements", response_model=ApiResponse, summary="新增公告")
def create_announcement(
    payload: AnnouncementCreateRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """管理员新增公告并持久化（需求 21.3）。仅管理员可访问（需求 21.17）。"""
    denied = _ensure_admin(current_user, db)
    if denied is not None:
        return denied
    return announcement_service.create_announcement(
        db,
        title=payload.title,
        content=payload.content,
        status=(
            announcement_service.STATUS_ENABLED
            if payload.enabled
            else announcement_service.STATUS_DISABLED
        ),
        operator_id=current_user.id,
    )


# ----------------------------------------------------------------------
# 管理端：列表（后端分页，需求 21.3 配套）
# ----------------------------------------------------------------------
@router.get("/announcements", response_model=ApiResponse, summary="公告列表")
def list_announcements(
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    status: Optional[int] = Query(None, description="按状态筛选：1=启用/0=停用"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """管理端公告列表后端分页（默认排除已逻辑删除）。仅管理员可访问。"""
    denied = _ensure_admin(current_user, db)
    if denied is not None:
        return denied
    return announcement_service.list_announcements(
        db, page=page, page_size=page_size, status=status
    )


# ----------------------------------------------------------------------
# 管理端：查询单条
# ----------------------------------------------------------------------
@router.get("/announcements/{ann_id}", response_model=ApiResponse, summary="公告详情")
def get_announcement(
    ann_id: int,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """查询单条公告详情（已逻辑删除视为不存在）。仅管理员可访问。"""
    denied = _ensure_admin(current_user, db)
    if denied is not None:
        return denied
    return announcement_service.get_announcement(db, ann_id)


# ----------------------------------------------------------------------
# 管理端：编辑
# ----------------------------------------------------------------------
@router.put("/announcements/{ann_id}", response_model=ApiResponse, summary="编辑公告")
def update_announcement(
    ann_id: int,
    payload: AnnouncementUpdateRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """编辑公告标题 / 正文（仅更新显式提供字段）。仅管理员可访问。"""
    denied = _ensure_admin(current_user, db)
    if denied is not None:
        return denied
    return announcement_service.update_announcement(
        db, ann_id, title=payload.title, content=payload.content
    )


# ----------------------------------------------------------------------
# 管理端：启停用（逻辑上下线）
# ----------------------------------------------------------------------
@router.put(
    "/announcements/{ann_id}/status",
    response_model=ApiResponse,
    summary="启停用公告",
)
def set_announcement_status(
    ann_id: int,
    payload: AnnouncementStatusRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """启用 / 停用公告（停用即不在用户端展示，记录保留）。仅管理员可访问。"""
    denied = _ensure_admin(current_user, db)
    if denied is not None:
        return denied
    return announcement_service.set_announcement_status(db, ann_id, payload.enabled)


# ----------------------------------------------------------------------
# 管理端：逻辑删除（禁止物理删除，规范 11 / 需求 24.6）
# ----------------------------------------------------------------------
@router.delete(
    "/announcements/{ann_id}", response_model=ApiResponse, summary="删除公告"
)
def delete_announcement(
    ann_id: int,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """逻辑删除公告（deleted_flag=True，记录保留）。仅管理员可访问。"""
    denied = _ensure_admin(current_user, db)
    if denied is not None:
        return denied
    return announcement_service.delete_announcement(db, ann_id)


__all__ = ["router"]

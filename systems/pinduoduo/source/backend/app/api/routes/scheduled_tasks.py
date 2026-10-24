# -*- coding: utf-8 -*-
"""
backend.app.api.routes.scheduled_tasks —— 定时任务与执行日志接口路由
====================================================================
本文件用途：提供 backend 服务的「定时任务」管理端 REST 接口（任务 17.6 配套），
满足需求 21.2（管理员配置定时任务并查看执行日志）：

- ``GET /scheduled-tasks``                定时任务列表（后端分页，需求 21.2）。
- ``PUT /scheduled-tasks/{id}``           更新调度方式 / 配置 / 启停用（需求 21.2）。
- ``PUT /scheduled-tasks/{id}/status``    启用 / 停用定时任务。
- ``GET /scheduled-tasks/run-logs``       执行日志列表（后端分页，可按任务键筛选）。

权限控制（需求 21.17）：定时任务为管理员专属。所有接口先经统一权限模块判断当前
用户是否管理员（``permission.load_auth_context(...).is_admin``）；非管理员一律返回
``success=false``、``message``「无访问权限」的统一响应体（HTTP 恒 200）。

接口约定（开发规范 1-3）：所有接口 HTTP 恒返回 200，业务成败由统一响应体
{code, success, message, data} 表达；业务逻辑委托 app.services.scheduled_task_service，
路由层仅负责入参解析、依赖注入与权限判断；数据库会话经 get_db 注入。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import permission
from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import scheduled_task_service
from common.db.session import get_db
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response

# 定时任务路由：标签「定时任务」便于 OpenAPI 文档分组；前缀由聚合层统一添加。
router = APIRouter(tags=["定时任务"])


def _ensure_admin(user: SysUser, session: Session) -> Optional[ApiResponse]:
    """统一权限校验：定时任务仅管理员可访问（需求 21.17）。

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
class ScheduledTaskUpdateRequest(BaseModel):
    """更新定时任务请求体（字段可选，None 表示不修改）。"""

    schedule_type: Optional[str] = Field(None, description="调度方式：cron/interval")
    schedule_config: Optional[str] = Field(
        None, description="调度配置（cron 表达式或间隔秒数）"
    )
    enabled: Optional[bool] = Field(None, description="是否启用")


class ScheduledTaskStatusRequest(BaseModel):
    """定时任务启停用请求体。"""

    enabled: bool = Field(..., description="True=启用，False=停用")


# ----------------------------------------------------------------------
# 执行日志列表（需置于 /{task_id} 之前避免路由冲突）
# ----------------------------------------------------------------------
@router.get(
    "/scheduled-tasks/run-logs",
    response_model=ApiResponse,
    summary="定时任务执行日志列表（后端分页）",
)
def list_run_logs(
    task_key: Optional[str] = Query(None, description="按任务键筛选"),
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """分页查询定时任务执行日志，可按任务键筛选（需求 21.2 / 21.4）。"""
    denied = _ensure_admin(current_user, db)
    if denied is not None:
        return denied
    return scheduled_task_service.list_run_logs(
        db, task_key=task_key, page=page, page_size=page_size
    )


# ----------------------------------------------------------------------
# 定时任务列表（后端分页，需求 21.2）
# ----------------------------------------------------------------------
@router.get(
    "/scheduled-tasks", response_model=ApiResponse, summary="定时任务列表（后端分页）"
)
def list_scheduled_tasks(
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """分页查询定时任务列表（需求 21.2）。仅管理员可访问（需求 21.17）。"""
    denied = _ensure_admin(current_user, db)
    if denied is not None:
        return denied
    return scheduled_task_service.list_tasks(db, page=page, page_size=page_size)


# ----------------------------------------------------------------------
# 更新调度配置（需求 21.2）
# ----------------------------------------------------------------------
@router.put(
    "/scheduled-tasks/{task_id}", response_model=ApiResponse, summary="更新定时任务"
)
def update_scheduled_task(
    task_id: int,
    payload: ScheduledTaskUpdateRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """更新定时任务的调度方式 / 配置 / 启停用（需求 21.2）。仅管理员可访问。"""
    denied = _ensure_admin(current_user, db)
    if denied is not None:
        return denied
    return scheduled_task_service.update_task(
        db,
        task_id=task_id,
        schedule_type=payload.schedule_type,
        schedule_config=payload.schedule_config,
        enabled=payload.enabled,
    )


# ----------------------------------------------------------------------
# 启停用
# ----------------------------------------------------------------------
@router.put(
    "/scheduled-tasks/{task_id}/status",
    response_model=ApiResponse,
    summary="启停用定时任务",
)
def set_scheduled_task_status(
    task_id: int,
    payload: ScheduledTaskStatusRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """启用 / 停用定时任务（需求 21.2 配套）。仅管理员可访问。"""
    denied = _ensure_admin(current_user, db)
    if denied is not None:
        return denied
    return scheduled_task_service.set_task_enabled(db, task_id, payload.enabled)


__all__ = ["router"]

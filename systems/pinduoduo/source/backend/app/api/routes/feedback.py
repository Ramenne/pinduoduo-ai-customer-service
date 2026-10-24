# -*- coding: utf-8 -*-
"""
backend.app.api.routes.feedback —— 意见反馈接口路由
==================================================
本文件用途：提供 backend 服务的「意见反馈」REST 接口，覆盖任务 8.4（意见反馈
提交落库与管理员查看处理回复），满足需求 21.5（前端提供意见反馈能力）：

用户端（所有登录用户可访问）：
- ``POST /feedbacks``        提交意见反馈（需求 21.5）。
- ``GET  /feedbacks/mine``   查看本人提交的反馈列表（数据范围隔离，分页）。

管理端（仅管理员可访问）：
- ``GET  /feedbacks``            查看反馈列表（后端分页，可按状态筛选）。
- ``GET  /feedbacks/{id}``       查看反馈详情。
- ``PUT  /feedbacks/{id}/reply`` 处理并回复反馈（写回复 + 更新处理状态）。

权限控制（需求 21.17）：提交反馈与查看本人反馈对所有登录用户开放；查看全部
反馈列表 / 详情与处理回复为管理员专属，经统一权限模块判断当前用户是否管理员
（``permission.load_auth_context(...).is_admin``）；非管理员访问管理端接口一律
返回「无访问权限」统一响应体（HTTP 恒 200）。

接口约定（开发规范 1-3）：
- 所有接口 HTTP 恒返回 200，业务成败由统一响应体 {code, success, message, data}
  表达；业务逻辑委托 app.services.feedback_service，路由层仅负责入参解析、依赖
  注入与权限判断；数据库会话经 get_db 注入。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import permission
from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import feedback_service
from common.db.session import get_db
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response

# 意见反馈路由：标签「意见反馈」便于 OpenAPI 文档分组；前缀由聚合层统一添加。
router = APIRouter(tags=["意见反馈"])


def _ensure_admin(user: SysUser, session: Session) -> Optional[ApiResponse]:
    """统一权限校验：反馈管理（查看全部 / 处理回复）仅管理员可访问（需求 21.17）。

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
class FeedbackSubmitRequest(BaseModel):
    """提交意见反馈请求体。"""

    content: str = Field(..., description="反馈内容")
    contact: Optional[str] = Field(None, description="联系方式（可选）")


class FeedbackReplyRequest(BaseModel):
    """管理员处理回复请求体（字段可选，None 表示不修改）。"""

    reply: Optional[str] = Field(None, description="管理员回复内容")
    status: Optional[str] = Field(
        None, description="处理状态：pending/processing/done/closed"
    )


# ----------------------------------------------------------------------
# 用户端：提交反馈（所有登录用户，需求 21.5）
# ----------------------------------------------------------------------
@router.post("/feedbacks", response_model=ApiResponse, summary="提交意见反馈")
def submit_feedback(
    payload: FeedbackSubmitRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """提交意见反馈并落库（需求 21.5）。所有登录用户可访问。"""
    return feedback_service.submit_feedback(
        db,
        content=payload.content,
        contact=payload.contact,
        user_id=current_user.id,
    )


# ----------------------------------------------------------------------
# 用户端：查看本人反馈（数据范围隔离）
# ----------------------------------------------------------------------
@router.get("/feedbacks/mine", response_model=ApiResponse, summary="本人反馈列表")
def list_my_feedbacks(
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """查看本人提交的反馈列表，后端分页（需求 21.5 配套，数据范围隔离）。"""
    return feedback_service.list_my_feedbacks(
        db, user_id=current_user.id, page=page, page_size=page_size
    )


# ----------------------------------------------------------------------
# 管理端：反馈列表（后端分页，需求 21.5）
# ----------------------------------------------------------------------
@router.get("/feedbacks", response_model=ApiResponse, summary="反馈列表")
def list_feedbacks(
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    status: Optional[str] = Query(None, description="按处理状态筛选"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """管理员查看反馈列表后端分页，可按处理状态筛选。仅管理员可访问。"""
    denied = _ensure_admin(current_user, db)
    if denied is not None:
        return denied
    return feedback_service.list_feedbacks(
        db, page=page, page_size=page_size, status=status
    )


# ----------------------------------------------------------------------
# 管理端：反馈详情
# ----------------------------------------------------------------------
@router.get("/feedbacks/{fb_id}", response_model=ApiResponse, summary="反馈详情")
def get_feedback(
    fb_id: int,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """查询单条反馈详情。仅管理员可访问。"""
    denied = _ensure_admin(current_user, db)
    if denied is not None:
        return denied
    return feedback_service.get_feedback(db, fb_id)


# ----------------------------------------------------------------------
# 管理端：处理回复（需求 21.5）
# ----------------------------------------------------------------------
@router.put(
    "/feedbacks/{fb_id}/reply", response_model=ApiResponse, summary="处理回复反馈"
)
def reply_feedback(
    fb_id: int,
    payload: FeedbackReplyRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """管理员处理并回复反馈（写回复 + 更新处理状态，需求 21.5）。仅管理员可访问。"""
    denied = _ensure_admin(current_user, db)
    if denied is not None:
        return denied
    return feedback_service.reply_feedback(
        db, fb_id, reply=payload.reply, status=payload.status
    )


__all__ = ["router"]

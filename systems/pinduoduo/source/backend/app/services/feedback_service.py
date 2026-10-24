# -*- coding: utf-8 -*-
"""
backend.app.services.feedback_service —— 意见反馈业务服务
=========================================================
本文件用途：实现 backend 服务的「意见反馈」业务逻辑（任务 8.4），供 feedback
路由复用，满足需求 21.5（前端提供意见反馈能力，用户提交反馈落库，管理员查看
并处理回复）：

- ``submit_feedback(...)``：所有登录用户提交意见反馈并落库（内容、联系方式），
  初始处理状态为 ``pending``（待处理），记录提交用户与时间（北京时间）。
- ``list_feedbacks(...)``：管理员查看反馈列表，后端分页，可按处理状态筛选，
  按创建时间倒序（新反馈在前）。
- ``get_feedback(...)``：查询单条反馈详情。
- ``reply_feedback(...)``：管理员处理并回复反馈，写入回复内容并更新处理状态。
- ``list_my_feedbacks(...)``：用户查看本人提交的反馈列表（数据范围隔离），
  后端分页。

实现约束（开发规范）：
- 统一响应体由 common.schemas.common 构造，HTTP 恒 200（规范 1-3 / 需求 24.1）。
- 所有数据访问经 common.db.repository 的参数化查询，禁止拼接 SQL（规范 16）。
- 禁止物理删除业务数据（规范 11 / 需求 24.6）。
- 处理状态枚举入数据字典表（需求 21.5），前端按字典展示中文。
- 时间字段统一北京时间（规范 17 / 需求 24.8）。
- 导入置顶（规范 51）；中文注释（规范 37）；单文件 ≤500 行（规范 35）；全中文。
- 「提交反馈为所有用户、查看处理为管理员」由路由层统一拦截，本服务专注业务逻辑。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.business_codes import CODE_NOT_FOUND, CODE_PARAM_ERROR
from common.db.repository import Repository
from common.models.task_models import Feedback
from common.schemas.common import ApiResponse, error_response, success_response
from common.utils.time_utils import now_beijing_naive

# 反馈处理状态值（与数据字典 feedback_status 约定一致）。
STATUS_PENDING: str = "pending"       # 待处理
STATUS_PROCESSING: str = "processing"  # 处理中
STATUS_DONE: str = "done"             # 已处理
STATUS_CLOSED: str = "closed"         # 已关闭

# 允许的处理状态集合，用于管理员处理回复时的入参校验。
_ALLOWED_STATUS = {STATUS_PENDING, STATUS_PROCESSING, STATUS_DONE, STATUS_CLOSED}


# ----------------------------------------------------------------------
# 序列化辅助
# ----------------------------------------------------------------------
def serialize_feedback(fb: Feedback) -> Dict[str, Any]:
    """将反馈模型序列化为对外字典。

    反馈不含敏感字段，直接输出展示所需字段；时间字段为北京时间。

    Args:
        fb: 反馈模型实例。

    Returns:
        反馈信息字典。
    """
    return {
        "id": fb.id,
        "user_id": fb.user_id,
        "content": fb.content,
        "contact": fb.contact,
        "status": fb.status,
        "reply": fb.reply,
        "created_at": fb.created_at,
        "updated_at": fb.updated_at,
    }


# ----------------------------------------------------------------------
# 提交反馈（所有用户，需求 21.5）
# ----------------------------------------------------------------------
def submit_feedback(
    session: Session,
    content: str,
    *,
    contact: Optional[str] = None,
    user_id: Optional[int] = None,
) -> ApiResponse:
    """提交意见反馈并落库（需求 21.5）。

    初始处理状态为 ``pending``（待处理），记录提交用户与提交时间（北京时间）。

    Args:
        session: 数据库会话。
        content: 反馈内容（非空）。
        contact: 联系方式（可选）。
        user_id: 提交用户 ID（普通列，无外键）。

    Returns:
        统一响应体：成功返回 data=反馈信息；失败返回中文提示。
    """
    if content is None or not content.strip():
        return error_response(CODE_PARAM_ERROR, "反馈内容不能为空")

    normalized_contact = contact.strip() if contact and contact.strip() else None
    fb = Repository(Feedback, session).create(
        user_id=user_id,
        content=content.strip(),
        contact=normalized_contact,
        status=STATUS_PENDING,
        reply=None,
        created_by=user_id,
        created_at=now_beijing_naive(),
    )
    return success_response(data=serialize_feedback(fb), message="提交成功")


# ----------------------------------------------------------------------
# 管理端列表（后端分页，需求 21.5）
# ----------------------------------------------------------------------
def list_feedbacks(
    session: Session,
    page: Any = 1,
    page_size: Any = 20,
    status: Optional[str] = None,
) -> ApiResponse:
    """管理员查看反馈列表后端分页（需求 21.5）。

    可按处理状态筛选；按创建时间倒序（仓储层自动探测时间字段）。

    Args:
        session: 数据库会话。
        page: 页码（从 1 开始，将被规整）。
        page_size: 每页条数（10/20/50/100，将被规整）。
        status: 按处理状态筛选；None 表示不筛选。

    Returns:
        统一响应体：data 为分页结构 {list, total, page, page_size}。
    """
    filters: Dict[str, Any] = {}
    if status is not None and status.strip():
        if status not in _ALLOWED_STATUS:
            return error_response(CODE_PARAM_ERROR, "处理状态非法")
        filters["status"] = status

    page_result = Repository(Feedback, session).paginate(
        page=page, page_size=page_size, filters=filters or None
    )
    serialized: List[Dict[str, Any]] = [
        serialize_feedback(fb) for fb in page_result.items
    ]
    data = {
        "list": serialized,
        "total": page_result.total,
        "page": page_result.page,
        "page_size": page_result.page_size,
    }
    return success_response(data=data, message="查询成功")


# ----------------------------------------------------------------------
# 用户端列表（仅本人，数据范围隔离）
# ----------------------------------------------------------------------
def list_my_feedbacks(
    session: Session,
    user_id: int,
    page: Any = 1,
    page_size: Any = 20,
) -> ApiResponse:
    """用户查看本人提交的反馈列表（需求 21.5 配套，数据范围隔离）。

    仅返回当前用户提交的反馈，后端分页、按创建时间倒序。

    Args:
        session: 数据库会话。
        user_id: 当前登录用户 ID。
        page: 页码（从 1 开始，将被规整）。
        page_size: 每页条数（10/20/50/100，将被规整）。

    Returns:
        统一响应体：data 为分页结构 {list, total, page, page_size}。
    """
    page_result = Repository(Feedback, session).paginate(
        page=page, page_size=page_size, filters={"user_id": user_id}
    )
    serialized: List[Dict[str, Any]] = [
        serialize_feedback(fb) for fb in page_result.items
    ]
    data = {
        "list": serialized,
        "total": page_result.total,
        "page": page_result.page,
        "page_size": page_result.page_size,
    }
    return success_response(data=data, message="查询成功")


# ----------------------------------------------------------------------
# 查询单条
# ----------------------------------------------------------------------
def get_feedback(session: Session, fb_id: int) -> ApiResponse:
    """查询单条反馈详情。

    Args:
        session: 数据库会话。
        fb_id: 反馈 ID。

    Returns:
        统一响应体：成功返回反馈信息；不存在返回失败。
    """
    fb = Repository(Feedback, session).get(fb_id)
    if fb is None:
        return error_response(CODE_NOT_FOUND, "目标反馈不存在")
    return success_response(data=serialize_feedback(fb), message="查询成功")


# ----------------------------------------------------------------------
# 管理员处理回复（需求 21.5）
# ----------------------------------------------------------------------
def reply_feedback(
    session: Session,
    fb_id: int,
    *,
    reply: Optional[str] = None,
    status: Optional[str] = None,
) -> ApiResponse:
    """管理员处理并回复反馈（需求 21.5）。

    写入回复内容并更新处理状态；未显式提供回复时仅更新状态。处理状态默认置为
    「已处理」（done），便于一键标记完成。

    Args:
        session: 数据库会话。
        fb_id: 目标反馈 ID。
        reply: 管理员回复内容；None 表示不修改回复。
        status: 目标处理状态（pending/processing/done/closed）；None 时若提供了
            回复则置为 done，否则保持不变。

    Returns:
        统一响应体：成功返回更新后的反馈信息。
    """
    fb_repo = Repository(Feedback, session)
    fb = fb_repo.get(fb_id)
    if fb is None:
        return error_response(CODE_NOT_FOUND, "目标反馈不存在")

    values: Dict[str, Any] = {}
    if reply is not None:
        if not reply.strip():
            return error_response(CODE_PARAM_ERROR, "回复内容不能为空")
        values["reply"] = reply.strip()

    # 状态确定：显式指定优先；否则若有回复则默认置为已处理。
    if status is not None and status.strip():
        if status not in _ALLOWED_STATUS:
            return error_response(CODE_PARAM_ERROR, "处理状态非法")
        values["status"] = status
    elif "reply" in values:
        values["status"] = STATUS_DONE

    if not values:
        return error_response(CODE_PARAM_ERROR, "未提供回复内容或处理状态")

    fb_repo.update(fb_id, **values)
    return success_response(data=serialize_feedback(fb), message="处理成功")


__all__ = [
    "STATUS_PENDING",
    "STATUS_PROCESSING",
    "STATUS_DONE",
    "STATUS_CLOSED",
    "serialize_feedback",
    "submit_feedback",
    "list_feedbacks",
    "list_my_feedbacks",
    "get_feedback",
    "reply_feedback",
]

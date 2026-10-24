# -*- coding: utf-8 -*-
"""
backend.app.services.announcement_service —— 公告管理业务服务
=============================================================
本文件用途：实现 backend 服务的「公告」业务逻辑（任务 8.4），供 announcements
路由复用，满足需求 21.3（管理员发布公告并在前端向用户展示）：

- ``create_announcement(...)``：管理员新增公告（标题、正文、启停用、发布时间），
  持久化并返回统一响应体（需求 21.3）。
- ``update_announcement(...)``：管理员编辑公告字段（标题 / 正文 / 发布时间）。
- ``set_announcement_status(...)``：启用 / 停用公告（status=1/0，逻辑上下线）；
  停用的公告不在用户端展示。
- ``delete_announcement(...)``：逻辑删除公告（``deleted_flag=True``），记录保留、
  禁止物理删除（规范 11 / 需求 24.6）。
- ``get_announcement(...)``：查询单条公告。
- ``list_announcements(...)``：管理端公告列表后端分页（默认排除已逻辑删除）。
- ``list_visible_announcements(...)``：用户端公告展示——仅返回「启用且未逻辑
  删除」的公告，后端分页、按发布时间 / 创建时间倒序（需求 21.3）。

实现约束（开发规范）：
- 统一响应体由 common.schemas.common 构造，HTTP 恒 200（规范 1-3 / 需求 24.1）。
- 所有数据访问经 common.db.repository 的参数化查询，禁止拼接 SQL（规范 16）。
- 禁止物理删除业务数据，删除经状态字段 ``deleted_flag`` 实现（规范 11 /
  需求 24.6）；启停用经 ``status`` 字段实现。
- 时间字段统一北京时间（规范 17 / 需求 24.8）。
- 导入置顶（规范 51）；中文注释（规范 37）；单文件 ≤500 行（规范 35）；全中文。
- 「公告管理为管理员」由路由层统一拦截，本服务专注业务逻辑。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.business_codes import CODE_NOT_FOUND, CODE_PARAM_ERROR
from common.db.repository import Repository
from common.models.task_models import Announcement
from common.schemas.common import ApiResponse, error_response, success_response
from common.utils.time_utils import now_beijing_naive

# 公告状态值（与 Announcement.status 约定一致：1=启用/上线，0=停用/下线）。
STATUS_ENABLED: int = 1
STATUS_DISABLED: int = 0


# ----------------------------------------------------------------------
# 序列化辅助
# ----------------------------------------------------------------------
def serialize_announcement(ann: Announcement) -> Dict[str, Any]:
    """将公告模型序列化为对外字典。

    公告不含敏感字段，直接输出展示所需字段；时间字段为北京时间。

    Args:
        ann: 公告模型实例。

    Returns:
        公告信息字典。
    """
    return {
        "id": ann.id,
        "title": ann.title,
        "content": ann.content,
        "status": ann.status,
        "enabled": ann.status == STATUS_ENABLED,
        "publish_at": ann.publish_at,
        "deleted_flag": bool(ann.deleted_flag),
        "created_by": ann.created_by,
        "created_at": ann.created_at,
        "updated_at": ann.updated_at,
    }


# ----------------------------------------------------------------------
# 新增（需求 21.3）
# ----------------------------------------------------------------------
def create_announcement(
    session: Session,
    title: str,
    content: str,
    *,
    status: int = STATUS_ENABLED,
    operator_id: Optional[int] = None,
) -> ApiResponse:
    """新增公告并持久化（需求 21.3）。

    发布时间默认取当前北京时间；状态默认启用（上线即对用户可见）。

    Args:
        session: 数据库会话。
        title: 公告标题（非空）。
        content: 公告正文（非空）。
        status: 状态（1=启用/0=停用），默认启用。
        operator_id: 操作人（管理员）用户 ID，作为发布人审计字段。

    Returns:
        统一响应体：成功返回 data=公告信息；失败返回中文提示。
    """
    if title is None or not title.strip():
        return error_response(CODE_PARAM_ERROR, "公告标题不能为空")
    if content is None or not content.strip():
        return error_response(CODE_PARAM_ERROR, "公告内容不能为空")
    normalized_status = STATUS_ENABLED if int(status) == STATUS_ENABLED else STATUS_DISABLED

    ann = Repository(Announcement, session).create(
        title=title.strip(),
        content=content.strip(),
        status=normalized_status,
        publish_at=now_beijing_naive(),
        deleted_flag=False,
        created_by=operator_id,
    )
    return success_response(data=serialize_announcement(ann), message="发布成功")


# ----------------------------------------------------------------------
# 编辑
# ----------------------------------------------------------------------
def update_announcement(
    session: Session,
    ann_id: int,
    *,
    title: Optional[str] = None,
    content: Optional[str] = None,
) -> ApiResponse:
    """编辑公告字段（仅更新显式提供的字段）。

    Args:
        session: 数据库会话。
        ann_id: 目标公告 ID。
        title: 新标题；None 表示不修改。
        content: 新正文；None 表示不修改。

    Returns:
        统一响应体：成功返回更新后的公告信息。
    """
    ann_repo = Repository(Announcement, session)
    ann = ann_repo.get(ann_id)
    if ann is None or ann.deleted_flag:
        return error_response(CODE_NOT_FOUND, "目标公告不存在")

    values: Dict[str, Any] = {}
    if title is not None:
        if not title.strip():
            return error_response(CODE_PARAM_ERROR, "公告标题不能为空")
        values["title"] = title.strip()
    if content is not None:
        if not content.strip():
            return error_response(CODE_PARAM_ERROR, "公告内容不能为空")
        values["content"] = content.strip()
    if not values:
        return error_response(CODE_PARAM_ERROR, "未提供任何待更新字段")

    ann_repo.update(ann_id, **values)
    return success_response(data=serialize_announcement(ann), message="更新成功")


# ----------------------------------------------------------------------
# 启停用（逻辑上下线）
# ----------------------------------------------------------------------
def set_announcement_status(
    session: Session,
    ann_id: int,
    enabled: bool,
) -> ApiResponse:
    """启用或停用公告（需求 21.3 配套）。

    停用经状态字段 ``status=0`` 标记，停用的公告不在用户端展示；记录保留。

    Args:
        session: 数据库会话。
        ann_id: 目标公告 ID。
        enabled: True=启用（上线），False=停用（下线）。

    Returns:
        统一响应体：成功返回更新后的公告信息。
    """
    ann_repo = Repository(Announcement, session)
    ann = ann_repo.get(ann_id)
    if ann is None or ann.deleted_flag:
        return error_response(CODE_NOT_FOUND, "目标公告不存在")

    ann_repo.update(ann_id, status=STATUS_ENABLED if enabled else STATUS_DISABLED)
    return success_response(
        data=serialize_announcement(ann),
        message="已启用" if enabled else "已停用",
    )


# ----------------------------------------------------------------------
# 逻辑删除（禁止物理删除业务数据，规范 11 / 需求 24.6）
# ----------------------------------------------------------------------
def delete_announcement(session: Session, ann_id: int) -> ApiResponse:
    """逻辑删除公告（需求 21.3 配套，规范 11）。

    依据「禁止物理删除业务数据」，通过状态字段 ``deleted_flag=True`` 标记删除，
    记录保留、总数不变；已删除公告不在管理端默认列表与用户端展示中出现。

    Args:
        session: 数据库会话。
        ann_id: 目标公告 ID。

    Returns:
        统一响应体：成功返回提示；不存在返回失败。
    """
    ann_repo = Repository(Announcement, session)
    ann = ann_repo.get(ann_id)
    if ann is None or ann.deleted_flag:
        return error_response(CODE_NOT_FOUND, "目标公告不存在")

    # 经仓储层逻辑删除：deleted_flag 为最高优先级状态字段，置为已删除值。
    ann_repo.soft_delete(ann_id, field="deleted_flag", deleted_value=True)
    return success_response(data=None, message="已删除")


# ----------------------------------------------------------------------
# 查询单条
# ----------------------------------------------------------------------
def get_announcement(session: Session, ann_id: int) -> ApiResponse:
    """查询单条公告（已逻辑删除视为不存在）。

    Args:
        session: 数据库会话。
        ann_id: 公告 ID。

    Returns:
        统一响应体：成功返回公告信息；不存在返回失败。
    """
    ann = Repository(Announcement, session).get(ann_id)
    if ann is None or ann.deleted_flag:
        return error_response(CODE_NOT_FOUND, "目标公告不存在")
    return success_response(data=serialize_announcement(ann), message="查询成功")


# ----------------------------------------------------------------------
# 管理端列表（后端分页，默认排除已逻辑删除）
# ----------------------------------------------------------------------
def list_announcements(
    session: Session,
    page: Any = 1,
    page_size: Any = 20,
    status: Optional[int] = None,
) -> ApiResponse:
    """管理端公告列表后端分页（需求 21.3 配套）。

    默认排除已逻辑删除的公告（``deleted_flag=False``）；可按启用状态筛选。
    按创建时间倒序（仓储层自动探测时间字段）。

    Args:
        session: 数据库会话。
        page: 页码（从 1 开始，将被规整）。
        page_size: 每页条数（10/20/50/100，将被规整）。
        status: 按状态筛选（1=启用/0=停用）；None 表示不筛选。

    Returns:
        统一响应体：data 为分页结构 {list, total, page, page_size}。
    """
    filters: Dict[str, Any] = {"deleted_flag": False}
    if status is not None:
        filters["status"] = STATUS_ENABLED if int(status) == STATUS_ENABLED else STATUS_DISABLED

    page_result = Repository(Announcement, session).paginate(
        page=page, page_size=page_size, filters=filters
    )
    serialized: List[Dict[str, Any]] = [
        serialize_announcement(ann) for ann in page_result.items
    ]
    data = {
        "list": serialized,
        "total": page_result.total,
        "page": page_result.page,
        "page_size": page_result.page_size,
    }
    return success_response(data=data, message="查询成功")


# ----------------------------------------------------------------------
# 用户端展示（仅启用且未逻辑删除，需求 21.3）
# ----------------------------------------------------------------------
def list_visible_announcements(
    session: Session,
    page: Any = 1,
    page_size: Any = 20,
) -> ApiResponse:
    """用户端公告展示列表（需求 21.3）。

    仅返回「启用（status=1）且未逻辑删除（deleted_flag=False）」的公告，后端
    分页、按创建时间倒序（新公告在前），供所有用户查看。

    Args:
        session: 数据库会话。
        page: 页码（从 1 开始，将被规整）。
        page_size: 每页条数（10/20/50/100，将被规整）。

    Returns:
        统一响应体：data 为分页结构 {list, total, page, page_size}。
    """
    filters = {"deleted_flag": False, "status": STATUS_ENABLED}
    page_result = Repository(Announcement, session).paginate(
        page=page, page_size=page_size, filters=filters
    )
    serialized: List[Dict[str, Any]] = [
        serialize_announcement(ann) for ann in page_result.items
    ]
    data = {
        "list": serialized,
        "total": page_result.total,
        "page": page_result.page,
        "page_size": page_result.page_size,
    }
    return success_response(data=data, message="查询成功")


__all__ = [
    "STATUS_ENABLED",
    "STATUS_DISABLED",
    "serialize_announcement",
    "create_announcement",
    "update_announcement",
    "set_announcement_status",
    "delete_announcement",
    "get_announcement",
    "list_announcements",
    "list_visible_announcements",
]

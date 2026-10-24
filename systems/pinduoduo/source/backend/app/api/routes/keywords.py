# -*- coding: utf-8 -*-
"""
backend.app.api.routes.keywords —— 关键词规则管理接口路由
=========================================================
本文件用途：提供 backend 服务的「关键词自动回复规则」REST 接口，满足需求 6
（关键词自动回复）中的「配置管理」部分：

- ``POST   /keywords``                创建关键词规则（需求 6.1）。
- ``GET    /keywords``                关键词规则列表（后端分页，需求 6.6）。
- ``GET    /keywords/{rule_id}``      查询单条关键词规则。
- ``PUT    /keywords/{rule_id}``      修改关键词规则（需求 6.1 配套）。
- ``PUT    /keywords/{rule_id}/status`` 启用 / 停用规则（下一条消息生效，需求 6.7）。
- ``DELETE /keywords/{rule_id}``      逻辑删除关键词规则（禁止物理删除，规范 11）。

权限控制（需求 2.4）：所有接口先经统一权限模块 ``permission.check`` 判断当前
用户对资源 ``keyword`` 的对应操作是否被授权；未授权时返回 ``success=false``、
``message``「无访问权限」的统一响应体（HTTP 恒 200）。

接口约定（开发规范 1-3）：
- 所有接口 HTTP 恒返回 200，业务成败由统一响应体 {code, success, message,
  data} 表达。
- 业务逻辑委托 app.services.keyword_service，路由层仅负责入参解析、鉴权依赖与
  权限判断；数据库会话经 common.db.session.get_db 注入。
- 导入置顶（规范 51）；中文注释（规范 37）；全中文文案（规范 27）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import permission
from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import keyword_service
from common.db.session import get_db
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response

# 关键词规则管理路由：标签「关键词规则」便于 OpenAPI 文档分组；前缀由聚合层添加。
router = APIRouter(tags=["关键词规则"])

# 受保护资源键约定（与 sys_permission.resource_key 对齐）。
RESOURCE_KEYWORD: str = "keyword"


def _ensure_permission(
    user: SysUser,
    action: str,
    session: Session,
) -> Optional[ApiResponse]:
    """统一权限校验：未授权时返回「无访问权限」响应，授权时返回 None。

    依据需求 2.4，集中经 ``permission.check`` 判断；未授权返回 success=false、
    message「无访问权限」的统一响应体，由调用方直接作为接口返回。

    Args:
        user: 当前登录用户。
        action: 操作（view / create / update / disable）。
        session: 数据库会话。

    Returns:
        未授权时返回失败响应体；已授权返回 None。
    """
    if permission.check(user, RESOURCE_KEYWORD, action, session=session):
        return None
    return error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)


# ----------------------------------------------------------------------
# 请求体模型
# ----------------------------------------------------------------------
class CreateKeywordRuleRequest(BaseModel):
    """创建关键词规则请求体。"""

    shop_pk: int = Field(..., description="关联店铺主键 shop.id")
    keyword: str = Field(..., description="关键词（匹配文本或正则）")
    match_type: str = Field(..., description="匹配方式：full/contains/regex")
    reply_content: str = Field(..., description="回复内容（文本或图片地址）")
    reply_type: str = Field("text", description="回复类型：text/image")
    priority: int = Field(0, description="优先级（越大越优先）")
    enabled: bool = Field(True, description="是否启用")


class UpdateKeywordRuleRequest(BaseModel):
    """修改关键词规则请求体（仅更新显式提供的字段）。"""

    keyword: Optional[str] = Field(None, description="新关键词；为空表示不修改")
    match_type: Optional[str] = Field(None, description="新匹配方式；为空表示不修改")
    reply_type: Optional[str] = Field(None, description="新回复类型；为空表示不修改")
    reply_content: Optional[str] = Field(None, description="新回复内容；为空表示不修改")
    priority: Optional[int] = Field(None, description="新优先级；为空表示不修改")


class UpdateStatusRequest(BaseModel):
    """启停用请求体。"""

    enabled: bool = Field(..., description="True=启用，False=停用（下一条消息生效）")


# ----------------------------------------------------------------------
# 关键词规则接口
# ----------------------------------------------------------------------
@router.post("/keywords", response_model=ApiResponse, summary="创建关键词规则")
def create_keyword_rule(
    payload: CreateKeywordRuleRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """创建关键词规则（需求 6.1）。"""
    denied = _ensure_permission(current_user, "create", db)
    if denied is not None:
        return denied
    return keyword_service.create_keyword_rule(
        db,
        shop_pk=payload.shop_pk,
        keyword=payload.keyword,
        match_type=payload.match_type,
        reply_content=payload.reply_content,
        reply_type=payload.reply_type,
        priority=payload.priority,
        enabled=payload.enabled,
        operator_id=current_user.id,
    )


@router.get("/keywords", response_model=ApiResponse, summary="关键词规则列表（后端分页）")
def list_keyword_rules(
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    shop_pk: Optional[int] = Query(None, description="按店铺主键筛选"),
    enabled: Optional[bool] = Query(None, description="按启用状态筛选"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """分页查询关键词规则列表（需求 6.6，后端分页）。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return keyword_service.list_keyword_rules(
        db,
        page=page,
        page_size=page_size,
        shop_pk=shop_pk,
        enabled=enabled,
        operator_id=current_user.id,
    )


@router.get(
    "/keywords/{rule_id}",
    response_model=ApiResponse,
    summary="查询单条关键词规则",
)
def get_keyword_rule(
    rule_id: int,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """查询单条关键词规则。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return keyword_service.get_keyword_rule(
        db, rule_id=rule_id, operator_id=current_user.id
    )


@router.put(
    "/keywords/{rule_id}",
    response_model=ApiResponse,
    summary="修改关键词规则",
)
def update_keyword_rule(
    rule_id: int,
    payload: UpdateKeywordRuleRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """修改关键词规则（需求 6.1 配套），变更在下一条消息处理时生效。"""
    denied = _ensure_permission(current_user, "update", db)
    if denied is not None:
        return denied
    return keyword_service.update_keyword_rule(
        db,
        rule_id=rule_id,
        keyword=payload.keyword,
        match_type=payload.match_type,
        reply_type=payload.reply_type,
        reply_content=payload.reply_content,
        priority=payload.priority,
        operator_id=current_user.id,
    )


@router.put(
    "/keywords/{rule_id}/status",
    response_model=ApiResponse,
    summary="启用 / 停用关键词规则",
)
def update_keyword_rule_status(
    rule_id: int,
    payload: UpdateStatusRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """启用或停用关键词规则（需求 6.7）。

    停用规则不参与匹配；该变更在下一条消息处理时由 websocket 引擎实时读取生效。
    """
    # 停用对应「disable」操作，启用对应「update」操作。
    action = "update" if payload.enabled else "disable"
    denied = _ensure_permission(current_user, action, db)
    if denied is not None:
        return denied
    return keyword_service.set_keyword_rule_status(
        db, rule_id=rule_id, enabled=payload.enabled, operator_id=current_user.id
    )


@router.delete(
    "/keywords/{rule_id}",
    response_model=ApiResponse,
    summary="删除关键词规则（逻辑删除）",
)
def delete_keyword_rule(
    rule_id: int,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """逻辑删除关键词规则（规范 11：禁止物理删除业务数据）。"""
    denied = _ensure_permission(current_user, "disable", db)
    if denied is not None:
        return denied
    return keyword_service.delete_keyword_rule(
        db, rule_id=rule_id, operator_id=current_user.id
    )


__all__ = ["router"]

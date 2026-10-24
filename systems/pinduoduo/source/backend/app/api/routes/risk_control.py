# -*- coding: utf-8 -*-
"""
backend.app.api.routes.risk_control —— 风控规则配置接口路由
==========================================================
本文件用途：提供 backend 服务的「风控规则配置」REST 接口，满足需求 13
（风控管理）中配置与字典部分：

- ``PUT  /shops/{shop_pk}/risk-rule``  配置 / 更新店铺风控规则（单会话/单店铺
  回复频率上限与统计窗口），按店铺 upsert（幂等，需求 13.1）。
- ``GET  /shops/{shop_pk}/risk-rule``  查询店铺风控规则配置；未配置返回
  data=null。
- ``GET  /risk-types``                查询「风控类型」枚举字典（key->中文文案），
  供前端中文展示（需求 13.4）。

权限控制（需求 2.4）：所有接口先经统一权限模块 ``permission.check`` 判断当前
用户对资源 ``risk_control`` 的对应操作（view / update）是否被授权；未授权时
返回 ``success=false``、``message``「无访问权限」的统一响应体（HTTP 恒 200）。

接口约定（开发规范 1-3）：
- 所有接口 HTTP 恒返回 200，业务成败由统一响应体 {code, success, message,
  data} 表达。
- 业务逻辑委托 app.services.risk_control_service，路由层仅负责入参解析、鉴权
  依赖与权限判断；数据库会话经 common.db.session.get_db 注入。
- 数据范围隔离（非管理员仅可操作本人店铺数据）在服务层统一处理（需求 3.7）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import permission
from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import risk_control_service
from common.db.session import get_db
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response

# 风控规则配置路由：标签「风控管理」便于 OpenAPI 文档分组；前缀由聚合层添加。
router = APIRouter(tags=["风控管理"])

# 受保护资源键：风控为「店铺级设置」，统一归属店铺管理（shop）资源判权——
# 入口收敛到店铺管理页后，有店铺管理权限即可操作店铺级设置（与前端入口一致）。
RESOURCE_RISK_CONTROL: str = "shop"


def _ensure_permission(
    user: SysUser,
    action: str,
    session: Session,
) -> Optional[ApiResponse]:
    """统一权限校验：未授权时返回「无访问权限」响应，授权时返回 None。

    依据需求 2.4，集中经 ``permission.check`` 判断对 ``risk_control`` 资源的
    操作权限；未授权返回 success=false、message「无访问权限」的统一响应体。

    Args:
        user: 当前登录用户。
        action: 操作（view / update）。
        session: 数据库会话。

    Returns:
        未授权时返回失败响应体；已授权返回 None。
    """
    if permission.check(user, RESOURCE_RISK_CONTROL, action, session=session):
        return None
    return error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)


class RiskRuleRequest(BaseModel):
    """风控规则配置请求体（需求 13.1）。

    各频率上限与统计窗口均为可空的非负整数；为空表示该维度不限制。
    """

    session_reply_limit: Optional[int] = Field(
        None, ge=0, description="单会话窗口内回复次数上限（非负整数，空表示不限制）"
    )
    shop_reply_limit: Optional[int] = Field(
        None, ge=0, description="单店铺窗口内回复次数上限（非负整数，空表示不限制）"
    )
    window_seconds: Optional[int] = Field(
        None, ge=0, description="统计窗口（秒，非负整数，空表示不限制）"
    )
    enabled: bool = Field(True, description="是否启用该风控规则")


@router.put(
    "/shops/{shop_pk}/risk-rule",
    response_model=ApiResponse,
    summary="配置 / 更新店铺风控规则",
)
def configure_risk_rule(
    shop_pk: int,
    payload: RiskRuleRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """配置并持久化店铺风控规则（需求 13.1）。

    按店铺主键 upsert，同一店铺仅一条配置，重复配置覆盖更新（幂等）。
    """
    denied = _ensure_permission(current_user, "update", db)
    if denied is not None:
        return denied
    return risk_control_service.configure_risk_rule(
        db,
        shop_pk=shop_pk,
        session_reply_limit=payload.session_reply_limit,
        shop_reply_limit=payload.shop_reply_limit,
        window_seconds=payload.window_seconds,
        enabled=payload.enabled,
        operator_id=current_user.id,
    )


@router.get(
    "/shops/{shop_pk}/risk-rule",
    response_model=ApiResponse,
    summary="查询店铺风控规则",
)
def get_risk_rule(
    shop_pk: int,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """查询店铺风控规则配置（需求 13.1 配套）。未配置返回 data=null。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return risk_control_service.get_risk_rule(
        db, shop_pk=shop_pk, operator_id=current_user.id
    )


@router.get(
    "/risk-types",
    response_model=ApiResponse,
    summary="查询风控类型枚举（中文文案）",
)
def list_risk_types(
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """查询风控类型枚举字典，供前端中文展示（需求 13.4）。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return risk_control_service.list_risk_types(db)


__all__ = ["router"]

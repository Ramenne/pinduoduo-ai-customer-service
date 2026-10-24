# -*- coding: utf-8 -*-
"""
backend.app.api.routes.logs —— 消息/风控/系统日志查询接口路由
============================================================
本文件用途：提供 backend 服务的「消息日志、风控日志与系统日志」查询 REST 接口，
满足需求 19（消息日志与风控日志）与需求 21.4（系统日志查询）：

- ``GET /message-logs``  消息日志列表（后端分页，按店铺与时间范围筛选，需求 19.1/19.3）。
- ``GET /risk-logs``     风控日志列表（后端分页，按店铺、风控类型与时间范围筛选，需求 19.2/19.3）。
- ``GET /system-logs``   系统日志列表（后端分页，按级别、模块与时间范围筛选，需求 21.4）。

**禁止删除日志数据**（需求 19.5 / 规范 11）：本路由仅提供查询接口，不提供任何
删除日志的接口。日志记录由消息处理链 / 各业务服务经 log_service 写入。

权限控制（需求 2.4）：所有接口先经统一权限模块 ``permission.check`` 判断当前用户
对相应资源（message_log / risk_log / system_log）的 view 操作是否被授权；未授权
返回 success=false、message「无访问权限」的统一响应体（HTTP 恒 200）。消息 / 风控
日志的店铺归属数据范围隔离在服务层统一处理（需求 3.7）；系统日志为全局日志，
仅授权用户可查看。

接口约定（开发规范 1-3）：所有接口 HTTP 恒返回 200，业务成败由统一响应体
{code, success, message, data} 表达；业务逻辑委托 app.services.log_service。
时间范围筛选按北京时间口径（需求 24.8），支持 "YYYY-MM-DD" 或
"YYYY-MM-DD HH:MM:SS" 字符串。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import permission
from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import log_service
from common.db.session import get_db
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response

# 日志查询路由：标签便于 OpenAPI 文档分组；前缀由聚合层添加。
router = APIRouter(tags=["日志查询"])

# 受保护资源键约定（与 sys_permission.resource_key 对齐）。
RESOURCE_MESSAGE_LOG: str = "message_log"
RESOURCE_RISK_LOG: str = "risk_log"
RESOURCE_SYSTEM_LOG: str = "system_log"


def _ensure_permission(
    user: SysUser, resource_key: str, action: str, session: Session
) -> Optional[ApiResponse]:
    """统一权限校验：未授权时返回「无访问权限」响应，授权时返回 None。

    Args:
        user: 当前登录用户。
        resource_key: 受保护资源键（message_log / risk_log / system_log）。
        action: 操作（view）。
        session: 数据库会话。

    Returns:
        未授权时返回失败响应体；已授权返回 None。
    """
    if permission.check(user, resource_key, action, session=session):
        return None
    return error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)


@router.get("/message-logs", response_model=ApiResponse, summary="消息日志列表（后端分页）")
def list_message_logs(
    shop_pk: Optional[int] = Query(None, description="按店铺主键 shop.id 筛选"),
    start_time: Optional[str] = Query(
        None, description="起始时间（北京时间，YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS）"
    ),
    end_time: Optional[str] = Query(
        None, description="结束时间（北京时间，YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS）"
    ),
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """分页查询消息日志，支持按店铺与时间范围筛选（需求 19.1/19.3）。"""
    denied = _ensure_permission(current_user, RESOURCE_MESSAGE_LOG, "view", db)
    if denied is not None:
        return denied
    return log_service.list_message_logs(
        db,
        current_user,
        shop_pk=shop_pk,
        start_time=start_time,
        end_time=end_time,
        page=page,
        page_size=page_size,
    )


@router.get("/risk-logs", response_model=ApiResponse, summary="风控日志列表（后端分页）")
def list_risk_logs(
    shop_pk: Optional[int] = Query(None, description="按店铺主键 shop.id 筛选"),
    risk_type: Optional[str] = Query(None, description="按风控类型枚举键筛选"),
    start_time: Optional[str] = Query(
        None, description="起始时间（北京时间，YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS）"
    ),
    end_time: Optional[str] = Query(
        None, description="结束时间（北京时间，YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS）"
    ),
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """分页查询风控日志，支持按店铺、风控类型与时间范围筛选（需求 19.2/19.3）。"""
    denied = _ensure_permission(current_user, RESOURCE_RISK_LOG, "view", db)
    if denied is not None:
        return denied
    return log_service.list_risk_logs(
        db,
        current_user,
        shop_pk=shop_pk,
        risk_type=risk_type,
        start_time=start_time,
        end_time=end_time,
        page=page,
        page_size=page_size,
    )


@router.get("/system-logs", response_model=ApiResponse, summary="系统日志列表（后端分页）")
def list_system_logs(
    level: Optional[str] = Query(None, description="按日志级别筛选：info/warning/error"),
    module: Optional[str] = Query(None, description="按来源模块筛选"),
    start_time: Optional[str] = Query(
        None, description="起始时间（北京时间，YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS）"
    ),
    end_time: Optional[str] = Query(
        None, description="结束时间（北京时间，YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS）"
    ),
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """分页查询系统日志，支持按级别、模块与时间范围筛选（需求 21.4）。"""
    denied = _ensure_permission(current_user, RESOURCE_SYSTEM_LOG, "view", db)
    if denied is not None:
        return denied
    return log_service.list_system_logs(
        db,
        level=level,
        module=module,
        start_time=start_time,
        end_time=end_time,
        page=page,
        page_size=page_size,
    )


__all__ = ["router"]

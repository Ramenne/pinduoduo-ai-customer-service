# -*- coding: utf-8 -*-
"""
backend.app.services.log_service —— 消息/风控/系统日志业务服务
==============================================================
本文件用途：实现 backend 服务的「消息日志、风控日志与系统日志」记录与查询
业务逻辑，供 logs 路由复用，满足需求 19（消息日志与风控日志）与需求 21.4
（系统日志查询）：

- ``record_message_log(...)``：记录一条消息处理日志（店铺、客户、消息内容、
  处理结果、回复内容、北京时间，需求 19.1）。
- ``record_risk_log(...)``：记录一条风控日志（店铺、风控类型、触发原因、北京
  时间，需求 19.2）。
- ``record_system_log(...)``：记录一条系统日志（级别、模块、内容、北京时间）。
- ``list_message_logs(...)``：消息日志后端分页查询，支持按店铺与时间范围筛选
  （需求 19.3），并做数据范围隔离（非管理员仅见本人 / 被授权店铺日志，需求 3.7）。
- ``list_risk_logs(...)``：风控日志后端分页查询，支持按店铺与时间范围筛选
  （需求 19.3），同样做数据范围隔离。
- ``list_system_logs(...)``：系统日志后端分页查询，支持按级别、模块与时间范围
  筛选（需求 21.4）；系统日志为全局日志，仅授权用户（路由层经 permission.check
  控制）可查看，不按店铺归属隔离。

关键约束（开发规范 / 需求）：
- 统一响应体由 common.schemas.common 构造，HTTP 恒 200（规范 1-3 / 需求 24.1）。
- 所有数据访问经 common.db.repository 参数化查询（含时间范围 / IN 条件），禁止
  拼接 SQL（规范 16 / 需求 24.4）。
- **禁止删除日志数据**（需求 19.5 / 规范 11）：本服务仅提供记录与查询，不提供
  任何删除（含逻辑删除）日志的接口 / 方法。
- 全部日志时间字段统一北京时间（规范 17 / 需求 24.8）：记录时取
  ``now_beijing_naive()``，查询筛选按北京时间字符串解析。
- 后端分页（默认 20，可选 10/20/50/100，规范 28 / 需求 19.3）。
- 数据范围隔离统一经 app.core.data_scope（规范 42 / 需求 3.7）。
- 处理结果 / 风控类型枚举入字典，前端展示中文（需求 19.1 / 13.4 / 24.7）。
- 导入置顶（规范 51）；中文注释（规范 37）；单文件 ≤500 行（规范 35）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.business_codes import CODE_PARAM_ERROR
from app.core.data_scope import DataScope, build_data_scope
from common.db.repository import Repository
from common.models.log_models import MessageLog, RiskLog, SystemLog
from common.models.shop_models import Shop
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response, success_response
from common.utils.time_utils import (
    BEIJING_TZ,
    now_beijing_naive,
    parse_beijing,
    safe_isoformat,
)
from common.utils.pagination import normalize_pagination

# 系统日志合法级别（禁止 debug，规范 38）。
ALLOWED_LOG_LEVELS: tuple[str, ...] = ("info", "warning", "error")

# 时间范围筛选支持的输入格式（北京时间）：日期或日期时间。
_DATE_FORMAT: str = "%Y-%m-%d"
_DATETIME_FORMAT: str = "%Y-%m-%d %H:%M:%S"


# ----------------------------------------------------------------------
# 时间范围解析辅助（按北京时间口径，需求 24.8）
# ----------------------------------------------------------------------
def _parse_beijing_time(value: Any) -> Optional[datetime]:
    """将时间范围筛选入参解析为「去时区的北京时间」naive datetime。

    支持三种入参：
    - ``datetime``：带时区者换算到北京时间后去时区；naive 者按北京时间直接使用；
    - ``"%Y-%m-%d %H:%M:%S"`` 字符串：按北京时间解析；
    - ``"%Y-%m-%d"`` 字符串：按北京时间当日零点解析。

    解析失败返回 None（调用方据此判定入参非法）。日志时间字段以 naive 北京时间
    存储，故统一转为 naive 北京时间用于比较。

    Args:
        value: 时间范围筛选入参（datetime / 字符串 / None）。

    Returns:
        去时区的北京时间 ``datetime``；无法解析时返回 None。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(BEIJING_TZ).replace(tzinfo=None)
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        for fmt in (_DATETIME_FORMAT, _DATE_FORMAT):
            try:
                return parse_beijing(text, fmt).replace(tzinfo=None)
            except ValueError:
                continue
    return None


def _build_time_conditions(
    time_column: Any,
    start_time: Any,
    end_time: Any,
) -> tuple[Optional[List[Any]], Optional[ApiResponse]]:
    """构造按北京时间范围筛选的参数化条件列表（需求 19.3）。

    起 / 止时间均为可选；任一提供且无法解析则返回入参错误响应。返回的条件列表
    可直接传入 ``Repository.paginate`` 的 ``extra_conditions``，由 SQLAlchemy 生成
    参数化语句（规范 16）。

    Args:
        time_column: 目标日志模型的时间列（如 ``MessageLog.log_time``）。
        start_time: 起始时间（含）；None 表示不限制。
        end_time: 结束时间（含）；None 表示不限制。

    Returns:
        二元组 (条件列表, 失败响应)：解析成功返回 (conditions, None)；任一时间
        非法返回 (None, 失败响应体)。
    """
    conditions: List[Any] = []
    if start_time is not None:
        start_dt = _parse_beijing_time(start_time)
        if start_dt is None:
            return None, error_response(CODE_PARAM_ERROR, "起始时间格式非法")
        conditions.append(time_column >= start_dt)
    if end_time is not None:
        end_dt = _parse_beijing_time(end_time)
        if end_dt is None:
            return None, error_response(CODE_PARAM_ERROR, "结束时间格式非法")
        conditions.append(time_column <= end_dt)
    return conditions, None


# ----------------------------------------------------------------------
# 数据范围隔离辅助（按店铺归属，需求 3.7）
# ----------------------------------------------------------------------
def _visible_shop_pks(session: Session, scope: DataScope) -> Optional[List[int]]:
    """计算当前数据范围内可见的店铺主键列表（用于日志按店铺归属隔离）。

    - 管理员：返回 None，表示不附加店铺归属限制（可见全部日志）；
    - 非管理员：返回其本人创建（或被授权）店铺的主键列表（可能为空列表，表示
      不可见任何店铺日志）。

    日志表（message_log / risk_log）以 ``shop_pk`` 关联店铺（普通列，无外键），
    故据可见店铺集合下推 IN 条件实现隔离（规范 16 参数化）。

    Args:
        session: 数据库会话。
        scope: 当前用户数据范围。

    Returns:
        管理员返回 None；非管理员返回可见店铺主键列表。
    """
    if scope.is_admin:
        return None
    allowed_owners = scope.allowed_owner_ids() or frozenset()
    if not allowed_owners:
        return []
    shops = Repository(Shop, session).list(
        order_by=False,
        extra_conditions=[Shop.owner_user_id.in_(sorted(allowed_owners))],
    )
    return [shop.id for shop in shops]


# ----------------------------------------------------------------------
# 序列化
# ----------------------------------------------------------------------
def serialize_message_log(record: MessageLog) -> Dict[str, Any]:
    """将消息日志模型序列化为对外字典（时间为北京时间 ISO 串）。"""
    return {
        "id": record.id,
        "shop_pk": record.shop_pk,
        "customer_uid": record.customer_uid,
        "message_content": record.message_content,
        "process_result": record.process_result,
        "reply_content": record.reply_content,
        "log_time": safe_isoformat(record.log_time),
    }


def serialize_risk_log(record: RiskLog) -> Dict[str, Any]:
    """将风控日志模型序列化为对外字典（时间为北京时间 ISO 串）。"""
    return {
        "id": record.id,
        "shop_pk": record.shop_pk,
        "risk_type": record.risk_type,
        "trigger_reason": record.trigger_reason,
        "log_time": safe_isoformat(record.log_time),
    }


def serialize_system_log(record: SystemLog) -> Dict[str, Any]:
    """将系统日志模型序列化为对外字典（时间为北京时间 ISO 串）。"""
    return {
        "id": record.id,
        "level": record.level,
        "module": record.module,
        "content": record.content,
        "log_time": safe_isoformat(record.log_time),
    }


# ----------------------------------------------------------------------
# 记录日志（需求 19.1 / 19.2；时间统一北京时间，禁止删除）
# ----------------------------------------------------------------------
def record_message_log(
    session: Session,
    shop_pk: int,
    *,
    customer_uid: Optional[str] = None,
    message_content: Optional[str] = None,
    process_result: Optional[str] = None,
    reply_content: Optional[str] = None,
    operator_id: Optional[int] = None,
) -> MessageLog:
    """记录一条消息处理日志（需求 19.1）。

    记录店铺、客户、消息内容、处理结果、回复内容与北京时间。该方法供消息处理
    链 / 在线聊天手动发送等场景复用，仅写入不删除（需求 19.5）。

    Args:
        session: 数据库会话。
        shop_pk: 关联店铺主键 shop.id。
        customer_uid: 客户唯一标识。
        message_content: 原始消息内容。
        process_result: 处理结果枚举键（见字典 process_result）。
        reply_content: 回复内容。
        operator_id: 创建人用户 ID（手动发送等场景记录操作人）。

    Returns:
        已持久化的消息日志模型实例。
    """
    return Repository(MessageLog, session).create(
        shop_pk=shop_pk,
        customer_uid=customer_uid,
        message_content=message_content,
        process_result=process_result,
        reply_content=reply_content,
        log_time=now_beijing_naive(),
        created_by=operator_id,
    )


def record_risk_log(
    session: Session,
    shop_pk: int,
    risk_type: str,
    *,
    trigger_reason: Optional[str] = None,
    operator_id: Optional[int] = None,
) -> RiskLog:
    """记录一条风控日志（需求 19.2）。

    记录店铺、风控类型、触发原因与北京时间。仅写入不删除（需求 19.5）。

    Args:
        session: 数据库会话。
        shop_pk: 关联店铺主键 shop.id。
        risk_type: 风控类型枚举键（见字典 risk_type）。
        trigger_reason: 触发原因（中文）。
        operator_id: 创建人用户 ID（可空）。

    Returns:
        已持久化的风控日志模型实例。
    """
    return Repository(RiskLog, session).create(
        shop_pk=shop_pk,
        risk_type=risk_type,
        trigger_reason=trigger_reason,
        log_time=now_beijing_naive(),
        created_by=operator_id,
    )


def record_system_log(
    session: Session,
    level: str,
    content: str,
    *,
    module: Optional[str] = None,
    operator_id: Optional[int] = None,
) -> SystemLog:
    """记录一条系统日志（级别 / 模块 / 内容 / 北京时间）。

    级别非法时回退为 ``info``（禁止 debug，规范 38）。仅写入不删除。

    Args:
        session: 数据库会话。
        level: 日志级别（info/warning/error）。
        content: 日志内容（中文）。
        module: 来源模块标识。
        operator_id: 创建人用户 ID（可空）。

    Returns:
        已持久化的系统日志模型实例。
    """
    safe_level = level if level in ALLOWED_LOG_LEVELS else "info"
    return Repository(SystemLog, session).create(
        level=safe_level,
        module=module,
        content=content,
        log_time=now_beijing_naive(),
        created_by=operator_id,
    )


# ----------------------------------------------------------------------
# 查询：消息日志（后端分页 + 店铺/时间范围筛选 + 数据范围隔离，需求 19.3/3.7）
# ----------------------------------------------------------------------
def list_message_logs(
    session: Session,
    user: SysUser,
    *,
    shop_pk: Optional[int] = None,
    start_time: Any = None,
    end_time: Any = None,
    page: Any = 1,
    page_size: Any = 20,
) -> ApiResponse:
    """分页查询消息日志（需求 19.3）。

    支持按店铺与北京时间范围筛选，按日志时间倒序、后端分页。非管理员仅可见本人
    创建（或被授权）店铺的日志（需求 3.7）。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        shop_pk: 按店铺主键筛选；None 表示按可见范围全部店铺。
        start_time: 起始时间（含，北京时间）；None 表示不限制。
        end_time: 结束时间（含，北京时间）；None 表示不限制。
        page: 页码（从 1 开始，将被规整）。
        page_size: 每页条数（10/20/50/100，将被规整）。

    Returns:
        统一响应体：data 为分页结构 {list, total, page, page_size}。
    """
    conditions, denied = _build_time_conditions(
        MessageLog.log_time, start_time, end_time
    )
    if denied is not None:
        return denied

    scope_conditions = _resolve_shop_scope_conditions(
        session, user, MessageLog.shop_pk, shop_pk
    )
    if scope_conditions is None:
        # 不可见任何店铺日志：直接返回空分页（避免越权）。
        return _empty_page_response(page, page_size)
    conditions.extend(scope_conditions)

    page_result = Repository(MessageLog, session).paginate(
        page=page,
        page_size=page_size,
        extra_conditions=conditions or None,
    )
    serialized = [serialize_message_log(record) for record in page_result.items]
    return _page_response(serialized, page_result)


# ----------------------------------------------------------------------
# 查询：风控日志（后端分页 + 店铺/时间范围筛选 + 数据范围隔离，需求 19.3/3.7）
# ----------------------------------------------------------------------
def list_risk_logs(
    session: Session,
    user: SysUser,
    *,
    shop_pk: Optional[int] = None,
    risk_type: Optional[str] = None,
    start_time: Any = None,
    end_time: Any = None,
    page: Any = 1,
    page_size: Any = 20,
) -> ApiResponse:
    """分页查询风控日志（需求 19.3）。

    支持按店铺、风控类型与北京时间范围筛选，按日志时间倒序、后端分页。非管理员
    仅可见本人创建（或被授权）店铺的日志（需求 3.7）。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        shop_pk: 按店铺主键筛选；None 表示按可见范围全部店铺。
        risk_type: 按风控类型枚举键筛选；None 表示不筛选。
        start_time: 起始时间（含，北京时间）；None 表示不限制。
        end_time: 结束时间（含，北京时间）；None 表示不限制。
        page: 页码（从 1 开始，将被规整）。
        page_size: 每页条数（10/20/50/100，将被规整）。

    Returns:
        统一响应体：data 为分页结构 {list, total, page, page_size}。
    """
    conditions, denied = _build_time_conditions(
        RiskLog.log_time, start_time, end_time
    )
    if denied is not None:
        return denied

    scope_conditions = _resolve_shop_scope_conditions(
        session, user, RiskLog.shop_pk, shop_pk
    )
    if scope_conditions is None:
        return _empty_page_response(page, page_size)
    conditions.extend(scope_conditions)

    filters: Dict[str, Any] = {}
    if risk_type is not None and str(risk_type).strip():
        filters["risk_type"] = str(risk_type).strip()

    page_result = Repository(RiskLog, session).paginate(
        page=page,
        page_size=page_size,
        filters=filters or None,
        extra_conditions=conditions or None,
    )
    serialized = [serialize_risk_log(record) for record in page_result.items]
    return _page_response(serialized, page_result)


# ----------------------------------------------------------------------
# 查询：系统日志（后端分页 + 级别/模块/时间范围筛选，需求 21.4）
# ----------------------------------------------------------------------
def list_system_logs(
    session: Session,
    *,
    level: Optional[str] = None,
    module: Optional[str] = None,
    start_time: Any = None,
    end_time: Any = None,
    page: Any = 1,
    page_size: Any = 20,
) -> ApiResponse:
    """分页查询系统日志（需求 21.4）。

    支持按级别、模块与北京时间范围筛选，按日志时间倒序、后端分页。系统日志为
    全局日志（不按店铺归属隔离），可见性由路由层经 permission.check 控制。

    Args:
        session: 数据库会话。
        level: 按日志级别筛选（info/warning/error）；None 表示不筛选。
        module: 按来源模块筛选；None 表示不筛选。
        start_time: 起始时间（含，北京时间）；None 表示不限制。
        end_time: 结束时间（含，北京时间）；None 表示不限制。
        page: 页码（从 1 开始，将被规整）。
        page_size: 每页条数（10/20/50/100，将被规整）。

    Returns:
        统一响应体：data 为分页结构 {list, total, page, page_size}。
    """
    conditions, denied = _build_time_conditions(
        SystemLog.log_time, start_time, end_time
    )
    if denied is not None:
        return denied

    filters: Dict[str, Any] = {}
    if level is not None and str(level).strip():
        filters["level"] = str(level).strip()
    if module is not None and str(module).strip():
        filters["module"] = str(module).strip()

    page_result = Repository(SystemLog, session).paginate(
        page=page,
        page_size=page_size,
        filters=filters or None,
        extra_conditions=conditions or None,
    )
    serialized = [serialize_system_log(record) for record in page_result.items]
    return _page_response(serialized, page_result)


# ----------------------------------------------------------------------
# 内部辅助：店铺范围条件 + 分页响应封装
# ----------------------------------------------------------------------
def _resolve_shop_scope_conditions(
    session: Session,
    user: SysUser,
    shop_column: Any,
    shop_pk: Optional[int],
) -> Optional[List[Any]]:
    """据数据范围与显式店铺筛选，构造按店铺主键的参数化条件列表（需求 3.7）。

    - 管理员：若指定 shop_pk 则按其等值筛选，否则无店铺限制（返回空条件列表）；
    - 非管理员：先取可见店铺集合；若显式指定 shop_pk 且不在可见集合内，返回
      None（表示越权，调用方据此返回空分页）；否则按「指定店铺」或「可见店铺
      集合 IN 条件」下推。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        shop_column: 日志模型的 shop_pk 列。
        shop_pk: 显式店铺筛选；None 表示不指定。

    Returns:
        条件列表（可能为空）；当非管理员越权访问指定店铺时返回 None。
    """
    scope = build_data_scope(user, session=session)
    visible = _visible_shop_pks(session, scope)

    if visible is None:
        # 管理员：可选按指定店铺等值筛选。
        if shop_pk is not None:
            return [shop_column == int(shop_pk)]
        return []

    # 非管理员：限定在可见店铺集合内。
    if shop_pk is not None:
        if int(shop_pk) not in visible:
            return None
        return [shop_column == int(shop_pk)]
    # 未指定店铺：按可见店铺集合 IN 下推（空集时返回 None 交由调用方空分页）。
    if not visible:
        return None
    return [shop_column.in_(visible)]


def _page_response(serialized: List[Dict[str, Any]], page_result: Any) -> ApiResponse:
    """据序列化列表与分页结果构造统一分页响应体。"""
    data = {
        "list": serialized,
        "total": page_result.total,
        "page": page_result.page,
        "page_size": page_result.page_size,
    }
    return success_response(data=data, message="查询成功")


def _empty_page_response(page: Any, page_size: Any) -> ApiResponse:
    """构造「无可见数据」的空分页响应体（规整页码与每页条数）。"""
    norm_page, norm_size = normalize_pagination(page, page_size)
    data = {"list": [], "total": 0, "page": norm_page, "page_size": norm_size}
    return success_response(data=data, message="查询成功")


__all__ = [
    "ALLOWED_LOG_LEVELS",
    "serialize_message_log",
    "serialize_risk_log",
    "serialize_system_log",
    "record_message_log",
    "record_risk_log",
    "record_system_log",
    "list_message_logs",
    "list_risk_logs",
    "list_system_logs",
]

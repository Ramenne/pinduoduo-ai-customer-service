# -*- coding: utf-8 -*-
"""
backend.app.services.risk_control_service —— 风控规则配置业务服务
================================================================
本文件用途：实现 backend 服务的「风控规则配置」业务逻辑，供 risk_control
路由复用，满足需求 13（风控管理）：

- ``configure_risk_rule(...)``：配置店铺风控规则（单会话/单店铺回复频率上限、
  统计窗口）并持久化（需求 13.1）。按店铺主键 ``shop_pk`` 作为业务键 upsert
  （同一店铺仅一条风控规则，重复配置覆盖更新，幂等），返回统一响应体。
- ``get_risk_rule(...)``：查询某店铺的风控规则配置；未配置时返回 data=None。
- ``list_risk_types(...)``：查询「风控类型」枚举字典（key->中文标签），供前端
  中文展示（需求 13.4：风控类型枚举入数据字典表并展示中文文案）。

风控规则模型说明（common.models.config_models.RiskRule）：
- ``session_reply_limit`` / ``shop_reply_limit``：单会话 / 单店铺在统计窗口内
  的回复次数上限（需求 13.2，运行时判定在 websocket 引擎执行）；
- ``window_seconds``：统计窗口（秒）；
- ``enabled``：该风控规则是否启用；
- 三个数值字段均可为空，表示该维度不限制。

实现约束（开发规范）：
- 统一响应体由 common.schemas.common 构造，HTTP 恒 200（规范 1-3 / 需求 24.1）。
- 所有数据访问经 common.db.repository 的参数化查询，禁止拼接 SQL（规范 16）。
- 风控类型枚举入数据字典 ``risk_type``，前端展示中文（规范 15 / 需求 13.4）。
- 数据范围隔离：非管理员仅能操作 / 查看其本人创建（或被授权）店铺下的风控
  规则，复用 app.core.data_scope（需求 3.7）。
- 禁止物理删除业务数据；配置变更经 upsert 覆盖更新（规范 11 / 需求 24.6）。
- 时间字段统一北京时间（规范 17 / 需求 24.8）。
- 导入置顶（规范 51）；中文注释（规范 37）；单文件 ≤500 行（规范 35）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.business_codes import (
    CODE_FORBIDDEN,
    CODE_NOT_FOUND,
    CODE_PARAM_ERROR,
    MSG_FORBIDDEN,
)
from app.core.data_scope import DataScope, build_data_scope, is_in_scope
from common.db.repository import Repository
from common.models.config_models import RiskRule
from common.models.shop_models import Shop
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response, success_response
from common.services.dict_service import DictService
from common.utils.time_utils import safe_isoformat

# 风控类型字典分组键（见 dict_seed_data 的 risk_type，需求 13.4）。
DICT_RISK_TYPE: str = "risk_type"


# ----------------------------------------------------------------------
# 序列化辅助
# ----------------------------------------------------------------------
def serialize_risk_rule(record: RiskRule) -> Dict[str, Any]:
    """将风控规则配置模型序列化为对外字典（时间为北京时间 ISO 串）。

    Args:
        record: 风控规则模型实例。

    Returns:
        风控规则信息字典。
    """
    return {
        "id": record.id,
        "shop_pk": record.shop_pk,
        "session_reply_limit": record.session_reply_limit,
        "shop_reply_limit": record.shop_reply_limit,
        "window_seconds": record.window_seconds,
        "enabled": bool(record.enabled),
        "created_at": safe_isoformat(record.created_at),
        "updated_at": safe_isoformat(record.updated_at),
    }


# ----------------------------------------------------------------------
# 内部辅助：数据范围隔离（需求 3.7）
# ----------------------------------------------------------------------
def _load_scope(session: Session, operator_id: Optional[int]) -> DataScope:
    """据操作用户 ID 装配数据范围 ``DataScope``（经统一权限模块解析 is_admin）。

    Args:
        session: 数据库会话。
        operator_id: 当前操作用户 ID；None 视为无身份。

    Returns:
        装配完成的数据范围。
    """
    if operator_id is None:
        return DataScope(is_admin=False, user_id=None)
    user = Repository(SysUser, session).get(operator_id)
    return build_data_scope(user, session=session)


def _ensure_shop_in_scope(
    session: Session,
    shop_pk: int,
    operator_id: Optional[int],
) -> Optional[ApiResponse]:
    """校验目标店铺存在且当前用户对其有数据访问权限（需求 3.7）。

    管理员不受限；非管理员仅可操作本人创建（或被授权）店铺下的数据。校验失败
    时返回对应失败响应体；通过返回 None。

    Args:
        session: 数据库会话。
        shop_pk: 目标店铺主键（shop.id）。
        operator_id: 当前操作用户 ID。

    Returns:
        校验未通过时返回失败响应体；通过返回 None。
    """
    shop = Repository(Shop, session).get(shop_pk)
    if shop is None:
        return error_response(CODE_NOT_FOUND, "目标店铺不存在")

    scope = _load_scope(session, operator_id)
    if not is_in_scope(scope, shop.owner_user_id):
        return error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)
    return None


def _parse_optional_non_negative_int(
    value: Any,
) -> tuple[bool, Optional[int]]:
    """将入参解析为「可空的非负整数」（频率上限 / 窗口秒数）。

    None 表示该维度不限制（合法，返回 (True, None)）；负数或非整数非法。

    Args:
        value: 待解析的值（int / None）。

    Returns:
        二元组 (是否合法, 解析结果)。合法且为空返回 (True, None)；合法且有值
        返回 (True, int)；非法返回 (False, None)。
    """
    if value is None:
        return True, None
    # 布尔是 int 子类，需排除，避免 True/False 被当作 1/0。
    if isinstance(value, bool) or not isinstance(value, int):
        return False, None
    if value < 0:
        return False, None
    return True, value


# ----------------------------------------------------------------------
# 风控规则配置（需求 13.1）
# ----------------------------------------------------------------------
def configure_risk_rule(
    session: Session,
    shop_pk: int,
    *,
    session_reply_limit: Any = None,
    shop_reply_limit: Any = None,
    window_seconds: Any = None,
    enabled: bool = True,
    operator_id: Optional[int] = None,
) -> ApiResponse:
    """配置并持久化店铺风控规则（需求 13.1）。

    按店铺主键 ``shop_pk`` 作为业务键 upsert：同一店铺仅保留一条风控规则，重复
    配置覆盖更新（幂等）。各频率上限与统计窗口均为可空的非负整数（为空表示该
    维度不限制）。

    Args:
        session: 数据库会话。
        shop_pk: 店铺主键（shop.id）。
        session_reply_limit: 单会话窗口内回复次数上限（非负整数或为空）。
        shop_reply_limit: 单店铺窗口内回复次数上限（非负整数或为空）。
        window_seconds: 统计窗口（秒，非负整数或为空）。
        enabled: 该风控规则是否启用，默认启用。
        operator_id: 操作人用户 ID，作为创建人审计字段（仅新建时记录）+ 数据范围。

    Returns:
        统一响应体：成功返回 data=风控规则；失败返回对应中文提示。
    """
    # 入参校验：店铺主键必填且为正整数。
    if shop_pk is None or not isinstance(shop_pk, int) or shop_pk <= 0:
        return error_response(CODE_PARAM_ERROR, "店铺主键不能为空")

    # 数据范围隔离：店铺需存在且在当前用户可见范围内（需求 3.7）。
    denied = _ensure_shop_in_scope(session, shop_pk, operator_id)
    if denied is not None:
        return denied

    # 解析三个可空非负整数字段，任一非法即返回明确中文提示。
    session_ok, session_value = _parse_optional_non_negative_int(session_reply_limit)
    if not session_ok:
        return error_response(CODE_PARAM_ERROR, "单会话回复上限须为非负整数")
    shop_ok, shop_value = _parse_optional_non_negative_int(shop_reply_limit)
    if not shop_ok:
        return error_response(CODE_PARAM_ERROR, "单店铺回复上限须为非负整数")
    window_ok, window_value = _parse_optional_non_negative_int(window_seconds)
    if not window_ok:
        return error_response(CODE_PARAM_ERROR, "统计窗口秒数须为非负整数")

    repo = Repository(RiskRule, session)
    # 按 shop_pk upsert：存在则更新各字段，不存在则新建。
    existing = repo.get_by(shop_pk=shop_pk)
    if existing is None:
        record = repo.create(
            shop_pk=shop_pk,
            session_reply_limit=session_value,
            shop_reply_limit=shop_value,
            window_seconds=window_value,
            enabled=bool(enabled),
            created_by=operator_id,
        )
    else:
        record = repo.update(
            existing.id,
            session_reply_limit=session_value,
            shop_reply_limit=shop_value,
            window_seconds=window_value,
            enabled=bool(enabled),
        )

    return success_response(
        data=serialize_risk_rule(record),
        message="风控规则已保存",
    )


def get_risk_rule(
    session: Session,
    shop_pk: int,
    *,
    operator_id: Optional[int] = None,
) -> ApiResponse:
    """查询某店铺的风控规则配置（需求 13.1 配套）。

    未配置时返回 ``data=None``，便于前端区分「未配置」与「已配置」。非管理员
    仅可查看其可见范围内店铺的配置（需求 3.7）。

    Args:
        session: 数据库会话。
        shop_pk: 店铺主键（shop.id）。
        operator_id: 当前操作用户 ID（数据范围校验）。

    Returns:
        统一响应体：已配置返回风控规则；未配置返回 data=None。
    """
    if shop_pk is None or not isinstance(shop_pk, int) or shop_pk <= 0:
        return error_response(CODE_PARAM_ERROR, "店铺主键不能为空")

    denied = _ensure_shop_in_scope(session, shop_pk, operator_id)
    if denied is not None:
        return denied

    record = Repository(RiskRule, session).get_by(shop_pk=shop_pk)
    if record is None:
        return success_response(data=None, message="未配置风控规则")
    return success_response(
        data=serialize_risk_rule(record),
        message="查询成功",
    )


# ----------------------------------------------------------------------
# 风控类型字典（需求 13.4：枚举入字典并前端中文展示）
# ----------------------------------------------------------------------
def list_risk_types(session: Session) -> ApiResponse:
    """查询「风控类型」枚举字典，供前端展示中文文案（需求 13.4）。

    从数据字典 ``risk_type`` 查出启用项（按 order_no 升序），返回 key 与中文
    标签列表，前端据此展示风控类型中文文案。

    Args:
        session: 数据库会话。

    Returns:
        统一响应体：data 为风控类型列表 [{key, label}, ...]。
    """
    items = DictService(session).list_by_type(DICT_RISK_TYPE)
    risk_types: List[Dict[str, Any]] = [
        {"key": item.dict_key, "label": item.dict_label} for item in items
    ]
    return success_response(data=risk_types, message="查询成功")


__all__ = [
    "DICT_RISK_TYPE",
    "serialize_risk_rule",
    "configure_risk_rule",
    "get_risk_rule",
    "list_risk_types",
]

# -*- coding: utf-8 -*-
"""
backend.app.services.filter_service —— 消息过滤与黑名单业务服务
==============================================================
本文件用途：实现 backend 服务的「消息过滤规则」与「黑名单」业务逻辑，供
message_filters / blacklist 路由复用，满足需求 12（消息过滤与黑名单）：

- 过滤规则：
  * ``create_filter_rule(...)``：创建消息过滤规则并持久化（需求 12.1）。
  * ``update_filter_rule(...)``：修改过滤条件类型 / 条件值 / 启停用。
  * ``set_filter_rule_status(...)``：启用 / 停用过滤规则（停用经状态字段
    逻辑删除，禁止物理删除）。
  * ``list_filter_rules(...)``：过滤规则列表后端分页（需求 12.6）。
- 黑名单：
  * ``add_to_blacklist(...)``：将客户加入黑名单（按 (shop_pk, customer_uid)
    业务键 upsert，重复加入幂等并置为有效，需求 12.3）。
  * ``remove_from_blacklist(...)``：将客户移出黑名单——置 ``is_active=False``
    逻辑失效，禁止物理删除数据（需求 12.5）。
  * ``list_blacklist(...)``：黑名单列表后端分页（需求 12.6）。

实现约束（开发规范）：
- 统一响应体由 common.schemas.common 构造，HTTP 恒 200（规范 1-3）。
- 所有数据访问经 common.db.repository 的参数化查询，禁止拼接 SQL（规范 16）。
- 禁止物理删除业务数据：过滤规则停用经 ``enabled`` 字段、黑名单移出经
  ``is_active`` 字段逻辑删除实现（规范 11 / 需求 12.5 / 24.6）。
- 数据范围隔离：非管理员仅能操作 / 查看其本人创建（或被授权）店铺下的过滤
  规则与黑名单，复用 app.core.data_scope（需求 3.7）。
- 过滤条件类型枚举入数据字典（filter_condition），创建 / 修改时校验合法性
  （需求 24.7）。
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
from common.models.config_models import Blacklist, MessageFilterRule
from common.models.shop_models import Shop
from common.schemas.common import ApiResponse, error_response, success_response
from common.services.dict_service import DictService
from common.utils.time_utils import safe_isoformat

# 过滤规则启停用状态值（与 MessageFilterRule.enabled 约定：True=启用）。
# 黑名单有效性（Blacklist.is_active：True=有效，False=移出失效）。
# 过滤条件类型字典分组键（见 dict_seed_data 的 filter_condition）。
DICT_FILTER_CONDITION: str = "filter_condition"


# ----------------------------------------------------------------------
# 序列化辅助
# ----------------------------------------------------------------------
def serialize_filter_rule(rule: MessageFilterRule) -> Dict[str, Any]:
    """将消息过滤规则模型序列化为对外字典（时间为北京时间 ISO 串）。

    Args:
        rule: 过滤规则模型实例。

    Returns:
        过滤规则信息字典。
    """
    return {
        "id": rule.id,
        "shop_pk": rule.shop_pk,
        "condition_type": rule.condition_type,
        "condition_value": rule.condition_value,
        "enabled": bool(rule.enabled),
        "created_at": safe_isoformat(rule.created_at),
        "updated_at": safe_isoformat(rule.updated_at),
    }


def serialize_blacklist(item: Blacklist) -> Dict[str, Any]:
    """将黑名单记录模型序列化为对外字典（时间为北京时间 ISO 串）。

    Args:
        item: 黑名单模型实例。

    Returns:
        黑名单信息字典（含 ``is_active`` 表示是否有效）。
    """
    return {
        "id": item.id,
        "shop_pk": item.shop_pk,
        "customer_uid": item.customer_uid,
        "is_active": bool(item.is_active),
        "created_at": safe_isoformat(item.created_at),
        "updated_at": safe_isoformat(item.updated_at),
    }


# ----------------------------------------------------------------------
# 内部辅助：店铺归属校验（数据范围隔离，需求 3.7）
# ----------------------------------------------------------------------
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

    # 据当前用户装配数据范围，再判定该店铺归属是否在可见范围内。
    scope = _load_scope(session, operator_id)
    if not is_in_scope(scope, shop.owner_user_id):
        return error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)
    return None


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
    from common.models.user_models import SysUser

    user = Repository(SysUser, session).get(operator_id)
    return build_data_scope(user, session=session)


def _accessible_shop_pks(
    session: Session, operator_id: Optional[int]
) -> Optional[List[int]]:
    """返回当前用户可访问的店铺主键列表；管理员返回 None（不受限）。

    供列表查询做数据范围隔离：非管理员仅可见其本人创建（或被授权）店铺下的
    过滤规则 / 黑名单（需求 3.7）。

    Args:
        session: 数据库会话。
        operator_id: 当前操作用户 ID。

    Returns:
        管理员返回 None；非管理员返回可访问店铺主键列表（可能为空列表）。
    """
    scope = _load_scope(session, operator_id)
    allowed_owners = scope.allowed_owner_ids()
    if allowed_owners is None:
        # 管理员：不受店铺归属限制
        return None
    # 非管理员：取这些归属用户名下的全部店铺主键（单次 IN 查询，避免 N+1）。
    shop_repo = Repository(Shop, session)
    owner_ids = sorted(allowed_owners)
    if not owner_ids:
        return []
    shops = shop_repo.list(
        order_by=False,
        extra_conditions=[Shop.owner_user_id.in_(owner_ids)],
    )
    return [shop.id for shop in shops]


def _validate_condition_type(session: Session, condition_type: str) -> bool:
    """校验过滤条件类型是否为数据字典登记的合法枚举（需求 24.7）。

    Args:
        session: 数据库会话。
        condition_type: 待校验的过滤条件类型键。

    Returns:
        合法返回 True；否则 False。
    """
    label = DictService(session).get_label(DICT_FILTER_CONDITION, condition_type)
    return label is not None


# ----------------------------------------------------------------------
# 过滤规则 CRUD（需求 12.1 / 12.6）
# ----------------------------------------------------------------------
def create_filter_rule(
    session: Session,
    shop_pk: int,
    condition_type: str,
    condition_value: str,
    *,
    operator_id: Optional[int] = None,
    enabled: bool = True,
) -> ApiResponse:
    """创建消息过滤规则并持久化（需求 12.1）。

    校验：店铺存在且在数据范围内；条件类型为字典合法枚举；条件值非空。

    Args:
        session: 数据库会话。
        shop_pk: 关联店铺主键。
        condition_type: 过滤条件类型（枚举入字典：contains/regex/msg_type）。
        condition_value: 过滤条件值。
        operator_id: 当前操作用户 ID（创建人审计 + 数据范围）。
        enabled: 是否启用，默认启用。

    Returns:
        统一响应体：成功返回新建规则信息。
    """
    denied = _ensure_shop_in_scope(session, shop_pk, operator_id)
    if denied is not None:
        return denied

    if not condition_type or not _validate_condition_type(session, condition_type):
        return error_response(CODE_PARAM_ERROR, "过滤条件类型不合法")
    if not condition_value or not condition_value.strip():
        return error_response(CODE_PARAM_ERROR, "过滤条件值不能为空")

    rule = Repository(MessageFilterRule, session).create(
        shop_pk=shop_pk,
        condition_type=condition_type,
        condition_value=condition_value.strip(),
        enabled=enabled,
        created_by=operator_id,
    )
    return success_response(data=serialize_filter_rule(rule), message="创建成功")


def update_filter_rule(
    session: Session,
    rule_id: int,
    *,
    operator_id: Optional[int] = None,
    condition_type: Optional[str] = None,
    condition_value: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> ApiResponse:
    """修改消息过滤规则（条件类型 / 条件值 / 启停用）。

    仅更新显式传入的字段。修改条件类型时校验字典合法性。

    Args:
        session: 数据库会话。
        rule_id: 目标过滤规则 ID。
        operator_id: 当前操作用户 ID（数据范围校验）。
        condition_type: 新的过滤条件类型；None 表示不修改。
        condition_value: 新的过滤条件值；None 表示不修改。
        enabled: 新的启停用状态；None 表示不修改。

    Returns:
        统一响应体：成功返回更新后的规则信息。
    """
    rule_repo = Repository(MessageFilterRule, session)
    rule = rule_repo.get(rule_id)
    if rule is None:
        return error_response(CODE_NOT_FOUND, "目标过滤规则不存在")

    denied = _ensure_shop_in_scope(session, rule.shop_pk, operator_id)
    if denied is not None:
        return denied

    values: Dict[str, Any] = {}
    if condition_type is not None:
        if not _validate_condition_type(session, condition_type):
            return error_response(CODE_PARAM_ERROR, "过滤条件类型不合法")
        values["condition_type"] = condition_type
    if condition_value is not None:
        if not condition_value.strip():
            return error_response(CODE_PARAM_ERROR, "过滤条件值不能为空")
        values["condition_value"] = condition_value.strip()
    if enabled is not None:
        values["enabled"] = enabled

    if not values:
        return error_response(CODE_PARAM_ERROR, "未提供任何待更新字段")

    rule_repo.update(rule_id, **values)
    return success_response(data=serialize_filter_rule(rule), message="更新成功")


def set_filter_rule_status(
    session: Session,
    rule_id: int,
    enabled: bool,
    *,
    operator_id: Optional[int] = None,
) -> ApiResponse:
    """启用 / 停用消息过滤规则（停用经状态字段逻辑删除，禁止物理删除）。

    Args:
        session: 数据库会话。
        rule_id: 目标过滤规则 ID。
        enabled: True=启用，False=停用（逻辑删除）。
        operator_id: 当前操作用户 ID（数据范围校验）。

    Returns:
        统一响应体：成功返回更新后的规则信息。
    """
    rule_repo = Repository(MessageFilterRule, session)
    rule = rule_repo.get(rule_id)
    if rule is None:
        return error_response(CODE_NOT_FOUND, "目标过滤规则不存在")

    denied = _ensure_shop_in_scope(session, rule.shop_pk, operator_id)
    if denied is not None:
        return denied

    rule_repo.update(rule_id, enabled=enabled)
    message = "已启用" if enabled else "已停用"
    return success_response(data=serialize_filter_rule(rule), message=message)


def list_filter_rules(
    session: Session,
    page: Any = 1,
    page_size: Any = 20,
    *,
    operator_id: Optional[int] = None,
    shop_pk: Optional[int] = None,
    enabled: Optional[bool] = None,
) -> ApiResponse:
    """分页查询消息过滤规则列表（后端分页，需求 12.6）。

    非管理员仅可见其可访问店铺下的规则（数据范围隔离，需求 3.7）。默认按创建
    时间倒序（仓储层自动探测时间字段）。

    Args:
        session: 数据库会话。
        page: 页码（从 1 开始，将被规整）。
        page_size: 每页条数（10/20/50/100，将被规整）。
        operator_id: 当前操作用户 ID。
        shop_pk: 按店铺筛选；None 表示该用户可见范围内全部店铺。
        enabled: 按启停用筛选；None 表示不筛选。

    Returns:
        统一响应体：data 为分页结构 {list, total, page, page_size}。
    """
    return _paginate_with_scope(
        session,
        model=MessageFilterRule,
        serializer=serialize_filter_rule,
        page=page,
        page_size=page_size,
        operator_id=operator_id,
        shop_pk=shop_pk,
        extra_filters={"enabled": enabled} if enabled is not None else None,
    )


# ----------------------------------------------------------------------
# 黑名单加入 / 移出（需求 12.3 / 12.5 / 12.6）
# ----------------------------------------------------------------------
def add_to_blacklist(
    session: Session,
    shop_pk: int,
    customer_uid: str,
    *,
    operator_id: Optional[int] = None,
) -> ApiResponse:
    """将客户加入黑名单（需求 12.3）。

    按 (shop_pk, customer_uid) 业务键 upsert：重复加入保持单条记录并置为有效
    （幂等）；若此前曾被移出失效（is_active=False），重新加入会恢复为有效。

    Args:
        session: 数据库会话。
        shop_pk: 关联店铺主键。
        customer_uid: 客户唯一标识。
        operator_id: 当前操作用户 ID（创建人审计 + 数据范围）。

    Returns:
        统一响应体：成功返回黑名单记录信息。
    """
    denied = _ensure_shop_in_scope(session, shop_pk, operator_id)
    if denied is not None:
        return denied

    if not customer_uid or not customer_uid.strip():
        return error_response(CODE_PARAM_ERROR, "客户标识不能为空")

    customer_uid = customer_uid.strip()
    repo = Repository(Blacklist, session)
    existing = repo.get_by(shop_pk=shop_pk, customer_uid=customer_uid)
    if existing is not None:
        # 已存在：恢复为有效（幂等加入），不新建重复记录。
        repo.update(existing.id, is_active=True)
        return success_response(data=serialize_blacklist(existing), message="已加入黑名单")

    item = repo.create(
        shop_pk=shop_pk,
        customer_uid=customer_uid,
        is_active=True,
        created_by=operator_id,
    )
    return success_response(data=serialize_blacklist(item), message="已加入黑名单")


def remove_from_blacklist(
    session: Session,
    blacklist_id: int,
    *,
    operator_id: Optional[int] = None,
) -> ApiResponse:
    """将客户移出黑名单——逻辑失效，禁止物理删除数据（需求 12.5）。

    将 ``is_active`` 置为 False，记录保留、总数不变；移出后运行时不再据该记录
    拦截客户消息。

    Args:
        session: 数据库会话。
        blacklist_id: 目标黑名单记录 ID。
        operator_id: 当前操作用户 ID（数据范围校验）。

    Returns:
        统一响应体：成功返回移出后的黑名单记录信息。
    """
    repo = Repository(Blacklist, session)
    item = repo.get(blacklist_id)
    if item is None:
        return error_response(CODE_NOT_FOUND, "目标黑名单记录不存在")

    denied = _ensure_shop_in_scope(session, item.shop_pk, operator_id)
    if denied is not None:
        return denied

    # 逻辑失效：置 is_active=False，禁止物理删除（需求 12.5 / 24.6）。
    repo.update(blacklist_id, is_active=False)
    return success_response(data=serialize_blacklist(item), message="已移出黑名单")


def list_blacklist(
    session: Session,
    page: Any = 1,
    page_size: Any = 20,
    *,
    operator_id: Optional[int] = None,
    shop_pk: Optional[int] = None,
    is_active: Optional[bool] = None,
) -> ApiResponse:
    """分页查询黑名单列表（后端分页，需求 12.6）。

    非管理员仅可见其可访问店铺下的黑名单（数据范围隔离，需求 3.7）。默认按
    创建时间倒序。

    Args:
        session: 数据库会话。
        page: 页码（将被规整）。
        page_size: 每页条数（将被规整）。
        operator_id: 当前操作用户 ID。
        shop_pk: 按店铺筛选；None 表示该用户可见范围内全部店铺。
        is_active: 按有效性筛选（True=仅有效，False=仅失效）；None 表示全部。

    Returns:
        统一响应体：data 为分页结构 {list, total, page, page_size}。
    """
    return _paginate_with_scope(
        session,
        model=Blacklist,
        serializer=serialize_blacklist,
        page=page,
        page_size=page_size,
        operator_id=operator_id,
        shop_pk=shop_pk,
        extra_filters={"is_active": is_active} if is_active is not None else None,
    )


# ----------------------------------------------------------------------
# 内部辅助：带数据范围隔离的分页查询（过滤规则 / 黑名单复用）
# ----------------------------------------------------------------------
def _paginate_with_scope(
    session: Session,
    *,
    model: Any,
    serializer: Any,
    page: Any,
    page_size: Any,
    operator_id: Optional[int],
    shop_pk: Optional[int],
    extra_filters: Optional[Dict[str, Any]] = None,
) -> ApiResponse:
    """按数据范围隔离对 shop_pk 维度的业务表做后端分页查询的通用实现。

    管理员可见全部；非管理员仅可见其可访问店铺主键集合内的数据。当显式传入
    ``shop_pk`` 时，对非管理员还需校验该店铺在其可见范围内，越权则返回无权限。

    Args:
        session: 数据库会话。
        model: 目标 ORM 模型（含 ``shop_pk`` 列）。
        serializer: 单条记录序列化函数。
        page: 页码。
        page_size: 每页条数。
        operator_id: 当前操作用户 ID。
        shop_pk: 指定店铺筛选；None 表示可见范围内全部。
        extra_filters: 附加等值筛选条件（如 enabled / is_active）。

    Returns:
        统一响应体：data 为分页结构 {list, total, page, page_size}。
    """
    accessible = _accessible_shop_pks(session, operator_id)
    base_filters: Dict[str, Any] = dict(extra_filters or {})

    if shop_pk is not None:
        # 指定店铺：非管理员需校验该店铺在可见范围内（越权拒绝）。
        if accessible is not None and shop_pk not in accessible:
            return error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)
        base_filters["shop_pk"] = shop_pk
        page_result = Repository(model, session).paginate(
            page=page, page_size=page_size, filters=base_filters or None
        )
    elif accessible is None:
        # 管理员且未指定店铺：可见全部。
        page_result = Repository(model, session).paginate(
            page=page, page_size=page_size, filters=base_filters or None
        )
    else:
        # 非管理员且未指定店铺：按可见店铺主键集合做 IN 过滤（空集合即无数据）。
        page_result = _paginate_in_shop_pks(
            session, model, page, page_size, accessible, base_filters
        )

    serialized: List[Dict[str, Any]] = [serializer(row) for row in page_result.items]
    data = {
        "list": serialized,
        "total": page_result.total,
        "page": page_result.page,
        "page_size": page_result.page_size,
    }
    return success_response(data=data, message="查询成功")


def _paginate_in_shop_pks(
    session: Session,
    model: Any,
    page: Any,
    page_size: Any,
    shop_pks: List[int],
    base_filters: Dict[str, Any],
) -> Any:
    """对「shop_pk 落在给定集合内」的记录做后端分页（参数化 IN 查询）。

    非管理员可见多个店铺时，等值过滤无法表达「IN 多值」，故在此用 SQLAlchemy
    ``in_`` 条件构造参数化查询并复用分页规整逻辑，保证不越权、不遗漏。

    Args:
        session: 数据库会话。
        model: 目标 ORM 模型（含 ``shop_pk`` 列）。
        page: 页码。
        page_size: 每页条数。
        shop_pks: 可见店铺主键集合（可能为空，空集合返回空结果）。
        base_filters: 附加等值筛选条件。

    Returns:
        ``PageResult`` 分页结构实例。
    """
    from sqlalchemy import func, select

    from common.utils.pagination import (
        build_page_result,
        calc_offset,
        normalize_pagination,
    )

    norm_page, norm_size = normalize_pagination(page, page_size)

    # 构造 shop_pk IN 条件 + 附加等值条件（全部参数化绑定）。
    condition = model.shop_pk.in_(shop_pks)
    count_stmt = select(func.count()).select_from(model).where(condition)
    list_stmt = select(model).where(condition)
    for key, value in base_filters.items():
        col = getattr(model, key)
        count_stmt = count_stmt.where(col == value)
        list_stmt = list_stmt.where(col == value)

    total = int(session.execute(count_stmt).scalar_one())

    # 默认按创建时间倒序、主键倒序兜底（与仓储层默认排序口径一致）。
    list_stmt = list_stmt.order_by(model.created_at.desc(), model.id.desc())
    offset = calc_offset(norm_page, norm_size)
    list_stmt = list_stmt.offset(offset).limit(norm_size)
    items = list(session.execute(list_stmt).scalars().all())

    return build_page_result(
        items=items, total=total, page=norm_page, page_size=norm_size
    )


__all__ = [
    "DICT_FILTER_CONDITION",
    "serialize_filter_rule",
    "serialize_blacklist",
    "create_filter_rule",
    "update_filter_rule",
    "set_filter_rule_status",
    "list_filter_rules",
    "add_to_blacklist",
    "remove_from_blacklist",
    "list_blacklist",
]

# -*- coding: utf-8 -*-
"""
backend.app.core.data_scope —— 数据范围隔离逻辑（集中式、纯逻辑可测）
====================================================================
本文件用途：按《开发规范》第 41/42 条「多用户多权限、权限判断集中」，为 backend
服务提供唯一的「数据范围隔离」判定入口，供店铺 / 会话 / 个人设置等各类列表
查询统一调用，禁止隔离逻辑散落各处。覆盖两类隔离语义：

- **归属范围隔离（ownership scope）**：用于店铺、会话等带「归属用户」的业务数据。
  规则：管理员不受限（可见全部）；非管理员仅可见「本人创建」或「被显式授权」
  的归属对象的数据（需求 3.7 / 14.1，Property 8）。
- **个人维度隔离（personal scope）**：用于个人设置等按用户维度存放的数据。
  规则：严格按用户维度隔离，不同用户互不可见，且不因管理员身份而放开
  （个人设置是「私有」数据，管理员亦不应看到他人个人设置，需求 22.7）。

设计要点（为属性测试 4.10 Property 8 服务）：
- **纯判定逻辑与数据库分离**：核心判定为不依赖数据库的纯函数
  （``is_in_scope`` / ``filter_in_scope`` / ``is_personal_visible`` /
  ``filter_personal``），输入为「数据范围 ``DataScope`` + 记录集合 + 归属取值器」
  等纯数据结构，便于 Hypothesis 直接生成「用户 + 记录集合 + 归属字段」做属性
  测试，无需连接 MySQL。
- **查询条件构造器**：``scope_equality_filter`` / ``build_owner_condition``
  供列表查询「下推」隔离条件到数据库（管理员无附加条件；非管理员附加归属
  条件），减少全量取数后再过滤的开销。
- **DB 加载辅助单独提供**：``build_data_scope`` 据用户装配 ``DataScope``
  （经统一权限模块解析 is_admin），``authorized_owner_ids`` 显式传入被授权的
  归属对象集合（由各业务依据其授权关系提供），保持本模块与具体授权表解耦。

约束：导入置顶（规范 51）；中文注释（规范 37）；本文件 ≤500 行（规范 35）；
仅在 backend 内实现并通过 ``import common`` 复用公共库（规范 34/52）。
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.business_codes import (
    CODE_FORBIDDEN,
    CODE_NOT_FOUND,
    MSG_FORBIDDEN,
)
from app.core.permission import load_auth_context
from common.db.repository import Repository
from common.models.shop_models import Shop
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response, success_response
from common.utils.pagination import (
    build_page_result,
    calc_offset,
    normalize_pagination,
)

# 记录类型变量：仅用于类型标注，记录可为 ORM 模型实例或任意带归属信息的对象。
RecordT = TypeVar("RecordT")

# 归属取值器：从单条记录提取其「归属用户 ID」（None 表示无归属 / 无法判定）。
OwnerGetter = Callable[[Any], "int | None"]

# 归属字段默认名（与 common.models 中店铺/账号等业务表的归属列命名一致）。
DEFAULT_OWNER_FIELD: str = "owner_user_id"


# ----------------------------------------------------------------------
# 纯数据结构（不依赖数据库，供纯判定函数与属性测试直接构造）
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class DataScope:
    """用户数据范围（纯数据，DB 无关）。

    封装做归属范围隔离所需的最小信息：是否管理员、当前用户 ID、被显式授权可
    访问的归属用户 ID 集合。由 ``build_data_scope`` 从数据库装配 is_admin，或在
    属性测试中直接构造。

    Attributes:
        is_admin: 当前用户所属角色是否为管理员角色（管理员不受归属限制，需求 3.7）。
        user_id: 当前用户 ID；None 表示无身份（非管理员且无 user_id 时不可见任何数据）。
        authorized_owner_ids: 被显式授权可访问的「归属用户 ID」集合（不含本人时
            由 ``is_in_scope`` 额外放行本人，需求 3.7）。
    """

    is_admin: bool = False
    user_id: int | None = None
    authorized_owner_ids: frozenset[int] = field(default_factory=frozenset)

    def allowed_owner_ids(self) -> frozenset[int] | None:
        """返回非管理员可访问的全部归属用户 ID 集合；管理员返回 None（不受限）。

        非管理员的可访问集合 = 本人 ∪ 被显式授权对象（需求 3.7）。

        Returns:
            管理员返回 None（表示不附加任何归属限制）；非管理员返回归属 ID 集合
            （可能为空集，表示不可见任何数据）。
        """
        if self.is_admin:
            return None
        owners: set[int] = set(self.authorized_owner_ids)
        if self.user_id is not None:
            owners.add(self.user_id)
        return frozenset(owners)


# ----------------------------------------------------------------------
# 归属范围隔离 —— 纯判定逻辑（不依赖数据库，Property 8 直接验证此层）
# ----------------------------------------------------------------------
def is_in_scope(scope: DataScope, owner_id: int | None) -> bool:
    """纯判定：某条记录（按其归属用户 ID）是否在用户的数据范围内。

    规则（需求 3.7 / 14.1，Property 8）：
    - 管理员：恒为 True（不受归属限制）；
    - 非管理员：当且仅当 ``owner_id`` 等于本人 ``user_id``，或处于被显式授权的
      ``authorized_owner_ids`` 中时为 True；``owner_id`` 为 None（无归属）时，
      非管理员一律不可见。

    Args:
        scope: 用户数据范围。
        owner_id: 待判定记录的归属用户 ID。

    Returns:
        True 表示该记录在用户可见范围内。
    """
    if scope.is_admin:
        return True
    if owner_id is None:
        # 非管理员不可见「无归属」数据，避免越权泄露
        return False
    allowed = scope.allowed_owner_ids()
    # 非管理员时 allowed 必为集合（非 None）
    return owner_id in (allowed or frozenset())


def filter_in_scope(
    scope: DataScope,
    records: Iterable[RecordT],
    owner_getter: OwnerGetter | None = None,
    owner_field: str = DEFAULT_OWNER_FIELD,
) -> list[RecordT]:
    """纯判定：过滤记录集合，仅保留落在用户数据范围内的记录（保持输入顺序）。

    保证（Property 8）：返回结果中每一条的归属者必为该用户本人或其被显式授权
    的对象；管理员则原样返回全部记录。

    Args:
        scope: 用户数据范围。
        records: 待过滤记录集合（ORM 实例或任意对象）。
        owner_getter: 从单条记录提取归属用户 ID 的取值器；None 时按 ``owner_field``
            以 ``getattr`` 读取。
        owner_field: 当未提供 ``owner_getter`` 时使用的归属字段名（默认
            ``owner_user_id``）。

    Returns:
        过滤后的记录列表（保持输入顺序）。
    """
    getter = owner_getter or _attr_owner_getter(owner_field)
    return [record for record in records if is_in_scope(scope, getter(record))]


# ----------------------------------------------------------------------
# 个人维度隔离 —— 纯判定逻辑（个人设置：管理员亦不可见他人，需求 22.7）
# ----------------------------------------------------------------------
def is_personal_visible(user_id: int | None, record_user_id: int | None) -> bool:
    """纯判定：某条个人维度数据对该用户是否可见（不同用户互不可见，需求 22.7）。

    个人设置为「私有」数据，严格按用户维度隔离：当且仅当记录归属用户 ID 等于
    当前用户 ID 时可见；不因管理员身份放开（管理员也看不到他人个人设置）。

    Args:
        user_id: 当前用户 ID。
        record_user_id: 个人维度记录的归属用户 ID。

    Returns:
        True 表示可见。
    """
    if user_id is None or record_user_id is None:
        return False
    return user_id == record_user_id


def filter_personal(
    user_id: int | None,
    records: Iterable[RecordT],
    owner_getter: OwnerGetter | None = None,
    owner_field: str = "user_id",
) -> list[RecordT]:
    """纯判定：过滤个人维度记录集合，仅保留属于当前用户的记录（保持输入顺序）。

    保证（需求 22.7）：不同用户的个人设置互不可见。

    Args:
        user_id: 当前用户 ID。
        records: 待过滤的个人维度记录集合。
        owner_getter: 从单条记录提取归属用户 ID 的取值器；None 时按 ``owner_field``
            以 ``getattr`` 读取。
        owner_field: 当未提供 ``owner_getter`` 时使用的归属字段名（默认 ``user_id``）。

    Returns:
        仅含当前用户记录的列表（保持输入顺序）。
    """
    getter = owner_getter or _attr_owner_getter(owner_field)
    return [
        record for record in records if is_personal_visible(user_id, getter(record))
    ]


# ----------------------------------------------------------------------
# 查询条件构造器（将隔离条件「下推」到数据库列表查询 —— 性能与正确性兼顾）
# ----------------------------------------------------------------------
def scope_equality_filter(
    scope: DataScope, owner_field: str = DEFAULT_OWNER_FIELD
) -> dict[str, Any] | None:
    """构造可附加到 Repository 等值查询的归属过滤条件（仅适用单一归属场景）。

    适配 ``Repository.list`` / ``Repository.paginate`` 的等值 ``filters`` 入参：
    - 管理员：返回 ``{}``（不附加任何归属限制，可见全部）；
    - 非管理员且可见归属恰为「本人」一项：返回 ``{owner_field: user_id}``，将隔离
      条件下推数据库；
    - 非管理员存在多个可见归属（本人 + 被授权对象）：等值过滤无法表达「IN 多值」，
      返回 None，提示调用方改用 ``build_owner_condition``（IN 条件）或对结果集
      调用 ``filter_in_scope`` 做后置过滤，避免遗漏或越权。

    Args:
        scope: 用户数据范围。
        owner_field: 归属字段名（默认 ``owner_user_id``）。

    Returns:
        - ``{}``：管理员，无附加条件；
        - ``{owner_field: user_id}``：非管理员且单一归属；
        - ``None``：非管理员且多归属（需用 IN 条件或结果集过滤）。
    """
    allowed = scope.allowed_owner_ids()
    if allowed is None:
        # 管理员：不附加归属限制
        return {}
    if len(allowed) == 1:
        # 单一归属：可安全下推为等值条件
        (only_owner,) = tuple(allowed)
        return {owner_field: only_owner}
    # 空集（不可见任何数据）或多归属：等值过滤无法准确表达，交由调用方处理
    if not allowed:
        # 空集：构造一个永不命中的条件由调用方处理更稳妥，这里返回 None 以示需特判
        return None
    return None


def build_owner_condition(scope: DataScope, owner_column: Any) -> Any | None:
    """构造可用于 SQLAlchemy ``where`` 的归属隔离条件（支持多归属 IN 表达）。

    供需要「下推多归属隔离条件」的列表查询使用（配合自定义查询语句）：
    - 管理员：返回 None（不附加任何条件，可见全部）；
    - 非管理员：返回 ``owner_column.in_([...])`` 条件（可见归属为空集时返回
      ``owner_column.in_([])`` 永不命中，确保不可见任何数据，避免越权）。

    Args:
        scope: 用户数据范围。
        owner_column: 目标模型的归属列（如 ``Shop.owner_user_id``）。

    Returns:
        管理员返回 None；非管理员返回 SQLAlchemy IN 条件表达式。
    """
    allowed = scope.allowed_owner_ids()
    if allowed is None:
        # 管理员：不附加任何条件
        return None
    # 非管理员：以 IN 条件下推（空集时 in_([]) 永不命中，确保隔离）
    return owner_column.in_(sorted(allowed))


# ----------------------------------------------------------------------
# DB 加载辅助（据用户装配 DataScope —— 与纯判定逻辑分离）
# ----------------------------------------------------------------------
def build_data_scope(
    user: SysUser | None,
    *,
    session: Session,
    authorized_owner_ids: Iterable[int] | None = None,
) -> DataScope:
    """据当前用户装配数据范围 ``DataScope``（经统一权限模块解析 is_admin）。

    is_admin 复用 ``permission.load_auth_context`` 的解析结果，保证「是否管理员」
    的判定与权限模块一致（规范 42 集中判权）。被显式授权的归属对象集合由各业务
    依据其授权关系显式传入，本模块不耦合具体授权表。

    Args:
        user: 当前登录用户模型；None 视为无身份（非管理员、无 user_id）。
        session: 当前事务性会话（关键字参数）。
        authorized_owner_ids: 被显式授权可访问的归属用户 ID 集合；None 表示仅本人。

    Returns:
        装配完成的 ``DataScope``（纯数据结构）。
    """
    if user is None:
        return DataScope(is_admin=False, user_id=None, authorized_owner_ids=frozenset())
    context = load_auth_context(user, session)
    owner_ids = frozenset(authorized_owner_ids or ())
    return DataScope(
        is_admin=context.is_admin,
        user_id=user.id,
        authorized_owner_ids=owner_ids,
    )


# ----------------------------------------------------------------------
# 店铺级数据范围校验（共享辅助，供各「店铺级设置」service 统一复用，规范 36）
# ----------------------------------------------------------------------
def load_user_scope(session: Session, operator_id: int | None) -> DataScope:
    """据操作用户 ID 装配数据范围 ``DataScope``（无身份时返回非管理员空范围）。

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


def ensure_shop_in_scope(
    session: Session,
    shop_pk: int,
    operator_id: int | None,
) -> ApiResponse | None:
    """校验目标店铺存在且当前用户对其有数据访问权限（需求 3.7，规范 41/42a）。

    供所有「店铺级设置」service（AI 配置 / 营业时间 / 转人工 / 关键词 / 通知等）
    统一复用，避免隔离逻辑重复实现（规范 36）。管理员不受限；非管理员仅可操作
    本人创建（或被授权）店铺下的数据。

    Args:
        session: 数据库会话。
        shop_pk: 目标店铺主键（shop.id）。
        operator_id: 当前操作用户 ID。

    Returns:
        校验未通过时返回失败响应体（店铺不存在 / 无权限）；通过返回 None。
    """
    shop = Repository(Shop, session).get(shop_pk)
    if shop is None or shop.merged_into_shop_pk is not None:
        return error_response(CODE_NOT_FOUND, "目标店铺不存在")
    scope = load_user_scope(session, operator_id)
    if not is_in_scope(scope, shop.owner_user_id):
        return error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)
    return None


def accessible_shop_pks(
    session: Session, operator_id: int | None
) -> list[int] | None:
    """返回当前用户可访问的店铺主键列表；管理员返回 None（不受限）。

    供「店铺级设置」列表查询做数据范围隔离：非管理员仅可见其本人创建（或被
    授权）店铺下的数据（需求 3.7 / 规范 42a）。单次 IN 查询取出名下店铺，避免
    N+1。

    Args:
        session: 数据库会话。
        operator_id: 当前操作用户 ID。

    Returns:
        管理员返回 None；非管理员返回可访问店铺主键列表（可能为空列表）。
    """
    scope = load_user_scope(session, operator_id)
    allowed_owners = scope.allowed_owner_ids()
    if allowed_owners is None:
        return None
    owner_ids = sorted(allowed_owners)
    if not owner_ids:
        return []
    shops = Repository(Shop, session).list(
        order_by=False,
        extra_conditions=[Shop.owner_user_id.in_(owner_ids), Shop.merged_into_shop_pk.is_(None)],
    )
    return [shop.id for shop in shops]


def paginate_shop_scoped(
    session: Session,
    *,
    model: Any,
    serializer: Callable[[Any], dict[str, Any]],
    page: Any,
    page_size: Any,
    operator_id: int | None,
    shop_pk: int | None,
    extra_filters: dict[str, Any] | None = None,
) -> ApiResponse:
    """对含 ``shop_pk`` 列的店铺级业务表做「带数据范围隔离」的后端分页查询。

    供所有店铺级设置列表统一复用（规范 36），管理员可见全部；非管理员仅可见
    其可访问店铺主键集合内的数据。显式传入 ``shop_pk`` 时，对非管理员还需校验
    该店铺在可见范围内，越权则返回无权限（需求 3.7 / 规范 42a）。

    Args:
        session: 数据库会话。
        model: 目标 ORM 模型（须含 ``shop_pk`` 列）。
        serializer: 单条记录序列化函数。
        page: 页码（将被规整）。
        page_size: 每页条数（将被规整）。
        operator_id: 当前操作用户 ID。
        shop_pk: 指定店铺筛选；None 表示可见范围内全部。
        extra_filters: 附加等值筛选条件（如 enabled / is_active）。

    Returns:
        统一响应体：data 为分页结构 {list, total, page, page_size}。
    """
    accessible = accessible_shop_pks(session, operator_id)
    base_filters: dict[str, Any] = dict(extra_filters or {})

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
        # 非管理员且未指定店铺：按可见店铺主键集合做参数化 IN 过滤。
        page_result = _paginate_in_shop_pks(
            session, model, page, page_size, accessible, base_filters
        )

    serialized = [serializer(row) for row in page_result.items]
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
    shop_pks: list[int],
    base_filters: dict[str, Any],
) -> Any:
    """对「shop_pk 落在给定集合内」的记录做后端分页（参数化 IN 查询）。

    Args:
        session: 数据库会话。
        model: 目标 ORM 模型（含 ``shop_pk`` 列）。
        page: 页码。
        page_size: 每页条数。
        shop_pks: 可见店铺主键集合（空集合返回空结果）。
        base_filters: 附加等值筛选条件。

    Returns:
        ``PageResult`` 分页结构实例。
    """
    norm_page, norm_size = normalize_pagination(page, page_size)

    condition = model.shop_pk.in_(shop_pks)
    count_stmt = select(func.count()).select_from(model).where(condition)
    list_stmt = select(model).where(condition)
    for key, value in base_filters.items():
        col = getattr(model, key)
        count_stmt = count_stmt.where(col == value)
        list_stmt = list_stmt.where(col == value)

    total = int(session.execute(count_stmt).scalar_one())
    list_stmt = list_stmt.order_by(model.created_at.desc(), model.id.desc())
    offset = calc_offset(norm_page, norm_size)
    list_stmt = list_stmt.offset(offset).limit(norm_size)
    items = list(session.execute(list_stmt).scalars().all())

    return build_page_result(
        items=items, total=total, page=norm_page, page_size=norm_size
    )


# ----------------------------------------------------------------------
# 内部辅助
# ----------------------------------------------------------------------
def _attr_owner_getter(owner_field: str) -> OwnerGetter:
    """构造「按属性名读取归属用户 ID」的取值器。

    Args:
        owner_field: 归属字段名。

    Returns:
        从记录以 ``getattr`` 读取 ``owner_field`` 的取值器（字段缺失时返回 None）。
    """

    def _getter(record: Any) -> int | None:
        return getattr(record, owner_field, None)

    return _getter


__all__ = [
    "OwnerGetter",
    "DEFAULT_OWNER_FIELD",
    "DataScope",
    "is_in_scope",
    "filter_in_scope",
    "is_personal_visible",
    "filter_personal",
    "scope_equality_filter",
    "build_owner_condition",
    "build_data_scope",
    "load_user_scope",
    "ensure_shop_in_scope",
    "accessible_shop_pks",
    "paginate_shop_scoped",
]

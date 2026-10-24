# -*- coding: utf-8 -*-
"""
common.db.repository —— SQL 统一管理通用仓储层
================================================
本文件用途：为「拼多多自动回复」系统提供「统一管理」的通用数据访问仓储层
（开发规范 12：所有 SQL 集中管理，不在各业务文件随处拼接）。基于
SQLAlchemy 2.0（同步）ORM 实现，所有查询均由 ORM 生成参数化语句，杜绝字符串
拼接 SQL（规范 16 / 需求 24.4，防 SQL 注入），并提供：

- 通用创建 ``create`` / 批量创建 ``bulk_create``；
- 按主键查询 ``get``、按条件查询 ``get_by`` / 列表 ``list``、计数 ``count``；
- 通用更新 ``update``（按主键）/ ``update_by``（按条件）；
- 分页查询 ``paginate``：结合 common.utils.pagination 返回
  ``{list, total, page, page_size}``，默认按时间字段倒序（规范 28 / 需求 24.x）；
- 逻辑删除 ``soft_delete`` / ``soft_delete_by``：通过状态字段
  （status/enabled/is_active/deleted_flag）更新实现，**禁止物理删除业务数据**
  （规范 11 / 需求 24.6），记录保留、总数不变；
- 按业务键 ``upsert``：存在则更新、不存在则插入，保证相同业务键多次写入
  记录数恒为 1 且内容为最后一次写入（需求 3.2 / 9.2 / 15.4）。

并提供 ``run_in_session`` / ``run_with_retry`` 便捷函数，配合
common.db.session.session_scope 与 common.db.retry.with_db_retry，在「连接失败」
时自动重试（规范 13）；每次重试都会打开一个全新的事务性会话，避免复用已损坏
的连接。

设计说明：
- 仓储以「模型类 + 会话」构造，单实例只服务单张表；SQL 语义集中于本模块，
  业务层只调用方法、传参数化条件，不书写任何原生 SQL 文本。
- 逻辑删除字段与「删除值」可自动探测（按优先级 deleted_flag → status →
  enabled → is_active），也可在调用时显式指定，兼容不同表的状态字段命名。
"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from common.db.retry import with_db_retry
from common.db.session import session_scope
from common.utils.pagination import (
    PageResult,
    build_page_result,
    calc_offset,
    normalize_pagination,
)

# 受 SQLAlchemy 声明式基类约束的模型类型变量（仅用于类型标注）。
ModelT = TypeVar("ModelT")

# 逻辑删除字段自动探测优先级：靠前者优先作为「状态/删除标记」字段。
# 不同业务表的状态字段命名不一（规范未强制统一字段名），故按约定顺序探测。
_SOFT_DELETE_FIELDS: tuple[str, ...] = (
    "deleted_flag",  # 删除标记：1/True=已删除
    "status",        # 启停状态：1=启用，0=停用（停用即逻辑删除）
    "enabled",       # 是否启用：True=启用，False=停用
    "is_active",     # 是否激活：True=有效，False=失效
)

# 各状态字段对应的「逻辑删除值」（置为该值即视为逻辑删除 / 停用）。
_SOFT_DELETE_VALUES: dict[str, Any] = {
    "deleted_flag": 1,
    "status": 0,
    "enabled": False,
    "is_active": False,
}

# 各状态字段对应的「有效值」（恢复 / 未删除时的取值）。
_SOFT_ACTIVE_VALUES: dict[str, Any] = {
    "deleted_flag": 0,
    "status": 1,
    "enabled": True,
    "is_active": True,
}

# 时间倒序默认探测的时间字段优先级（用于列表 / 分页默认排序）。
_TIME_ORDER_FIELDS: tuple[str, ...] = ("created_at", "updated_at", "id")


class Repository(Generic[ModelT]):
    """通用仓储：封装单张表的参数化 CRUD / 分页 / 逻辑删除 / upsert。

    所有方法均使用传入的 ORM 模型与 SQLAlchemy 2.0 表达式构造参数化查询，
    不书写任何原生 SQL 字符串（规范 16）。事务提交 / 回滚由外层（如
    ``session_scope`` 或 FastAPI ``get_db`` 依赖）负责，本类内部只 ``flush``
    以获取自增主键，不主动 ``commit``，保证可组合进更大的事务。
    """

    def __init__(self, model: type[ModelT], session: Session) -> None:
        """构造仓储实例。

        Args:
            model: SQLAlchemy 声明式模型类（对应一张表）。
            session: 当前事务性会话（由外层管理生命周期）。
        """
        self.model = model
        self.session = session

    # ------------------------------------------------------------------
    # 创建
    # ------------------------------------------------------------------
    def create(self, **values: Any) -> ModelT:
        """创建单条记录并 flush 以回填自增主键。

        Args:
            **values: 字段名到值的映射（仅参数化绑定，不拼接 SQL）。

        Returns:
            已持久化（含主键）的模型实例。
        """
        obj = self.model(**values)
        self.session.add(obj)
        # flush 触发 INSERT 并回填自增主键，但不结束事务
        self.session.flush()
        return obj

    def bulk_create(self, rows: list[dict[str, Any]]) -> list[ModelT]:
        """批量创建记录。

        Args:
            rows: 多条记录的字段字典列表。

        Returns:
            已持久化的模型实例列表（保持入参顺序）。
        """
        objs = [self.model(**row) for row in rows]
        self.session.add_all(objs)
        self.session.flush()
        return objs

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get(self, pk: Any) -> ModelT | None:
        """按主键查询单条记录。

        Args:
            pk: 主键值。

        Returns:
            命中的模型实例；不存在时返回 None。
        """
        return self.session.get(self.model, pk)

    def get_by(self, **filters: Any) -> ModelT | None:
        """按等值条件查询首条记录。

        Args:
            **filters: 字段名到值的等值条件（参数化绑定）。

        Returns:
            首条命中的模型实例；无命中返回 None。
        """
        stmt = select(self.model).filter_by(**filters).limit(1)
        return self.session.execute(stmt).scalars().first()

    def list(
        self,
        filters: dict[str, Any] | None = None,
        order_by: Any = None,
        desc_order: bool = True,
        limit: int | None = None,
        offset: int | None = None,
        extra_conditions: list[Any] | None = None,
    ) -> list[ModelT]:
        """按条件查询记录列表，默认按时间字段倒序。

        Args:
            filters: 等值条件字典（参数化绑定），None 表示无附加条件。
            order_by: 显式排序列；None 时自动探测时间字段（created_at 等）。
            desc_order: 是否倒序（默认 True，新数据在前）。
            limit: 返回条数上限；None 表示不限制。
            offset: 偏移量；None 表示从头开始。
            extra_conditions: 附加的 SQLAlchemy 条件表达式列表（如时间范围
                ``Model.log_time >= start``、IN 条件 ``Model.shop_pk.in_(ids)``），
                由 SQLAlchemy 生成参数化语句，不书写原生 SQL（规范 16）。

        Returns:
            模型实例列表。
        """
        stmt = select(self.model)
        if filters:
            stmt = stmt.filter_by(**filters)
        if extra_conditions:
            for condition in extra_conditions:
                stmt = stmt.where(condition)
        stmt = self._apply_order(stmt, order_by, desc_order)
        if offset:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    def count(
        self,
        filters: dict[str, Any] | None = None,
        extra_conditions: list[Any] | None = None,
    ) -> int:
        """统计满足条件的记录总数。

        Args:
            filters: 等值条件字典（参数化绑定），None 表示统计全表。
            extra_conditions: 附加的 SQLAlchemy 条件表达式列表（与 ``list`` 一致，
                用于时间范围 / IN 等非等值条件，参数化绑定）。

        Returns:
            记录总数。
        """
        stmt = select(func.count()).select_from(self.model)
        if filters:
            stmt = stmt.filter_by(**filters)
        if extra_conditions:
            for condition in extra_conditions:
                stmt = stmt.where(condition)
        return int(self.session.execute(stmt).scalar_one())

    # ------------------------------------------------------------------
    # 更新
    # ------------------------------------------------------------------
    def update(self, pk: Any, **values: Any) -> ModelT | None:
        """按主键更新记录字段。

        Args:
            pk: 主键值。
            **values: 待更新的字段映射（参数化绑定）。

        Returns:
            更新后的模型实例；记录不存在时返回 None。
        """
        obj = self.get(pk)
        if obj is None:
            return None
        for key, value in values.items():
            setattr(obj, key, value)
        self.session.flush()
        return obj

    def update_by(
        self, filters: dict[str, Any], values: dict[str, Any]
    ) -> int:
        """按条件批量更新记录字段（单条 UPDATE 语句，不全量载入内存）。

        以 SQLAlchemy Core ``update().where()`` 直接下发一条参数化 UPDATE，避免把
        命中行全部载入内存逐条 setattr（海量记录时的内存 / 性能风险）。
        ``synchronize_session="auto"`` 使会话内已加载的同表对象保持一致。

        Args:
            filters: 等值条件字典（参数化绑定）。
            values: 待更新的字段映射。

        Returns:
            受影响的记录条数。
        """
        if not values:
            return 0
        conditions = [getattr(self.model, key) == value for key, value in filters.items()]
        stmt = update(self.model).where(*conditions).values(**values)
        result = self.session.execute(
            stmt, execution_options={"synchronize_session": "auto"}
        )
        self.session.flush()
        return int(result.rowcount or 0)

    # ------------------------------------------------------------------
    # 分页查询
    # ------------------------------------------------------------------
    def paginate(
        self,
        page: Any = 1,
        page_size: Any = 20,
        filters: dict[str, Any] | None = None,
        order_by: Any = None,
        desc_order: bool = True,
        strict: bool = False,
        extra_conditions: list[Any] | None = None,
    ) -> PageResult:
        """分页查询，返回统一分页结构 {list, total, page, page_size}。

        - 页码与每页条数经 ``normalize_pagination`` 规整为合法值（规范 28）；
        - 默认按时间字段倒序（新数据在前）；
        - ``total`` 为满足条件的总记录数，与当前页数据相互独立。

        Args:
            page: 页码（从 1 开始，将被规整）。
            page_size: 每页条数（10/20/50/100，将被规整）。
            filters: 等值条件字典（参数化绑定）。
            order_by: 显式排序列；None 时自动探测时间字段。
            desc_order: 是否倒序（默认 True）。
            strict: 每页条数是否严格校验（详见 normalize_page_size）。

        Returns:
            ``PageResult`` 分页结构实例。
        """
        norm_page, norm_size = normalize_pagination(page, page_size, strict=strict)
        total = self.count(filters=filters, extra_conditions=extra_conditions)
        offset = calc_offset(norm_page, norm_size)
        items = self.list(
            filters=filters,
            order_by=order_by,
            desc_order=desc_order,
            limit=norm_size,
            offset=offset,
            extra_conditions=extra_conditions,
        )
        return build_page_result(
            items=items,
            total=total,
            page=norm_page,
            page_size=norm_size,
            strict=strict,
        )

    # ------------------------------------------------------------------
    # 逻辑删除（禁止物理删除业务数据 —— 规范 11 / 需求 24.6）
    # ------------------------------------------------------------------
    def soft_delete(
        self,
        pk: Any,
        field: str | None = None,
        deleted_value: Any = None,
    ) -> bool:
        """逻辑删除单条记录：通过状态字段更新实现，记录保留、总数不变。

        Args:
            pk: 主键值。
            field: 状态字段名；None 时自动探测（deleted_flag/status/...）。
            deleted_value: 逻辑删除值；None 时按字段默认（status→0 等）。

        Returns:
            True 表示已标记删除；记录不存在返回 False。

        Raises:
            ValueError: 模型不含可用的状态字段且未显式指定 field。
        """
        obj = self.get(pk)
        if obj is None:
            return False
        field_name = self._resolve_soft_delete_field(field)
        value = deleted_value if deleted_value is not None else _SOFT_DELETE_VALUES.get(field_name)
        setattr(obj, field_name, value)
        self.session.flush()
        return True

    def soft_delete_by(
        self,
        filters: dict[str, Any],
        field: str | None = None,
        deleted_value: Any = None,
    ) -> int:
        """按条件批量逻辑删除：状态字段置为删除值，记录保留、总数不变。

        Args:
            filters: 等值条件字典（参数化绑定）。
            field: 状态字段名；None 时自动探测。
            deleted_value: 逻辑删除值；None 时按字段默认。

        Returns:
            受影响（被标记删除）的记录条数。
        """
        field_name = self._resolve_soft_delete_field(field)
        value = deleted_value if deleted_value is not None else _SOFT_DELETE_VALUES.get(field_name)
        return self.update_by(filters=filters, values={field_name: value})

    def restore(
        self,
        pk: Any,
        field: str | None = None,
        active_value: Any = None,
    ) -> bool:
        """恢复逻辑删除：状态字段置回「有效值」（status→1 等）。

        Args:
            pk: 主键值。
            field: 状态字段名；None 时自动探测。
            active_value: 有效值；None 时按字段默认。

        Returns:
            True 表示已恢复；记录不存在返回 False。
        """
        obj = self.get(pk)
        if obj is None:
            return False
        field_name = self._resolve_soft_delete_field(field)
        value = active_value if active_value is not None else _SOFT_ACTIVE_VALUES.get(field_name)
        setattr(obj, field_name, value)
        self.session.flush()
        return True

    # ------------------------------------------------------------------
    # 按业务键 upsert（幂等 —— 需求 3.2 / 9.2 / 15.4）
    # ------------------------------------------------------------------
    def upsert(
        self,
        biz_keys: dict[str, Any],
        values: dict[str, Any] | None = None,
    ) -> ModelT:
        """按业务键 upsert：存在则更新、不存在则插入。

        保证「相同业务键多次写入，记录数恒为 1 且内容为最后一次写入」
        （需求 3.2 / 9.2 / 15.4）。先按业务键查询，命中则覆盖更新非键字段，
        未命中则以「业务键 + values」插入新记录。

        Args:
            biz_keys: 业务唯一键字段映射（如 {owner_user_id, shop_id}）。
            values: 非键的待写入字段映射；None 表示仅按业务键插入 / 不更新。

        Returns:
            upsert 后的模型实例（已 flush，含主键）。
        """
        values = values or {}
        existing = self.get_by(**biz_keys)
        if existing is not None:
            # 命中：仅覆盖非业务键字段，保持业务键不变，内容更新为最后一次写入
            for key, value in values.items():
                setattr(existing, key, value)
            self.session.flush()
            return existing
        # 未命中：以业务键 + 值插入新记录。
        # 并发兜底：两个事务可能同时 get_by 未命中、都尝试 insert；若业务键有唯一
        # 约束（见各模型 UniqueConstraint），后插入者会触发 IntegrityError。这里在
        # 子事务（SAVEPOINT）中插入，失败则回滚子事务并改为「重查 + 更新」，保证
        # 幂等不产生重复行（需求 3.2 / 9.2 / 15.4）。
        try:
            with self.session.begin_nested():
                obj = self.model(**{**biz_keys, **values})
                self.session.add(obj)
                self.session.flush()
            return obj
        except IntegrityError:
            # 唯一约束冲突：说明并发事务已插入同业务键记录，改为更新该记录。
            existing = self.get_by(**biz_keys)
            if existing is None:
                # 理论上不应发生（约束冲突却查不到）；重新抛出由上层处理。
                raise
            for key, value in values.items():
                setattr(existing, key, value)
            self.session.flush()
            return existing

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _apply_order(self, stmt: Any, order_by: Any, desc_order: bool) -> Any:
        """为查询语句应用排序。

        - order_by 为 False：不排序（供批量更新取数等无需排序的场景）；
        - order_by 为具体列：直接按该列排序；
        - order_by 为 None：自动探测时间字段（created_at→updated_at→id），
          默认倒序（新数据在前）。
        """
        if order_by is False:
            return stmt
        if order_by is not None:
            column = order_by
        else:
            column = self._detect_time_column()
            if column is None:
                return stmt
        stmt = stmt.order_by(column.desc() if desc_order else column.asc())
        # 以主键作为稳定的二级排序键，避免排序列取值相等时分页顺序不确定
        # （重复 / 漏项）。无论 order_by 是自动探测还是显式传入，只要排序列不是
        # 主键本身，都追加主键兜底，保证翻页结果稳定。
        pk_column = getattr(self.model, "id", None)
        if pk_column is not None and column is not pk_column:
            stmt = stmt.order_by(pk_column.desc() if desc_order else pk_column.asc())
        return stmt

    def _detect_time_column(self) -> Any:
        """自动探测用于默认排序的时间 / 主键列，找不到返回 None。"""
        for name in _TIME_ORDER_FIELDS:
            column = getattr(self.model, name, None)
            if column is not None:
                return column
        return None

    def _resolve_soft_delete_field(self, field: str | None) -> str:
        """确定逻辑删除使用的状态字段名。

        显式传入则校验其存在；否则按优先级自动探测模型上可用的状态字段。

        Raises:
            ValueError: 显式字段不存在，或模型不含任何可用状态字段。
        """
        if field is not None:
            if not hasattr(self.model, field):
                raise ValueError(
                    f"模型 {self.model.__name__} 不存在状态字段 {field!r}"
                )
            return field
        for name in _SOFT_DELETE_FIELDS:
            if hasattr(self.model, name):
                return name
        raise ValueError(
            f"模型 {self.model.__name__} 未找到可用于逻辑删除的状态字段"
            f"（候选：{list(_SOFT_DELETE_FIELDS)}），请显式指定 field"
        )


def run_in_session(handler: Any) -> Any:
    """在一个事务性会话中执行 ``handler(session)``，自动提交 / 回滚。

    便捷封装 ``session_scope``，供「单次数据库操作」直接使用：

        result = run_in_session(lambda s: Repository(Shop, s).create(...))

    Args:
        handler: 接收 Session 并返回结果的可调用对象。

    Returns:
        handler 的返回值。
    """
    with session_scope() as session:
        return handler(session)


def run_with_retry(
    handler: Any,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 8.0,
    backoff_factor: float = 2.0,
) -> Any:
    """在「带连接失败重试」的事务性会话中执行 ``handler(session)``。

    每次重试都会打开一个全新的 ``session_scope`` 会话，避免复用已损坏的连接
    （规范 13）。仅对连接断开类错误重试，业务错误立即抛出。

    Args:
        handler: 接收 Session 并返回结果的可调用对象。
        max_retries: 最大重试次数（不含首次执行）。
        initial_delay: 首次重试前等待秒数。
        max_delay: 单次等待时间上限。
        backoff_factor: 每次失败后等待时间放大倍数。

    Returns:
        handler 的返回值。
    """

    @with_db_retry(
        max_retries=max_retries,
        initial_delay=initial_delay,
        max_delay=max_delay,
        backoff_factor=backoff_factor,
    )
    def _runner() -> Any:
        # 每次（重试）调用都打开全新会话，保证连接新鲜可用
        with session_scope() as session:
            return handler(session)

    return _runner()


__all__ = [
    "Repository",
    "run_in_session",
    "run_with_retry",
]

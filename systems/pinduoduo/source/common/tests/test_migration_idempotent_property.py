# -*- coding: utf-8 -*-
"""
启动迁移幂等属性测试（common.db.init_database.SchemaMigrator）
==============================================================
本文件用途：以 Hypothesis 属性测试验证启动自检迁移器（SchemaMigrator）的
幂等性，对应设计文档 Property 22「启动迁移幂等」
（Validates: Requirements 24.5）。

即：对任意已存在的数据库 schema 状态（先运行一次迁移建表、并随机插入若干
历史数据行），再次运行启动自检迁移时：
- 第二次运行不产生任何额外结构变更（MigrationResult.changed 为 False，即
  无新建表 / 无补列 / 无新增种子）；
- 任何历史数据行均不被修改或删除（行数与内容保持不变）。

测试基础设施：使用 SQLite 内存库 + common 模型（参照任务 2.8 / 2.11 验证方式）。
每个 example 使用一个全新的内存引擎并由迁移器建表，避免用例间数据串扰；
并清空字典初始化钩子以隔离全局状态（种子钩子默认绑定全局引擎，不适用于
本测试的独立内存引擎，清空后种子步骤为空操作，专注校验结构幂等与数据安全）。
"""
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from common.db.init_database import SchemaMigrator, clear_dict_initializers
from common.models.base import Base
from common.models.knowledge_models import CustomerServiceKnowledge, Product

# 单行商品历史数据生成器：覆盖商品名称、价格、已售数量、状态等字段。
# 价格用有界两位小数（规避 Numeric 精度问题），数量用非负整数，文本允许 None。
_product_row_strategy = st.fixed_dictionaries(
    {
        "shop_pk": st.integers(min_value=1, max_value=10**6),
        "goods_id": st.text(min_size=1, max_size=32),
        "goods_name": st.one_of(st.none(), st.text(max_size=50)),
        "price": st.one_of(
            st.none(),
            st.decimals(
                min_value=0,
                max_value=999999,
                places=2,
                allow_nan=False,
                allow_infinity=False,
            ),
        ),
        "sold_quantity": st.one_of(st.none(), st.integers(min_value=0, max_value=10**9)),
        "status": st.sampled_from([0, 1]),
    }
)

# 单行客服知识历史数据生成器：title / content 为非空文本（建模去重键），
# 用于覆盖「多张表均插入历史数据」以验证历史数据不被改动。
_csk_row_strategy = st.fixed_dictionaries(
    {
        "shop_pk": st.integers(min_value=1, max_value=10**6),
        "title": st.text(min_size=1, max_size=50),
        "content": st.text(min_size=1, max_size=80),
        "tags": st.one_of(st.none(), st.text(max_size=40)),
        "enabled": st.booleans(),
    }
)


def _snapshot_products(session):
    """读取 product 表全部行的快照（按主键排序），用于前后内容比对。"""
    rows = session.query(Product).order_by(Product.id).all()
    return [
        (
            r.id,
            r.shop_pk,
            r.goods_id,
            r.goods_name,
            None if r.price is None else float(r.price),
            r.sold_quantity,
            r.status,
        )
        for r in rows
    ]


def _snapshot_customer_service_knowledge(session):
    """读取 customer_service_knowledge 表全部行的快照（按主键排序）。"""
    rows = (
        session.query(CustomerServiceKnowledge)
        .order_by(CustomerServiceKnowledge.id)
        .all()
    )
    return [
        (r.id, r.shop_pk, r.title, r.content, r.tags, r.enabled) for r in rows
    ]


@settings(max_examples=100, deadline=None)
@given(
    products=st.lists(_product_row_strategy, max_size=8),
    knowledges=st.lists(_csk_row_strategy, max_size=6),
)
def test_startup_migration_is_idempotent(products, knowledges):
    """Feature: pdd-auto-reply, Property 22: 启动迁移幂等

    对任意已存在的数据库 schema 状态，连续运行启动自检迁移两次：
    - 第二次运行不产生额外结构变更（MigrationResult.changed 为 False）；
    - 任何历史数据行均不被修改或删除（行数与内容保持不变）。
    """
    # 每个 example 使用独立内存库引擎，避免用例间数据串扰。
    engine = create_engine("sqlite:///:memory:", future=True)
    # 隔离全局字典初始化钩子：种子钩子默认绑定全局引擎，不适用于本独立内存引擎，
    # 清空后种子步骤为空操作，专注校验「结构幂等 + 历史数据安全」。
    clear_dict_initializers()
    try:
        migrator = SchemaMigrator(engine=engine, metadata=Base.metadata)

        # 第一次迁移：在空库上建表，预期发生结构变更（建表）。
        first = migrator.run()
        assert first.changed is True
        assert first.created_tables  # 至少建出若干表

        # 在已建好的库中随机插入若干历史数据行（覆盖 product 与
        # customer_service_knowledge 两张表）。product 表含 (shop_pk, goods_id)
        # 唯一约束，故插入前按该业务键去重，避免违反唯一约束（与生产一致）。
        unique_products = list(
            {(row["shop_pk"], row["goods_id"]): row for row in products}.values()
        )
        with Session(engine, expire_on_commit=False, future=True) as session:
            for row in unique_products:
                session.add(Product(**row))
            for row in knowledges:
                session.add(CustomerServiceKnowledge(**row))
            session.commit()

        # 记录第二次迁移前的历史数据快照（行数 + 内容）。
        with Session(engine, expire_on_commit=False, future=True) as session:
            products_before = _snapshot_products(session)
            csk_before = _snapshot_customer_service_knowledge(session)

        # 第二次迁移：schema 已是最新，预期不产生任何结构变更（幂等）。
        second = migrator.run()
        assert second.changed is False
        assert second.created_tables == []
        assert second.added_columns == []
        assert second.added_unique_indexes == []
        assert second.seeded_rows == 0

        # 历史数据行不被修改或删除：行数与逐行内容均保持不变。
        with Session(engine, expire_on_commit=False, future=True) as session:
            products_after = _snapshot_products(session)
            csk_after = _snapshot_customer_service_knowledge(session)

        assert products_after == products_before
        assert csk_after == csk_before
        assert len(products_after) == len(unique_products)
        assert len(csk_after) == len(knowledges)
    finally:
        clear_dict_initializers()
        engine.dispose()

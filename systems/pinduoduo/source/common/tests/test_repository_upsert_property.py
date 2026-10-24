# -*- coding: utf-8 -*-
"""
相同业务键 upsert 幂等属性测试（common.db.repository.Repository.upsert）
========================================================================
本文件用途：以 Hypothesis 属性测试验证通用仓储层「按业务键 upsert」的幂等性，
对应设计文档 Property 6「相同业务键 upsert 幂等」
（Validates: Requirements 3.2, 9.2, 15.4）。

即：对任意实体与其业务键（此处以商品表 product 的 shop_pk + goods_id 为业务键），
以相同业务键连续写入任意多次（次数 ≥ 1）后：
- 该业务键对应的记录数恒为 1（不会重复插入）；
- 其内容等于最后一次写入的非键字段值（后写覆盖先写）。

测试基础设施：使用 SQLite 内存库 + common 模型（参照任务 2.8 验证方式）。
每个 example 使用一个全新的内存引擎并建表，避免用例间数据串扰。
"""
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from common.db.repository import Repository
from common.models.base import Base
from common.models.knowledge_models import Product

# 固定业务键：商品表逻辑唯一键为 shop_pk + goods_id（需求 15.4）。
# 业务键在单个 example 内保持不变，模拟「以相同业务键连续写入」。
_BIZ_KEYS: dict[str, Any] = {"shop_pk": 10086, "goods_id": "G-FIXED-001"}

# 单次写入的非键字段值生成器：覆盖商品名称、价格、已售数量、缩略图等可空字段。
# 价格用有界小数（避免 Numeric 精度问题），数量用非负整数，文本允许 None。
_value_strategy = st.fixed_dictionaries(
    {
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
        "thumb_url": st.one_of(st.none(), st.text(max_size=80)),
        "status": st.sampled_from([0, 1]),
    }
)


@settings(max_examples=100, deadline=None)
@given(values_seq=st.lists(_value_strategy, min_size=1, max_size=8))
def test_upsert_same_biz_keys_is_idempotent(values_seq):
    """Feature: pdd-auto-reply, Property 6: 相同业务键 upsert 幂等

    对任意非键字段值序列（长度 ≥ 1），以相同业务键连续 upsert 后：
    - 该业务键对应的记录数恒为 1；
    - 其内容等于最后一次写入的值。
    """
    # 每个 example 使用独立的内存库引擎并建表，避免用例间数据串扰。
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False, future=True) as session:
            repo: Repository[Product] = Repository(Product, session)

            # 以相同业务键依次写入序列中的每一组值。
            for values in values_seq:
                repo.upsert(biz_keys=_BIZ_KEYS, values=values)
                session.commit()

                # 不变量：无论写入多少次，该业务键对应记录数恒为 1。
                assert repo.count(filters=_BIZ_KEYS) == 1

            # 终态内容应等于最后一次写入的值（后写覆盖先写）。
            last_values = values_seq[-1]
            record = repo.get_by(**_BIZ_KEYS)
            assert record is not None
            # 业务键保持不变。
            assert record.shop_pk == _BIZ_KEYS["shop_pk"]
            assert record.goods_id == _BIZ_KEYS["goods_id"]
            # 逐个非键字段比对最后一次写入值。
            assert record.goods_name == last_values["goods_name"]
            assert record.sold_quantity == last_values["sold_quantity"]
            assert record.thumb_url == last_values["thumb_url"]
            assert record.status == last_values["status"]
            # 价格列为 Numeric(12,2)，SQLite 下回读可能为 Decimal/float，
            # 统一以浮点近似比较，None 单独判定。
            if last_values["price"] is None:
                assert record.price is None
            else:
                assert record.price is not None
                assert abs(float(record.price) - float(last_values["price"])) < 1e-6
    finally:
        engine.dispose()

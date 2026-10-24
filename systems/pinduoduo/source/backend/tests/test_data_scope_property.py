# -*- coding: utf-8 -*-
"""
数据范围隔离属性测试（backend.app.core.data_scope）
==================================================
本文件用途：以 Hypothesis 属性测试验证统一数据范围隔离模块的纯判定逻辑，对应
设计文档 Property 8（数据范围隔离，Validates: Requirements 3.7, 14.1, 22.7）。即：
- 归属范围隔离：非管理员的列表过滤结果中，每条记录的归属者必为「本人」或其被
  显式授权的对象（authorized_owner_ids）；管理员结果原样等于全部输入。
- 个人维度隔离：个人设置按用户维度严格隔离，过滤结果仅含 user_id 等于当前用户
  的记录，不同用户互不可见（且管理员亦不放开）。
"""
from dataclasses import dataclass

from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.data_scope import DataScope, filter_in_scope, filter_personal


@dataclass(frozen=True)
class _Record:
    """测试用归属记录：仅携带归属用户 ID（业务真实记录的最小投影）。"""

    owner_user_id: int | None


# 用户 ID 生成器：使用较小取值域，提升「记录归属命中授权集合」的概率，从而同时
# 覆盖「可见」与「不可见」两类情形（取值域过大几乎总是不命中，覆盖不足）。
_user_id_strategy = st.integers(min_value=1, max_value=8)

# 归属记录生成器：归属用户 ID 可为有效用户 ID，亦可为 None（表示无归属数据）。
_record_strategy = st.builds(
    _Record, owner_user_id=st.one_of(st.none(), _user_id_strategy)
)

# 记录集合生成器：随机长度的记录列表（可空），保持顺序语义以便断言顺序保持。
_records_strategy = st.lists(_record_strategy, max_size=15)


@settings(max_examples=200)
@given(
    is_admin=st.booleans(),
    user_id=st.one_of(st.none(), _user_id_strategy),
    authorized_owner_ids=st.frozensets(_user_id_strategy, max_size=6),
    records=_records_strategy,
    other_user_id=_user_id_strategy,
)
def test_data_scope_isolation(
    is_admin, user_id, authorized_owner_ids, records, other_user_id
):
    """Feature: pdd-auto-reply, Property 8: 数据范围隔离

    对任意用户集合与店铺/个人设置数据：
    - 归属维度（filter_in_scope）：非管理员结果中每条记录的归属者必属于
      {本人} ∪ authorized_owner_ids；管理员结果原样等于全部输入。
    - 个人维度（filter_personal）：结果仅含 user_id 等于当前用户的记录，
      不同用户的个人设置互不可见。
    """
    scope = DataScope(
        is_admin=is_admin,
        user_id=user_id,
        authorized_owner_ids=authorized_owner_ids,
    )

    # --- 归属范围隔离 ---
    filtered = filter_in_scope(scope, records)

    if is_admin:
        # 管理员不受归属限制：结果原样等于全部输入（同元素同顺序）。
        assert filtered == records
    else:
        # 非管理员可见归属集合 = 本人 ∪ 被显式授权对象（本人为空身份时不含本人）。
        allowed = set(authorized_owner_ids)
        if user_id is not None:
            allowed.add(user_id)
        # 结果中每条记录的归属者必落在可见集合内（隐含排除无归属 None 记录）。
        for record in filtered:
            assert record.owner_user_id is not None
            assert record.owner_user_id in allowed
        # 完备性：输入中所有「在范围内」的记录都应被保留（不遗漏、保持顺序）。
        expected = [r for r in records if r.owner_user_id in allowed]
        assert filtered == expected

    # --- 个人维度隔离（不因管理员身份放开，需求 22.7）---
    personal_records = [_Record(owner_user_id=r.owner_user_id) for r in records]
    personal = filter_personal(
        user_id, personal_records, owner_field="owner_user_id"
    )
    # 结果仅含归属等于当前用户的记录；user_id 为 None 时结果必为空。
    for record in personal:
        assert record.owner_user_id == user_id
    expected_personal = (
        []
        if user_id is None
        else [r for r in personal_records if r.owner_user_id == user_id]
    )
    assert personal == expected_personal

    # 不同用户互不可见：当 other_user_id 不等于当前用户时，对方视角看不到本人记录。
    if user_id is not None and other_user_id != user_id:
        other_view = filter_personal(
            other_user_id, personal_records, owner_field="owner_user_id"
        )
        assert all(r.owner_user_id != user_id for r in other_view)

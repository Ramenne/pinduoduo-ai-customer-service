# -*- coding: utf-8 -*-
"""
权限判断一致性属性测试（backend.app.core.permission）
====================================================
本文件用途：以 Hypothesis 属性测试验证统一权限模块纯判定函数
`check_permission` 与授权映射的一致性，对应设计文档 Property 3
（Validates: Requirements 2.4）。即：对任意角色-权限映射（被授予的权限对集合
`granted`）与任意 (资源键, 操作) 查询，`check_permission` 的返回值应「当且仅当」
该映射包含对应权限对时为真。测试用例同时覆盖「命中」（查询取自 granted）与
「不命中」（查询为任意随机对，可能落在集合外）两种情形。
"""
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.permission import AuthContext, check_permission

# 资源键 / 操作 生成器：使用有限字母表的短字符串，提升「随机查询命中已授权对」
# 的概率，从而同时覆盖命中与不命中两类情形（纯随机长字符串几乎总是不命中）。
_token_strategy = st.text(alphabet="abcde", min_size=1, max_size=3)

# 权限对 (resource_key, action) 生成器。
_pair_strategy = st.tuples(_token_strategy, _token_strategy)

# 被授予权限对集合 granted 生成器：随机权限对的集合（可空）。
_granted_strategy = st.frozensets(_pair_strategy, max_size=12)


@settings(max_examples=200)
@given(
    granted=_granted_strategy,
    is_admin=st.booleans(),
    resource_key=_token_strategy,
    action=_token_strategy,
)
def test_check_permission_matches_granted_mapping(
    granted, is_admin, resource_key, action
):
    """Feature: pdd-auto-reply, Property 3: 权限判断与授权映射一致

    对任意角色-权限映射 `granted` 与任意 (资源键, 操作) 请求：
    `check_permission` 返回真，当且仅当该映射包含对应权限对 (resource_key, action)。
    管理员标志不应影响该「当且仅当」语义（管理员权限须显式体现于 granted）。
    """
    context = AuthContext(is_admin=is_admin, granted=granted)

    # 期望值严格以授权映射的包含关系为准。
    expected = (resource_key, action) in granted

    assert check_permission(context, resource_key, action) is expected


@settings(max_examples=200)
@given(
    granted=st.frozensets(_pair_strategy, min_size=1, max_size=12),
    is_admin=st.booleans(),
    extra=_granted_strategy,
    data=st.data(),
)
def test_check_permission_hit_and_miss(granted, is_admin, extra, data):
    """Feature: pdd-auto-reply, Property 3: 权限判断与授权映射一致

    显式覆盖命中与不命中：
    - 命中：从非空 `granted` 中任取一个已授权对，断言判定为真。
    - 不命中：构造一个不在 `granted` 中的权限对，断言判定为假。
    """
    context = AuthContext(is_admin=is_admin, granted=granted)

    # 命中分支：从已授权集合中抽取一个权限对，必返回真。
    hit_resource, hit_action = data.draw(st.sampled_from(sorted(granted)))
    assert check_permission(context, hit_resource, hit_action) is True

    # 不命中分支：从候选集合中过滤出不在 granted 的权限对，必返回假。
    candidates = sorted(extra | {("zzz", "zzz"), ("z", "miss"), ("miss", "z")})
    misses = [pair for pair in candidates if pair not in granted]
    miss_resource, miss_action = data.draw(st.sampled_from(misses))
    assert check_permission(context, miss_resource, miss_action) is False

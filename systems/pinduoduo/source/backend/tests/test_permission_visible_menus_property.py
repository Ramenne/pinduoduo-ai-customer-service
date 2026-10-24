# -*- coding: utf-8 -*-
"""
可见菜单计算属性测试（backend.app.core.permission）
===================================================
本文件用途：以 Hypothesis 属性测试验证统一权限模块中「可见菜单计算」纯函数
``compute_visible_menus`` 的正确性，对应设计文档 Property 4「可见菜单计算正确」，
校验需求 2.5（按授权过滤菜单）、2.9（管理员专属菜单对管理员强制可见）、
21.17（可见菜单结果正确）。

测试策略：
- 统一采用 Hypothesis，单个属性测试，最少 100 次迭代（max_examples=100）。
- 菜单键 / 资源键从较小字符集生成，刻意提高「授权对」与「菜单所需权限对」的
  重合概率，使「授权可见 / 未授权不可见」两类分支都能被有效覆盖。
- 随机生成菜单集合（含 admin_only 标记与可选 resource_key、action）、随机授权
  上下文（is_admin + 已授予权限对集合），调用被测纯函数后对结果做不变式断言
  （不依赖数据库）。
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.permission import AuthContext, MenuItem, compute_visible_menus

# 受限键空间：用少量短键提高菜单所需权限对与授权对的重合概率，
# 从而覆盖「已授权可见」「未授权不可见」两条关键分支。
_key_strategy = st.sampled_from(["m1", "m2", "m3", "m4", "m5"])
# 操作集合：与默认 view 混合，覆盖菜单 action 与授权 action 的匹配 / 不匹配。
_action_strategy = st.sampled_from(["view", "create", "update"])


# 单个菜单项生成器：随机菜单键、是否仅管理员可见、可选资源键（None 时取菜单键）、操作。
_menu_item_strategy = st.builds(
    MenuItem,
    menu_key=_key_strategy,
    admin_only=st.booleans(),
    resource_key=st.one_of(st.none(), _key_strategy),
    action=_action_strategy,
)

# 菜单集合生成器：0~8 个菜单，按 menu_key 唯一（menu_key 是唯一标识，
# 同键出现多条且属性冲突在真实场景不会发生；保持唯一可使逐菜单不变式断言明确）。
_menus_strategy = st.lists(
    _menu_item_strategy, min_size=0, max_size=8, unique_by=lambda m: m.menu_key
)

# 授权权限对生成器：(resource_key, action) 对集合。
_granted_strategy = st.frozensets(
    st.tuples(_key_strategy, _action_strategy), max_size=8
)


# Feature: pdd-auto-reply, Property 4: 可见菜单计算正确
# 对任意菜单集合与任意用户，可见菜单计算结果必不包含该用户无权访问的菜单；
# 且当用户为管理员时，结果必包含全部标记为「仅管理员可见」的菜单。
# Validates: Requirements 2.5, 2.9, 21.17
@settings(max_examples=100, deadline=None)
@given(
    is_admin=st.booleans(),
    granted=_granted_strategy,
    menus=_menus_strategy,
)
def test_compute_visible_menus_correct(
    is_admin: bool,
    granted: frozenset[tuple[str, str]],
    menus: list[MenuItem],
) -> None:
    """Property 4：结果不含无权访问的菜单；管理员含全部 admin_only 菜单。"""
    context = AuthContext(is_admin=is_admin, granted=granted)
    visible = compute_visible_menus(context, menus)
    visible_set = set(visible)

    # 结果应无重复（去重不变式）。
    assert len(visible) == len(visible_set)

    # 逐菜单核对「不包含无权访问的菜单」这一核心不变式。
    for menu in menus:
        required_pair = (menu.resource_key or menu.menu_key, menu.action)
        if menu.admin_only:
            # admin_only 菜单：仅管理员可见；非管理员绝不应出现。
            if not is_admin:
                assert menu.menu_key not in visible_set
        elif not is_admin:
            # 非管理员：未被授予该菜单所需权限的普通菜单不应出现。
            if required_pair not in granted:
                assert menu.menu_key not in visible_set

    # 管理员强制可见：结果必含全部 admin_only 菜单键。
    if is_admin:
        admin_only_keys = {menu.menu_key for menu in menus if menu.admin_only}
        assert admin_only_keys <= visible_set

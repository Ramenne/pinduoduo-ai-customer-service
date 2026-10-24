# -*- coding: utf-8 -*-
"""
backend.app.core.permission —— 统一权限判断模块（集中式）
========================================================
本文件用途：按《开发规范》第 42 条「权限判断统一逻辑集中」，为 backend 服务
提供唯一的权限判断入口，禁止权限逻辑散落各处。覆盖两类能力：

- ``check(user, resource_key, action) -> bool``：判断某用户对 (资源键, 操作)
  是否被授权。授权当且仅当其角色-权限映射包含该 (resource_key, action)
  （需求 2.3 / 2.4，Property 3）。
- ``visible_menus(user) -> [menu_key]``：计算用户可见菜单键列表。规则：
  结果不含用户无权访问的菜单；管理员（is_admin）时结果必含全部标记为
  ``admin_only`` 的菜单（强制可见，需求 2.9 / 21.17）；非管理员按授权过滤
  （需求 2.5，Property 4）。

设计要点（为属性测试 4.5 Property3 / 4.6 Property4 服务）：
- **纯判定逻辑与数据库分离**：核心判定为不依赖数据库的纯函数
  （``check_permission`` / ``compute_visible_menus`` / ``is_menu_visible``），
  输入为「授权上下文 ``AuthContext`` + 菜单集合 ``MenuItem``」等纯数据结构，
  便于 Hypothesis 直接生成输入做属性测试，无需连接 MySQL。
- **DB 加载辅助单独提供**：``load_auth_context`` / ``load_menu_items`` 负责从
  数据库（SysUser/SysRole/SysPermission/SysRolePermission/SysMenu）装配纯数据
  结构；``check`` / ``visible_menus`` 为对外便捷封装，组合「加载 + 纯判定」。

约束：导入置顶（规范 51）；中文注释（规范 37）；本文件 ≤500 行（规范 35）；
仅在 backend 内实现并通过 ``import common`` 复用公共库（规范 34/52）。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from common.db.repository import Repository
from common.models.user_models import (
    SysMenu,
    SysPermission,
    SysRole,
    SysRolePermission,
    SysUser,
)

# 权限对：以 (资源键, 操作) 唯一标识一个可授权的操作点。
PermissionPair = tuple[str, str]

# 菜单与权限的约定动作：菜单的「可见」对应资源的「view」操作。
DEFAULT_MENU_ACTION = "view"


# ----------------------------------------------------------------------
# 纯数据结构（不依赖数据库，供纯判定函数与属性测试直接构造）
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class AuthContext:
    """用户授权上下文（纯数据，DB 无关）。

    封装做权限判断所需的最小信息：是否管理员、被授予的权限对集合。
    由 ``load_auth_context`` 从数据库装配，或在属性测试中直接构造。

    Attributes:
        is_admin: 该用户所属角色是否为管理员角色（需求 2.9）。
        granted: 被授予的 (resource_key, action) 权限对集合（不可变）。
    """

    is_admin: bool = False
    granted: frozenset[PermissionPair] = field(default_factory=frozenset)


@dataclass(frozen=True)
class MenuItem:
    """菜单项（纯数据，DB 无关）。

    描述一个菜单及其可见性所需的权限约定。菜单的「可访问」对应资源
    ``resource_key`` 的 ``action`` 操作；``resource_key`` 缺省时以 ``menu_key``
    充当资源键（菜单键与资源键同名的常见约定）。

    Attributes:
        menu_key: 菜单键（唯一标识，用于路由与权限映射）。
        admin_only: 是否仅管理员可见（管理员强制可见，需求 2.9）。
        resource_key: 该菜单对应的资源键；None 时取 ``menu_key``。
        action: 该菜单可见所需的操作，默认 ``view``。
    """

    menu_key: str
    admin_only: bool = False
    resource_key: str | None = None
    action: str = DEFAULT_MENU_ACTION

    def required_pair(self) -> PermissionPair:
        """返回该菜单可见所需的 (resource_key, action) 权限对。"""
        return (self.resource_key or self.menu_key, self.action)


# ----------------------------------------------------------------------
# 纯判定逻辑（不依赖数据库 —— Property 3 / Property 4 直接验证此层）
# ----------------------------------------------------------------------
def check_permission(
    context: AuthContext, resource_key: str, action: str
) -> bool:
    """纯判定：用户对 (resource_key, action) 是否被授权。

    授权当且仅当其角色-权限映射包含对应权限对（需求 2.4，Property 3）。
    本函数严格以授权映射为准，不对管理员做隐式放行（管理员的全部权限应通过
    其角色-权限映射体现于 ``context.granted``），以保证「当且仅当」语义。

    Args:
        context: 用户授权上下文（含 ``granted`` 权限对集合）。
        resource_key: 受保护资源键。
        action: 操作（view/create/update/disable 等）。

    Returns:
        True 表示被授权；否则 False。
    """
    return (resource_key, action) in context.granted


def is_menu_visible(
    context: AuthContext,
    menu: MenuItem,
) -> bool:
    """纯判定：某菜单对该用户是否可见。

    规则（需求 2.5 / 2.9 / 21.17）：
    - ``admin_only`` 菜单：当且仅当用户为管理员时可见（对管理员强制可见，
      需求 2.9）；非管理员一律不可见。
    - 普通菜单：管理员可见，非管理员当且仅当被授予该菜单所需权限时可见。

    Args:
        context: 用户授权上下文。
        menu: 待判定的菜单项。

    Returns:
        True 表示该菜单对该用户可见。
    """
    if menu.admin_only:
        # 仅管理员可见菜单：管理员强制可见（需求 2.9）
        return context.is_admin
    if context.is_admin:
        # 管理员可见全部普通菜单
        return True
    # 非管理员：当且仅当被授予该菜单所需权限时可见
    return menu.required_pair() in context.granted


def compute_visible_menus(
    context: AuthContext,
    menus: Iterable[MenuItem],
) -> list[str]:
    """纯判定：计算用户可见菜单键列表（保持输入顺序）。

    保证：结果不含用户无权访问的菜单；管理员时结果必含全部 ``admin_only``
    菜单；普通用户仅含被授权的菜单（需求 2.5 / 2.9 / 21.17，Property 4）。

    Args:
        context: 用户授权上下文。
        menus: 菜单集合（按期望展示顺序）。

    Returns:
        可见菜单键列表（去重，按输入顺序保留首次出现）。
    """
    visible: list[str] = []
    seen: set[str] = set()
    for menu in menus:
        if menu.menu_key in seen:
            continue
        if is_menu_visible(context, menu):
            visible.append(menu.menu_key)
            seen.add(menu.menu_key)
    return visible


# ----------------------------------------------------------------------
# DB 加载辅助（从数据库装配纯数据结构 —— 与纯判定逻辑分离）
# ----------------------------------------------------------------------
def _resolve_role_id(user: SysUser | int | None) -> int | None:
    """从入参解析角色 id：兼容 SysUser 实例与直接传入的 role_id。"""
    if user is None:
        return None
    if isinstance(user, SysUser):
        return user.role_id
    # 允许直接传入 role_id（整数），便于内部复用
    return int(user)


def load_auth_context(user: SysUser | int | None, session: Session) -> AuthContext:
    """从数据库装配某用户的授权上下文（is_admin + 权限对集合）。

    依据用户角色 id 读取角色（判定 ``is_admin``）与该角色的全部权限映射，
    展开为 (resource_key, action) 权限对集合。停用/缺失角色按「无权限」处理。

    Args:
        user: SysUser 实例或角色 id；None 视为无角色。
        session: 当前事务性会话。

    Returns:
        装配完成的 ``AuthContext``（纯数据结构）。
    """
    role_id = _resolve_role_id(user)
    if role_id is None:
        return AuthContext(is_admin=False, granted=frozenset())

    # 会话级缓存：同一请求（同一 DB 会话）内，相同角色的授权上下文只装配一次。
    # 典型请求会先经 permission.check 判权、再经 build_data_scope 装配数据范围，
    # 二者都会调用本函数；缓存可避免重复的角色 / 权限查询往返（远程库下尤其明显）。
    cache = session.info.setdefault("_auth_context_cache", {})
    cached = cache.get(role_id)
    if cached is not None:
        return cached

    role = Repository(SysRole, session).get(role_id)
    is_admin = bool(role.is_admin) if role is not None else False

    # 读取角色-权限映射，再据 permission_id 批量解析具体 (resource_key, action)。
    # 用单次 IN 查询批量取权限，避免「逐条 get」的 N+1 查询（远程数据库下 N+1
    # 会带来大量网络往返，严重拖慢响应）。
    # 仅认 enabled=True 的映射：取消授权经软删除（enabled=False）实现，
    # 已取消的映射不计入授权（规范 11 禁止物理删除，故以状态字段过滤）。
    mappings = Repository(SysRolePermission, session).list(
        filters={"role_id": role_id, "enabled": True}, order_by=False
    )
    permission_ids = [m.permission_id for m in mappings]
    granted: set[PermissionPair] = set()
    if permission_ids:
        permissions = Repository(SysPermission, session).list(
            order_by=False,
            extra_conditions=[SysPermission.id.in_(permission_ids)],
        )
        granted = {(p.resource_key, p.action) for p in permissions}

    context = AuthContext(is_admin=is_admin, granted=frozenset(granted))
    # 写入会话级缓存，供同一请求内后续判权 / 数据范围装配复用。
    cache[role_id] = context
    return context


def load_menu_items(session: Session) -> list[MenuItem]:
    """从数据库装配菜单集合（按排序序号升序）。

    将 ``SysMenu`` 行转换为纯数据 ``MenuItem``；菜单键直接充当资源键
    （菜单可见对应该资源的 ``view`` 操作）。

    Args:
        session: 当前事务性会话。

    Returns:
        ``MenuItem`` 列表（按 ``order_no`` 升序）。
    """
    rows = Repository(SysMenu, session).list(
        order_by=SysMenu.order_no, desc_order=False
    )
    return [
        MenuItem(menu_key=row.menu_key, admin_only=bool(row.admin_only))
        for row in rows
    ]


# ----------------------------------------------------------------------
# 对外便捷封装（组合「DB 加载 + 纯判定」，供业务路由调用 —— 规范 42）
# ----------------------------------------------------------------------
def check(
    user: SysUser | int | None,
    resource_key: str,
    action: str,
    *,
    session: Session,
) -> bool:
    """统一权限判断入口：用户对 (resource_key, action) 是否被授权。

    先从数据库装配授权上下文，再委托纯判定 ``check_permission``。业务路由统一
    经此函数判权，禁止在各处自行实现判权逻辑（规范 42）。

    Args:
        user: SysUser 实例或角色 id。
        resource_key: 受保护资源键。
        action: 操作。
        session: 当前事务性会话（关键字参数）。

    Returns:
        True 表示被授权；否则 False。
    """
    context = load_auth_context(user, session)
    return check_permission(context, resource_key, action)


def visible_menus(
    user: SysUser | int | None,
    *,
    session: Session,
) -> list[str]:
    """统一可见菜单计算入口：返回用户可见菜单键列表。

    先从数据库装配授权上下文与菜单集合，再委托纯判定
    ``compute_visible_menus``。管理员专属菜单对管理员强制可见（需求 2.9）。

    Args:
        user: SysUser 实例或角色 id。
        session: 当前事务性会话（关键字参数）。

    Returns:
        可见菜单键列表（按菜单排序）。
    """
    context = load_auth_context(user, session)
    menus = load_menu_items(session)
    return compute_visible_menus(context, menus)


def granted_view_resources(
    user: SysUser | int | None,
    *,
    session: Session,
) -> tuple[bool, list[str]]:
    """返回当前用户「是否管理员」与「被授予 view 操作的资源键列表」（需求 2.6）。

    前端据此按菜单所需资源键过滤可见菜单：管理员强制可见全部（resources 为全集
    意义不大，前端以 is_admin 直接放行）；普通用户仅渲染其被授予 view 的资源对应
    的菜单。资源键以 (resource_key, "view") 是否在授权集合中为准（与
    ``check_permission`` 同源），保证与接口级判权一致。

    Args:
        user: SysUser 实例或角色 id。
        session: 当前事务性会话（关键字参数）。

    Returns:
        二元组 (is_admin, 资源键列表)；资源键去重、按字母序，便于稳定输出。
    """
    context = load_auth_context(user, session)
    resources = sorted({res for (res, action) in context.granted if action == DEFAULT_MENU_ACTION})
    return context.is_admin, resources


__all__ = [
    "PermissionPair",
    "DEFAULT_MENU_ACTION",
    "AuthContext",
    "MenuItem",
    "check_permission",
    "is_menu_visible",
    "compute_visible_menus",
    "load_auth_context",
    "load_menu_items",
    "check",
    "visible_menus",
    "granted_view_resources",
]

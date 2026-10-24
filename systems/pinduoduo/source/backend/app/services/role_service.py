# -*- coding: utf-8 -*-
"""
backend.app.services.role_service —— 角色与权限分配业务服务
==========================================================
本文件用途：实现 backend 服务的「角色管理与权限分配」业务逻辑，供 roles 路由
复用，补齐需求 2（用户、角色与权限管理）中此前缺失的角色增删改与权限配置能力：

- ``create_role(...)``：新增角色（角色名全局唯一，非管理员）。
- ``update_role(...)``：修改角色名 / 启停用状态。
- ``set_role_status(...)``：启用 / 停用角色（停用经状态字段逻辑删除，规范 11）。
- ``set_default_role(...)``：设置某角色为「注册默认角色」（同一时刻仅一个）。
- ``assign_permissions(...)``：为角色重设权限集合（取消授权经软删除 enabled=False
  实现，禁止物理删除映射 —— 规范 11）。
- ``get_role_permissions(...)``：查询角色当前已授予的权限 id 列表。
- ``list_permissions(...)``：列出系统全部权限点（按资源分组，附中文名，供分配勾选）。

实现约束（开发规范）：
- 统一响应体由 common.schemas.common 构造，HTTP 恒 200（规范 1-3）。
- 所有数据访问经 common.db.repository 参数化查询，禁止拼接 SQL（规范 16）。
- 枚举中文名从数据字典表查出（规范 15）：资源用 permission_resource、操作用
  permission_action 字典类型。
- 管理员角色（is_admin=True）为系统内置，禁止改名 / 停用 / 改权限 / 设为默认，
  避免破坏管理员的完整权限（规范 41）。
- 禁止物理删除业务数据（规范 11 / 需求 24.6）：角色停用经状态字段，权限取消经
  映射软删除。
- 导入置顶（规范 51）；中文注释（规范 37）；单文件 ≤500 行（规范 35）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.business_codes import CODE_NOT_FOUND, CODE_PARAM_ERROR
from common.db.repository import Repository
from common.models.user_models import (
    SysPermission,
    SysRole,
    SysRolePermission,
)
from common.schemas.common import ApiResponse, error_response, success_response
from common.services.dict_service import DictService

# 角色启停用状态值（与 SysRole.status 约定一致：1=启用，0=停用）。
ROLE_STATUS_ENABLED: int = 1
ROLE_STATUS_DISABLED: int = 0

# 权限枚举中文名所用的数据字典类型（规范 15）。
_DICT_RESOURCE: str = "permission_resource"
_DICT_ACTION: str = "permission_action"


# ----------------------------------------------------------------------
# 序列化
# ----------------------------------------------------------------------
def serialize_role(role: SysRole) -> Dict[str, Any]:
    """将角色模型序列化为对外字典。

    Args:
        role: 角色模型实例。

    Returns:
        角色信息字典（含是否管理员 / 是否默认 / 状态）。
    """
    return {
        "id": role.id,
        "role_name": role.role_name,
        "is_admin": bool(role.is_admin),
        "is_default": bool(getattr(role, "is_default", False)),
        "status": role.status,
    }


# ----------------------------------------------------------------------
# 角色新增 / 修改 / 启停用 / 默认
# ----------------------------------------------------------------------
def create_role(
    session: Session,
    *,
    role_name: str,
    operator_id: Optional[int] = None,
) -> ApiResponse:
    """新增角色（需求 2.3）。

    角色名全局唯一；新建角色一律为非管理员（管理员角色系统内置，不经此创建）。
    新角色默认无任何权限，需经 ``assign_permissions`` 分配。

    Args:
        session: 数据库会话。
        role_name: 角色名称（全局唯一）。
        operator_id: 操作人用户 ID（创建人审计）。

    Returns:
        统一响应体：成功返回新角色信息。
    """
    if not role_name or not role_name.strip():
        return error_response(CODE_PARAM_ERROR, "角色名称不能为空")

    role_name = role_name.strip()
    role_repo = Repository(SysRole, session)
    if role_repo.get_by(role_name=role_name) is not None:
        return error_response(CODE_PARAM_ERROR, "角色名称已存在")

    role = role_repo.create(
        role_name=role_name,
        is_admin=False,
        is_default=False,
        status=ROLE_STATUS_ENABLED,
        created_by=operator_id,
    )
    return success_response(data=serialize_role(role), message="创建成功")


def update_role(
    session: Session,
    role_id: int,
    *,
    role_name: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> ApiResponse:
    """修改角色名称 / 启停用状态（需求 2.3）。

    管理员角色为系统内置，禁止修改。改名时校验新名称未被占用。

    Args:
        session: 数据库会话。
        role_id: 目标角色 ID。
        role_name: 新角色名称；None 表示不修改。
        enabled: 启停用；True=启用，False=停用；None 表示不修改。

    Returns:
        统一响应体：成功返回更新后的角色信息。
    """
    role_repo = Repository(SysRole, session)
    role = role_repo.get(role_id)
    if role is None:
        return error_response(CODE_NOT_FOUND, "目标角色不存在")
    if role.is_admin:
        return error_response(CODE_PARAM_ERROR, "管理员角色为系统内置，不可修改")

    values: Dict[str, Any] = {}
    if role_name is not None:
        new_name = role_name.strip()
        if not new_name:
            return error_response(CODE_PARAM_ERROR, "角色名称不能为空")
        # 改名唯一性校验：排除自身。
        existing = role_repo.get_by(role_name=new_name)
        if existing is not None and existing.id != role_id:
            return error_response(CODE_PARAM_ERROR, "角色名称已存在")
        values["role_name"] = new_name
    if enabled is not None:
        values["status"] = ROLE_STATUS_ENABLED if enabled else ROLE_STATUS_DISABLED

    if not values:
        return error_response(CODE_PARAM_ERROR, "未提供任何待更新字段")

    role_repo.update(role_id, **values)
    return success_response(data=serialize_role(role), message="更新成功")


def set_role_status(session: Session, role_id: int, enabled: bool) -> ApiResponse:
    """启用或停用角色（需求 2.3）。停用经状态字段逻辑删除（规范 11）。

    Args:
        session: 数据库会话。
        role_id: 目标角色 ID。
        enabled: True=启用，False=停用。

    Returns:
        统一响应体：成功返回更新后的角色信息。
    """
    role_repo = Repository(SysRole, session)
    role = role_repo.get(role_id)
    if role is None:
        return error_response(CODE_NOT_FOUND, "目标角色不存在")
    if role.is_admin:
        return error_response(CODE_PARAM_ERROR, "管理员角色为系统内置，不可停用")
    if not enabled and getattr(role, "is_default", False):
        return error_response(CODE_PARAM_ERROR, "默认注册角色不可停用，请先切换默认角色")

    role_repo.update(role_id, status=ROLE_STATUS_ENABLED if enabled else ROLE_STATUS_DISABLED)
    return success_response(
        data=serialize_role(role), message="已启用" if enabled else "已停用"
    )


def set_default_role(session: Session, role_id: int) -> ApiResponse:
    """设置某角色为「注册默认角色」，同一时刻仅一个默认角色（需求 2.3 / 规范 41）。

    管理员角色不可设为默认（避免注册用户获得管理员权限）；停用角色不可设为默认。
    设置时先清除其它角色的默认标记，再置当前角色为默认。

    Args:
        session: 数据库会话。
        role_id: 目标角色 ID。

    Returns:
        统一响应体：成功返回更新后的角色信息。
    """
    role_repo = Repository(SysRole, session)
    role = role_repo.get(role_id)
    if role is None:
        return error_response(CODE_NOT_FOUND, "目标角色不存在")
    if role.is_admin:
        return error_response(CODE_PARAM_ERROR, "管理员角色不可设为默认注册角色")
    if role.status != ROLE_STATUS_ENABLED:
        return error_response(CODE_PARAM_ERROR, "停用的角色不可设为默认注册角色")

    # 清除其它角色的默认标记（仅更新当前为默认的角色，避免全表更新）。
    current_defaults = role_repo.list(filters={"is_default": True}, order_by=False)
    for other in current_defaults:
        if other.id != role_id:
            role_repo.update(other.id, is_default=False)
    role_repo.update(role_id, is_default=True)
    return success_response(data=serialize_role(role), message="已设为默认注册角色")


# ----------------------------------------------------------------------
# 权限分配
# ----------------------------------------------------------------------
def assign_permissions(
    session: Session,
    role_id: int,
    permission_ids: List[int],
) -> ApiResponse:
    """为角色重设权限集合（需求 2.3 / 2.4）。

    以传入的 ``permission_ids`` 作为目标权限全集：目标内的权限确保有效（不存在
    则新建映射、被软删除则恢复）；目标外的现有有效映射置为软删除（enabled=False，
    禁止物理删除 —— 规范 11）。管理员角色权限固定为全部，禁止经此修改。

    Args:
        session: 数据库会话。
        role_id: 目标角色 ID。
        permission_ids: 目标权限 id 列表（去重后作为权限全集）。

    Returns:
        统一响应体：成功返回角色当前权限 id 列表。
    """
    role_repo = Repository(SysRole, session)
    role = role_repo.get(role_id)
    if role is None:
        return error_response(CODE_NOT_FOUND, "目标角色不存在")
    if role.is_admin:
        return error_response(CODE_PARAM_ERROR, "管理员角色拥有全部权限，无需也不可修改")

    # 目标权限 id 去重，并校验权限点存在（过滤非法 id）。
    target_ids = set(int(pid) for pid in permission_ids or [])
    if target_ids:
        valid_perms = Repository(SysPermission, session).list(
            order_by=False,
            extra_conditions=[SysPermission.id.in_(list(target_ids))],
        )
        valid_ids = {p.id for p in valid_perms}
        target_ids = target_ids & valid_ids

    mapping_repo = Repository(SysRolePermission, session)
    existing_maps = mapping_repo.list(filters={"role_id": role_id}, order_by=False)
    existing_by_pid = {m.permission_id: m for m in existing_maps}

    # 1) 目标内：确保有效（新建或恢复）。
    for pid in target_ids:
        m = existing_by_pid.get(pid)
        if m is None:
            mapping_repo.create(role_id=role_id, permission_id=pid, enabled=True)
        elif not m.enabled:
            mapping_repo.update(m.id, enabled=True)
    # 2) 目标外：现有有效映射软删除（enabled=False）。
    for pid, m in existing_by_pid.items():
        if pid not in target_ids and m.enabled:
            mapping_repo.update(m.id, enabled=False)

    return success_response(
        data={"role_id": role_id, "permission_ids": sorted(target_ids)},
        message="权限已保存",
    )


def get_role_permissions(session: Session, role_id: int) -> ApiResponse:
    """查询角色当前已授予（有效）的权限 id 列表（需求 2.3 / 2.4）。

    管理员角色拥有系统全部权限，直接返回全部权限 id。

    Args:
        session: 数据库会话。
        role_id: 角色 ID。

    Returns:
        统一响应体：data={role_id, permission_ids}。
    """
    role = Repository(SysRole, session).get(role_id)
    if role is None:
        return error_response(CODE_NOT_FOUND, "目标角色不存在")

    if role.is_admin:
        all_perms = Repository(SysPermission, session).list(order_by=False)
        ids = sorted(p.id for p in all_perms)
        return success_response(
            data={"role_id": role_id, "permission_ids": ids}, message="查询成功"
        )

    maps = Repository(SysRolePermission, session).list(
        filters={"role_id": role_id, "enabled": True}, order_by=False
    )
    ids = sorted(m.permission_id for m in maps)
    return success_response(
        data={"role_id": role_id, "permission_ids": ids}, message="查询成功"
    )


def list_permissions(session: Session) -> ApiResponse:
    """列出系统全部权限点，按资源分组并附中文名（供权限分配界面勾选，需求 2.3）。

    资源与操作中文名从数据字典表查出（规范 15）：资源用 permission_resource、
    操作用 permission_action；字典缺失项回退展示原始 key。

    Args:
        session: 数据库会话。

    Returns:
        统一响应体：data={groups: [{resource_key, resource_name, actions: [
        {permission_id, action, action_name}]}]}。
    """
    dict_service = DictService(session)
    resource_labels = dict_service.get_label_map(_DICT_RESOURCE)
    action_labels = dict_service.get_label_map(_DICT_ACTION)

    permissions = Repository(SysPermission, session).list(order_by=False)
    # 按资源聚合，资源内按操作聚合。
    groups: Dict[str, Dict[str, Any]] = {}
    for perm in permissions:
        group = groups.setdefault(
            perm.resource_key,
            {
                "resource_key": perm.resource_key,
                "resource_name": resource_labels.get(perm.resource_key, perm.resource_key),
                "actions": [],
            },
        )
        group["actions"].append(
            {
                "permission_id": perm.id,
                "action": perm.action,
                "action_name": action_labels.get(perm.action, perm.action),
            }
        )

    # 资源按字典排序（permission_resource 的 order_no）输出，未在字典中的排末尾。
    resource_order = list(resource_labels.keys())

    def _resource_sort_key(resource_key: str) -> int:
        return resource_order.index(resource_key) if resource_key in resource_order else len(resource_order)

    action_order = list(action_labels.keys())

    def _action_sort_key(action: str) -> int:
        return action_order.index(action) if action in action_order else len(action_order)

    ordered_groups: List[Dict[str, Any]] = []
    for resource_key in sorted(groups.keys(), key=_resource_sort_key):
        group = groups[resource_key]
        group["actions"].sort(key=lambda a: _action_sort_key(a["action"]))
        ordered_groups.append(group)

    return success_response(data={"groups": ordered_groups}, message="查询成功")


__all__ = [
    "ROLE_STATUS_ENABLED",
    "ROLE_STATUS_DISABLED",
    "serialize_role",
    "create_role",
    "update_role",
    "set_role_status",
    "set_default_role",
    "assign_permissions",
    "get_role_permissions",
    "list_permissions",
]

# -*- coding: utf-8 -*-
"""
backend.app.services.user_service —— 用户与角色管理业务服务
==========================================================
本文件用途：实现 backend 服务的「用户与角色管理」业务逻辑，供 users 路由复用，
满足需求 2（用户、角色与权限管理）：

- ``create_user(...)``：管理员创建用户，密码经哈希存储，指定角色并持久化
  （需求 2.1）；用户名全局唯一，重复则返回失败。
- ``update_user_role(...)``：修改用户角色，变更在该用户下次鉴权时生效
  （需求 2.2，鉴权依赖每次按 role 加载权限上下文）。
- ``set_user_status(...)`` / ``disable_user`` / ``enable_user``：启用 / 停用用户。
  停用通过状态字段（status=0）逻辑删除实现，禁止物理删除（需求 2.8）；停用后
  该用户「现有登录令牌即失效」——鉴权依赖 ``get_current_user`` 在每次请求时校验
  ``status``，停用用户被直接拒绝（需求 2.7），无需逐一登记其令牌 jti。
- ``list_users(...)``：用户列表后端分页查询（默认每页 20 条，需求 2.1 配套）。
- ``list_roles(...)`` / ``get_role(...)``：角色列表与查询（基础角色管理）。

实现约束（开发规范）：
- 统一响应体由 common.schemas.common 构造，HTTP 恒 200（规范 1-3）。
- 所有数据访问经 common.db.repository 的参数化查询，禁止拼接 SQL（规范 16）。
- 对外返回的用户信息经 common.schemas.sanitize 脱敏，绝不返回密码明文 / 哈希
  （需求 1.6 / 2.x）。
- 密码哈希复用 common.utils.security.hash_password（规范 36/52）。
- 禁止物理删除业务数据，停用经状态字段实现（规范 11 / 需求 2.8 / 24.6）。
- 导入置顶（规范 51）；中文注释（规范 37）；单文件 ≤500 行（规范 35）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.business_codes import (
    CODE_NOT_FOUND,
    CODE_PARAM_ERROR,
)
from app.services.auth_service import serialize_user
from common.db.repository import Repository
from common.models.user_models import SysRole, SysUser
from common.schemas.common import ApiResponse, error_response, success_response
from common.utils.security import hash_password

# 用户启停用状态值（与 SysUser.status 约定一致：1=启用，0=停用）。
USER_STATUS_ENABLED: int = 1
USER_STATUS_DISABLED: int = 0


# ----------------------------------------------------------------------
# 序列化辅助
# ----------------------------------------------------------------------
def _serialize_user_with_role(session: Session, user: SysUser) -> Dict[str, Any]:
    """将用户连同其角色信息脱敏序列化为对外字典（复用 auth_service）。

    Args:
        session: 数据库会话。
        user: 用户模型实例。

    Returns:
        脱敏后的用户信息字典（含角色名与是否管理员，若有角色）。
    """
    role: Optional[SysRole] = None
    if user.role_id is not None:
        role = Repository(SysRole, session).get(user.role_id)
    return serialize_user(user, role)


def serialize_role(role: SysRole) -> Dict[str, Any]:
    """将角色模型序列化为对外字典。

    Args:
        role: 角色模型实例。

    Returns:
        角色信息字典。
    """
    return {
        "id": role.id,
        "role_name": role.role_name,
        "is_admin": bool(role.is_admin),
        "status": role.status,
    }


# ----------------------------------------------------------------------
# 用户创建（需求 2.1）
# ----------------------------------------------------------------------
def create_user(
    session: Session,
    username: str,
    password: str,
    role_id: Optional[int] = None,
    *,
    operator_id: Optional[int] = None,
    wechat: Optional[str] = None,
    qq: Optional[str] = None,
) -> ApiResponse:
    """创建用户并指定角色，密码经哈希存储（需求 2.1）。

    校验：用户名 / 密码不可为空；用户名全局唯一（已存在则失败）；指定的角色
    必须存在。成功后持久化用户记录并返回脱敏后的用户信息。

    Args:
        session: 数据库会话。
        username: 登录用户名（全局唯一）。
        password: 明文密码（仅用于哈希，不落库明文）。
        role_id: 指定角色 ID；None 表示暂不分配角色。
        operator_id: 操作人（管理员）用户 ID，作为创建人审计字段。
        wechat: 可选个人联系方式（微信）。
        qq: 可选个人联系方式（QQ）。

    Returns:
        统一响应体：成功返回 data=脱敏用户信息；失败返回对应中文提示。
    """
    # 入参校验：用户名与密码均不可为空。
    if not username or not username.strip():
        return error_response(CODE_PARAM_ERROR, "用户名不能为空")
    if not password:
        return error_response(CODE_PARAM_ERROR, "密码不能为空")

    username = username.strip()
    user_repo = Repository(SysUser, session)

    # 用户名全局唯一校验（需求 2.1 配套，避免重复账号）。
    if user_repo.get_by(username=username) is not None:
        return error_response(CODE_PARAM_ERROR, "用户名已存在")

    # 指定角色时校验角色存在性。
    if role_id is not None:
        if Repository(SysRole, session).get(role_id) is None:
            return error_response(CODE_NOT_FOUND, "指定的角色不存在")

    # 密码哈希存储（绝不存明文，需求 1.6）。
    user = user_repo.create(
        username=username,
        password_hash=hash_password(password),
        role_id=role_id,
        status=USER_STATUS_ENABLED,
        wechat=wechat,
        qq=qq,
        created_by=operator_id,
    )

    return success_response(
        data=_serialize_user_with_role(session, user),
        message="创建成功",
    )


# ----------------------------------------------------------------------
# 修改用户角色（需求 2.2）
# ----------------------------------------------------------------------
def update_user_role(
    session: Session,
    user_id: int,
    role_id: Optional[int],
) -> ApiResponse:
    """修改用户角色（需求 2.2）。

    更新用户的 ``role_id``，变更在该用户下次鉴权时生效（鉴权依赖每次按角色
    加载权限上下文，故无需主动刷新会话）。指定的角色必须存在。

    Args:
        session: 数据库会话。
        user_id: 目标用户 ID。
        role_id: 新角色 ID；None 表示清除角色。

    Returns:
        统一响应体：成功返回更新后的脱敏用户信息。
    """
    user_repo = Repository(SysUser, session)
    user = user_repo.get(user_id)
    if user is None:
        return error_response(CODE_NOT_FOUND, "目标用户不存在")

    # 指定角色时校验角色存在性。
    if role_id is not None:
        if Repository(SysRole, session).get(role_id) is None:
            return error_response(CODE_NOT_FOUND, "指定的角色不存在")

    user_repo.update(user_id, role_id=role_id)
    return success_response(
        data=_serialize_user_with_role(session, user),
        message="角色已更新",
    )


# ----------------------------------------------------------------------
# 启停用（停用即逻辑删除 + 令牌失效，需求 2.7 / 2.8）
# ----------------------------------------------------------------------
def set_user_status(
    session: Session,
    user_id: int,
    enabled: bool,
) -> ApiResponse:
    """启用或停用用户（需求 2.7 / 2.8）。

    停用通过状态字段 status=0 逻辑删除实现，禁止物理删除（需求 2.8）。停用后
    该用户现有登录令牌即失效——鉴权依赖在每次请求时校验 status，停用用户被直接
    拒绝鉴权（需求 2.7）。

    Args:
        session: 数据库会话。
        user_id: 目标用户 ID。
        enabled: True=启用，False=停用。

    Returns:
        统一响应体：成功返回更新后的脱敏用户信息。
    """
    user_repo = Repository(SysUser, session)
    user = user_repo.get(user_id)
    if user is None:
        return error_response(CODE_NOT_FOUND, "目标用户不存在")

    new_status = USER_STATUS_ENABLED if enabled else USER_STATUS_DISABLED
    user_repo.update(user_id, status=new_status)
    message = "已启用" if enabled else "已停用"
    return success_response(
        data=_serialize_user_with_role(session, user),
        message=message,
    )


def disable_user(session: Session, user_id: int) -> ApiResponse:
    """停用用户（逻辑删除，需求 2.7 / 2.8）。便捷封装 set_user_status。"""
    return set_user_status(session, user_id, enabled=False)


def enable_user(session: Session, user_id: int) -> ApiResponse:
    """启用用户。便捷封装 set_user_status。"""
    return set_user_status(session, user_id, enabled=True)


# ----------------------------------------------------------------------
# 用户列表（后端分页，需求 2.1 配套）
# ----------------------------------------------------------------------
def list_users(
    session: Session,
    page: Any = 1,
    page_size: Any = 20,
    status: Optional[int] = None,
    role_id: Optional[int] = None,
) -> ApiResponse:
    """分页查询用户列表（需求 2.1 配套，后端分页）。

    默认按创建时间倒序（仓储层自动探测时间字段）。可按状态与角色筛选。返回的
    每条用户信息均经脱敏，绝不含密码哈希。数据范围隔离由任务 4.9 统一处理，此处
    预留 ``status`` / ``role_id`` 过滤参数。

    Args:
        session: 数据库会话。
        page: 页码（从 1 开始，将被规整）。
        page_size: 每页条数（10/20/50/100，将被规整）。
        status: 按状态筛选（1=启用，0=停用）；None 表示不筛选。
        role_id: 按角色筛选；None 表示不筛选。

    Returns:
        统一响应体：data 为分页结构 {list, total, page, page_size}。
    """
    filters: Dict[str, Any] = {}
    if status is not None:
        filters["status"] = status
    if role_id is not None:
        filters["role_id"] = role_id

    page_result = Repository(SysUser, session).paginate(
        page=page,
        page_size=page_size,
        filters=filters or None,
    )
    # 将分页中的用户模型逐条脱敏序列化，替换 items。
    serialized: List[Dict[str, Any]] = [
        _serialize_user_with_role(session, user) for user in page_result.items
    ]
    data = {
        "list": serialized,
        "total": page_result.total,
        "page": page_result.page,
        "page_size": page_result.page_size,
    }
    return success_response(data=data, message="查询成功")


# ----------------------------------------------------------------------
# 角色列表 / 查询（基础角色管理）
# ----------------------------------------------------------------------
def list_roles(
    session: Session,
    page: Any = 1,
    page_size: Any = 20,
    status: Optional[int] = None,
) -> ApiResponse:
    """分页查询角色列表（基础角色管理，后端分页）。

    Args:
        session: 数据库会话。
        page: 页码（将被规整）。
        page_size: 每页条数（将被规整）。
        status: 按状态筛选；None 表示不筛选。

    Returns:
        统一响应体：data 为分页结构 {list, total, page, page_size}。
    """
    filters: Dict[str, Any] = {}
    if status is not None:
        filters["status"] = status

    page_result = Repository(SysRole, session).paginate(
        page=page,
        page_size=page_size,
        filters=filters or None,
    )
    serialized: List[Dict[str, Any]] = [
        serialize_role(role) for role in page_result.items
    ]
    data = {
        "list": serialized,
        "total": page_result.total,
        "page": page_result.page,
        "page_size": page_result.page_size,
    }
    return success_response(data=data, message="查询成功")


def get_role(session: Session, role_id: int) -> ApiResponse:
    """查询单个角色（基础角色管理）。

    Args:
        session: 数据库会话。
        role_id: 角色 ID。

    Returns:
        统一响应体：成功返回角色信息；不存在返回失败。
    """
    role = Repository(SysRole, session).get(role_id)
    if role is None:
        return error_response(CODE_NOT_FOUND, "目标角色不存在")
    return success_response(data=serialize_role(role), message="查询成功")


__all__ = [
    "USER_STATUS_ENABLED",
    "USER_STATUS_DISABLED",
    "serialize_role",
    "create_user",
    "update_user_role",
    "set_user_status",
    "disable_user",
    "enable_user",
    "list_users",
    "list_roles",
    "get_role",
]

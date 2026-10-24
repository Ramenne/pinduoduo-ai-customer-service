# -*- coding: utf-8 -*-
"""
backend.app.services.profile_service —— 个人设置业务服务
========================================================
本文件用途：实现 backend 服务的「个人设置」业务逻辑，供 profile 路由复用，
满足需求 22（个人设置）：

- ``get_profile(session, user)``：返回当前用户账户信息（用户名、角色等），
  用户名与角色为只读展示（需求 22.1）。
- ``change_password(session, user, current_password, new_password)``：修改密码，
  先校验当前密码正确性（需求 22.2）；当前密码错误时返回 success=false、
  message「当前密码错误」（需求 22.3）；校验通过后以哈希方式更新密码
  （需求 22.5，令牌失效在路由层结合当前令牌完成）。
- ``update_contact(session, user, wechat, qq)``：以用户维度持久化联系方式
  （微信、QQ），仅作用于当前用户记录，天然按用户维度隔离（需求 22.6/22.7）。

实现约束（开发规范）：
- 统一响应体由 common.schemas.common 构造，HTTP 恒 200（规范 1-3）。
- 所有数据访问经 common.db.repository 的参数化查询，禁止拼接 SQL（规范 16）。
- 对外返回的用户信息经 auth_service.serialize_user 脱敏，绝不返回密码明文/哈希
  （需求 1.6 / 22.x）。
- 密码哈希校验与更新复用 common.utils.security（规范 36/52）。
- 个人设置以用户维度隔离：所有读写均限定为「当前登录用户」自身记录，不接受
  外部传入的目标用户 ID，确保不同用户的个人设置互不可见（需求 22.7）。
- 导入置顶（规范 51）；中文注释（规范 37）；单文件 ≤500 行（规范 35）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.core.business_codes import CODE_PARAM_ERROR
from app.services.auth_service import serialize_user
from common.db.repository import Repository
from common.models.user_models import SysRole, SysUser
from common.schemas.common import ApiResponse, error_response, success_response
from common.utils.security import hash_password, verify_password

# 修改密码：当前密码错误的固定中文提示（需求 22.3）。
MSG_CURRENT_PASSWORD_WRONG: str = "当前密码错误"
# 新密码最小长度（与前端校验一致：新密码长度小于 6 位阻止，需求 22.4 配套兜底）。
_NEW_PASSWORD_MIN_LENGTH: int = 6


def _load_role(session: Session, user: SysUser) -> Optional[SysRole]:
    """加载用户所属角色（可能为空）。

    Args:
        session: 数据库会话。
        user: 用户模型实例。

    Returns:
        角色模型实例；用户未分配角色时返回 None。
    """
    if user.role_id is None:
        return None
    return Repository(SysRole, session).get(user.role_id)


def get_profile(session: Session, user: SysUser) -> ApiResponse:
    """返回当前用户的账户信息（需求 22.1）。

    返回脱敏后的用户信息（用户名、角色名、状态、联系方式等），其中用户名与
    角色由前端只读展示。仅读取当前登录用户自身记录，天然按用户维度隔离
    （需求 22.7）。

    Args:
        session: 数据库会话。
        user: 当前登录用户模型。

    Returns:
        统一响应体：data 为脱敏后的当前用户信息。
    """
    role = _load_role(session, user)
    return success_response(data=serialize_user(user, role), message="查询成功")


def change_password(
    session: Session,
    user: SysUser,
    current_password: str,
    new_password: str,
) -> ApiResponse:
    """修改当前用户密码（需求 22.2/22.3/22.5）。

    流程：
    1. 校验入参非空、新密码长度（兜底，前端亦会校验，需求 22.4）；
    2. 校验当前密码正确性（需求 22.2），不通过返回 success=false、
       message「当前密码错误」（需求 22.3）；
    3. 以哈希方式更新密码（需求 22.5），令牌失效由路由层结合当前令牌完成。

    Args:
        session: 数据库会话。
        user: 当前登录用户模型。
        current_password: 用户填写的当前密码（明文）。
        new_password: 用户填写的新密码（明文）。

    Returns:
        统一响应体：成功 success=true；当前密码错误返回固定中文提示。
    """
    # 入参兜底：当前密码与新密码均不可为空。
    if not current_password or not new_password:
        return error_response(CODE_PARAM_ERROR, "当前密码与新密码均不能为空")

    # 新密码长度兜底校验（前端已校验 <6 位阻止，需求 22.4）。
    if len(new_password) < _NEW_PASSWORD_MIN_LENGTH:
        return error_response(CODE_PARAM_ERROR, "新密码长度不能少于 6 位")

    # 校验当前密码正确性（需求 22.2）；不通过返回固定中文提示（需求 22.3）。
    if not verify_password(current_password, user.password_hash):
        return error_response(CODE_PARAM_ERROR, MSG_CURRENT_PASSWORD_WRONG)

    # 以哈希方式更新密码（绝不存明文，需求 22.5 / 1.6）。
    Repository(SysUser, session).update(
        user.id, password_hash=hash_password(new_password)
    )

    return success_response(data=None, message="密码修改成功，请重新登录")


def update_contact(
    session: Session,
    user: SysUser,
    wechat: Optional[str] = None,
    qq: Optional[str] = None,
) -> ApiResponse:
    """保存当前用户的个人联系方式（微信、QQ）（需求 22.6/22.7）。

    仅更新当前登录用户自身记录，按用户维度持久化与隔离——不接受外部目标用户
    ID，确保不同用户的个人设置互不可见（需求 22.7）。

    Args:
        session: 数据库会话。
        user: 当前登录用户模型。
        wechat: 微信号（None 表示不修改该字段，空字符串表示清空）。
        qq: QQ 号（None 表示不修改该字段，空字符串表示清空）。

    Returns:
        统一响应体：data 为更新后脱敏的当前用户信息。
    """
    # 仅更新本次提交的字段：None 表示保持原值不变。
    fields: Dict[str, Any] = {}
    if wechat is not None:
        fields["wechat"] = wechat
    if qq is not None:
        fields["qq"] = qq

    user_repo = Repository(SysUser, session)
    if fields:
        user_repo.update(user.id, **fields)

    # 重新读取以返回最新数据（限定当前用户自身，按用户维度隔离）。
    updated = user_repo.get(user.id)
    role = _load_role(session, updated) if updated is not None else None
    return success_response(
        data=serialize_user(updated, role) if updated is not None else None,
        message="保存成功",
    )


__all__ = [
    "MSG_CURRENT_PASSWORD_WRONG",
    "get_profile",
    "change_password",
    "update_contact",
]

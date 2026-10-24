# -*- coding: utf-8 -*-
"""
common.services.admin_seed —— 初始管理员账号自检种子
====================================================
本文件用途：在服务启动自检时，**幂等**地补齐一个「超级管理员」账号，解决全新
数据库（无任何用户）无法登录的问题（需求 1 / 2，规范 14：启动自检补齐初始数据）。

补齐内容（仅当库中尚无任何用户时执行，避免影响历史数据）：
1. 全量权限点：对系统全部受保护资源（RESOURCE_KEYS）× 标准动作（ACTIONS）
   生成 sys_permission 记录（幂等：存在则跳过）；
2. 管理员角色：sys_role 中 is_admin=True 的「超级管理员」角色（幂等）；
3. 角色-权限映射：将全部权限授予管理员角色（幂等去重）；
4. 管理员用户：按配置的用户名 / 密码创建 sys_user（密码经哈希存储，绝不存明文，
   需求 1.6），绑定管理员角色。

关键约束（开发规范）：
- 规范 11 / 需求 24.5：只增不改不删 —— 已存在的角色 / 权限 / 用户一律保持不变；
  仅当「库中完全没有用户」时才创建初始管理员，已有用户的库不做任何变更。
- 规范 16：经通用仓储层参数化查询，不拼接 SQL。
- 规范 21：账号 / 密码来自 common.core.config（环境变量），不写死生产凭据。
- 幂等：连续运行两次，第二次不产生额外变更。

资源键与动作说明：
- RESOURCE_KEYS 与 backend 各路由模块定义的 ``RESOURCE_*`` 常量一致（受保护资源）；
  集中在此维护一份，避免 common 反向依赖 backend。新增受保护资源时同步在此登记。
- ACTIONS 为全部业务动作的并集（view/create/update/disable/send）；管理员获得全部
  动作的授权，保证其拥有完整操作权限。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from common.core.config import get_settings
from common.db.init_database import register_dict_initializer
from common.db.repository import Repository
from common.models.user_models import (
    SysPermission,
    SysRole,
    SysRolePermission,
    SysUser,
)
from common.utils.security import hash_password

logger = logging.getLogger(__name__)

# 系统全部受保护资源键（与 backend 各路由的 RESOURCE_* 常量保持一致）。
# 新增受保护资源时，须在此同步登记，使管理员自动获得其全部操作权限。
RESOURCE_KEYS: tuple[str, ...] = (
    "user",
    "role",
    "shop",
    "keyword",
    "reply",
    "business_hours",
    "message_filter",
    "blacklist",
    "product_knowledge",
    "cs_knowledge",
    "risk_control",
    "product",
    "chat",
    "dashboard",
    "message_log",
    "risk_log",
    "system_log",
    "notify",
    # 通用 / 内容类菜单资源（个人设置、使用教程、意见反馈、免责声明、关于）：
    # 这些菜单原先无资源、对所有登录用户开放，现统一纳入权限控制，可按角色授权显隐。
    "profile",
    "tutorial",
    "feedback",
    "disclaimer",
    "about",
)

# 全部业务动作并集：管理员对每个资源均授予以下全部动作（含 chat 专用 send）。
ACTIONS: tuple[str, ...] = ("view", "create", "update", "disable", "send")

# 管理员角色名（中文，规范 27）。
ADMIN_ROLE_NAME: str = "超级管理员"

# 注册默认角色名（中文，规范 27）：用户自助注册时自动分配此角色。
DEFAULT_ROLE_NAME: str = "普通用户"

# 注册默认角色的基线权限（resource_key, action）集合。
# 设计原则：普通用户可登录并管理「自己的」店铺与自动回复相关配置，但不含用户 /
# 角色管理、系统设置、通知渠道等管理端能力；数据范围隔离另由 data_scope
# 保证其仅见本人数据。管理员可在「角色权限」页按需为该角色增删权限。
# 说明（店铺级设置统一判权）：默认回复 / 商品回复 / AI 设置 / 营业时间 / 消息过滤 /
# 黑名单 / 风控 / 转人工等「按店铺维度」的设置，入口已统一收敛到店铺管理页，
# 后端均改用 shop 资源判权，故此处仅需授予 shop 权限即可操作全部店铺级设置，
# 不再单独授予 reply / business_hours / message_filter / blacklist 等资源。
_DEFAULT_ROLE_PERMISSIONS: tuple[tuple[str, str], ...] = (
    ("dashboard", "view"),
    ("shop", "view"), ("shop", "create"), ("shop", "update"), ("shop", "disable"),
    ("keyword", "view"), ("keyword", "create"), ("keyword", "update"), ("keyword", "disable"),
    ("product_knowledge", "view"), ("product_knowledge", "create"), ("product_knowledge", "update"), ("product_knowledge", "disable"),
    ("cs_knowledge", "view"), ("cs_knowledge", "create"), ("cs_knowledge", "update"), ("cs_knowledge", "disable"),
    ("product", "view"), ("product", "update"),
    ("chat", "view"), ("chat", "send"),
    ("message_log", "view"),
    # 通用 / 内容类菜单：普通用户默认可见（仅 view），与历史「对所有登录用户开放」
    # 的行为保持一致，避免纳入权限控制后存量普通用户丢失个人设置 / 教程 / 关于等入口。
    ("profile", "view"),
    ("tutorial", "view"),
    ("feedback", "view"),
    ("disclaimer", "view"),
    ("about", "view"),
)

# 启用状态常量（与 SysUser/SysRole.status 约定一致：1=启用）。
_STATUS_ENABLED: int = 1


def _seed_permissions(session: Session) -> dict[tuple[str, str], int]:
    """幂等补齐全部 (resource_key, action) 权限点，返回「权限对 -> 权限 id」映射。

    已存在的权限点直接复用其 id，不重复插入（规范 14：只增不改）。

    Args:
        session: 当前事务性会话。

    Returns:
        ``{(resource_key, action): permission_id}`` 映射，覆盖全部资源×动作。
    """
    repo: Repository[SysPermission] = Repository(SysPermission, session)
    pair_to_id: dict[tuple[str, str], int] = {}
    for resource_key in RESOURCE_KEYS:
        for action in ACTIONS:
            existing = repo.get_by(resource_key=resource_key, action=action)
            if existing is None:
                existing = repo.create(
                    resource_key=resource_key,
                    action=action,
                    description=f"{resource_key}:{action}",
                )
            pair_to_id[(resource_key, action)] = existing.id
    return pair_to_id


def _seed_admin_role(session: Session) -> int:
    """幂等补齐管理员角色（is_admin=True），返回角色 id。

    已存在同名管理员角色则复用，不重复创建（规范 14）。

    Args:
        session: 当前事务性会话。

    Returns:
        管理员角色 id。
    """
    repo: Repository[SysRole] = Repository(SysRole, session)
    role = repo.get_by(role_name=ADMIN_ROLE_NAME)
    if role is None:
        role = repo.create(
            role_name=ADMIN_ROLE_NAME, is_admin=True, status=_STATUS_ENABLED
        )
    return role.id


def _seed_default_role(session: Session) -> int:
    """幂等补齐注册默认角色（is_default=True，非管理员），返回角色 id。

    已存在同名角色则复用并确保其 is_default 标记为真；不存在则创建。该角色用于
    用户自助注册时自动分配，绝不设为管理员（规范 41：注册用户不应获得管理员权限）。

    Args:
        session: 当前事务性会话。

    Returns:
        默认角色 id。
    """
    repo: Repository[SysRole] = Repository(SysRole, session)
    role = repo.get_by(role_name=DEFAULT_ROLE_NAME)
    if role is None:
        role = repo.create(
            role_name=DEFAULT_ROLE_NAME,
            is_admin=False,
            is_default=True,
            status=_STATUS_ENABLED,
        )
    elif not role.is_default:
        # 已存在但未标记默认：补齐默认标记（幂等，不改其它字段）。
        repo.update(role.id, is_default=True)
    return role.id


def _grant_all_permissions(
    session: Session, role_id: int, pair_to_id: dict[tuple[str, str], int]
) -> int:
    """将全部权限幂等授予指定角色，返回本次新增的映射条数。

    已存在的 (role_id, permission_id) 映射不重复插入（规范 14，幂等去重）；
    若映射存在但被软删除（enabled=False），则恢复为有效。

    Args:
        session: 当前事务性会话。
        role_id: 目标角色 id（管理员角色）。
        pair_to_id: 权限对到权限 id 的映射。

    Returns:
        本次新增的角色-权限映射条数。
    """
    return _grant_permissions(session, role_id, list(pair_to_id.values()))


def _grant_permissions(
    session: Session, role_id: int, permission_ids: list[int]
) -> int:
    """将指定权限 id 列表幂等授予角色，返回本次新增映射条数。

    幂等规则：映射不存在则插入（enabled=True）；**映射已存在则一律不改动**
    （无论其当前是否有效）。

    说明（规范 14：启动自检不得影响历史数据）：早期实现会把「存在但被软删除
    （enabled=False）」的映射在启动时恢复为有效，这会撤销管理员对默认角色基线
    权限的停用操作（``ensure_default_role`` 每次启动都执行）。此处改为「仅插入
    缺失映射」，不再复活已被管理员停用的授权，保护历史数据。

    Args:
        session: 当前事务性会话。
        role_id: 目标角色 id。
        permission_ids: 待授予的权限 id 列表。

    Returns:
        本次新增的角色-权限映射条数。
    """
    repo: Repository[SysRolePermission] = Repository(SysRolePermission, session)
    granted = 0
    for permission_id in permission_ids:
        existing = repo.get_by(role_id=role_id, permission_id=permission_id)
        if existing is None:
            repo.create(role_id=role_id, permission_id=permission_id, enabled=True)
            granted += 1
        # 已存在（无论有效与否）：不改动，保护管理员的历史停用操作（规范 14）。
    return granted


def register_initial_admin(session: Session) -> int:
    """启动自检：在「库中尚无任何用户」时幂等创建初始超级管理员，返回新增用户数。

    步骤：
    1. 若 sys_user 已存在任何用户，直接返回 0（不影响历史数据，规范 11/14）；
    2. 否则补齐全部权限点、管理员角色与角色-权限映射，并创建管理员用户
       （密码经哈希存储，账号 / 密码来自配置，规范 21）。

    Args:
        session: 当前事务性会话（事务提交由外层 session_scope 负责）。

    Returns:
        本次新增的用户数（创建管理员返回 1，已有用户返回 0）。
    """
    user_repo: Repository[SysUser] = Repository(SysUser, session)
    # 仅在「完全没有用户」时初始化，避免对已有数据做任何变更（规范 11/14）。
    if user_repo.count() > 0:
        return 0

    settings = get_settings()
    username = settings.default_admin_username
    password = settings.default_admin_password

    # 1) 补齐全部权限点；2) 管理员角色；3) 全量授权。
    pair_to_id = _seed_permissions(session)
    role_id = _seed_admin_role(session)
    _grant_all_permissions(session, role_id, pair_to_id)

    # 4) 创建管理员用户（密码哈希存储，绝不存明文 —— 需求 1.6）。
    user_repo.create(
        username=username,
        password_hash=hash_password(password),
        role_id=role_id,
        status=_STATUS_ENABLED,
    )
    logger.info("启动自检：已创建初始超级管理员账号「%s」（请尽快修改默认密码）", username)
    return 1


def ensure_default_role(session: Session) -> int:
    """启动自检：幂等补齐「注册默认角色」及其基线权限（始终执行）。

    与 ``register_initial_admin`` 不同，本钩子对任何库（含已有用户的库）都会执行，
    确保「用户自助注册」始终有一个可分配的默认角色（普通用户）。步骤均幂等：
    1. 补齐全部权限点（已存在跳过）；
    2. 补齐默认角色（已存在复用并确保 is_default 标记）；
    3. 将基线权限授予默认角色（已存在/已恢复跳过，规范 14）。

    Args:
        session: 当前事务性会话（提交由外层 session_scope 负责）。

    Returns:
        本次新增的记录数（权限点 + 角色 + 映射的增量之和）。
    """
    pair_to_id = _seed_permissions(session)
    role_id = _seed_default_role(session)
    # 解析基线权限对到权限 id（权限点已在上一步补齐，必然命中）。
    baseline_ids = [
        pair_to_id[pair] for pair in _DEFAULT_ROLE_PERMISSIONS if pair in pair_to_id
    ]
    _grant_permissions(session, role_id, baseline_ids)
    # 增量条数对幂等无实质意义，统一返回 0（已存在则不增长，规范 14）。
    return 0


# 模块导入时注册为启动自检钩子：迁移器在建表 / 补字段 / 补字典后回调本钩子，
# 幂等补齐初始管理员。register_dict_initializer 自身去重，重复导入不会重复注册。
register_dict_initializer(register_initial_admin)
# 始终幂等补齐注册默认角色（普通用户）及其基线权限，供用户自助注册分配。
register_dict_initializer(ensure_default_role)


__all__ = [
    "RESOURCE_KEYS",
    "ACTIONS",
    "ADMIN_ROLE_NAME",
    "DEFAULT_ROLE_NAME",
    "register_initial_admin",
    "ensure_default_role",
]

# -*- coding: utf-8 -*-
"""
common.models.user_models —— 用户与权限相关数据表模型
====================================================
本文件用途：定义「拼多多自动回复」系统用户与权限业务域的数据表结构模型，
覆盖：
- sys_user            系统用户（用户名唯一、密码哈希、角色、启停用、个人联系方式）
- sys_role            角色（角色名、是否管理员、启停用）
- sys_permission      权限（资源键、操作、描述）
- sys_role_permission 角色-权限映射（多对多桥接表）
- sys_menu            菜单（菜单键、标题、父级、仅管理员可见、排序）

关键约束（开发规范）：
- 规范 9：每张表均有自增 BIGINT 主键（来自 AuditMixin / IdMixin）。
- 规范 10：表间关系（role_id / permission_id 等）一律为普通列，不设外键约束。
- 规范 17：审计时间字段统一北京时间。
- 敏感字段 ``password_hash`` 仅存哈希，绝不存明文，对外序列化时须脱敏。

逻辑约束（代码层维护，不用外键 / 不用唯一索引强制，见注释）：
- sys_user.username 全局唯一（建唯一索引，登录账号）。
- sys_role_permission 同 (role_id, permission_id) 组合应唯一（代码层校验）。
"""
from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from common.models.base import AuditMixin, Base


class SysUser(AuditMixin, Base):
    """系统用户表 sys_user。

    存储登录账号、密码哈希、所属角色与启停用状态，以及个人联系方式
    （微信 / QQ，归个人设置维度）。停用（status=0）即逻辑删除，禁止物理删除。
    """

    __tablename__ = "pdd_sys_user"
    __table_args__ = {"comment": "系统用户表（登录账号 / 密码哈希 / 角色 / 联系方式）"}

    # 登录用户名：全局唯一（唯一索引），用于登录与展示
    username: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, comment="登录用户名（全局唯一）"
    )
    # 密码哈希：仅存哈希值，禁止存明文，对外响应须脱敏（需求 1.6 / 22.2）
    password_hash: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="密码哈希（仅存哈希，绝不存明文）"
    )
    # 角色 id：普通列，关系由代码维护（规范 10，不设外键）
    role_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="所属角色 ID（普通列，无外键）"
    )
    # 启停用状态：1=启用，0=停用（停用即逻辑删除，需求 2.8）
    status: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, comment="状态：1=启用，0=停用（逻辑删除）"
    )
    # 注册邮箱：用于邮箱验证码注册（登录账号仍以 username 为准）。
    # 设为可空普通列、不建库级唯一约束（历史用户无邮箱为 NULL，避免迁移补唯一索引
    # 时因多 NULL 被判为重复而跳过）；邮箱全局唯一由业务层 register 校验（规范 10）。
    email: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="注册邮箱（邮箱验证码注册用，全局唯一由代码校验）"
    )
    # 个人联系方式：微信号
    wechat: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="微信号（个人联系方式）"
    )
    # 个人联系方式：QQ 号
    qq: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="QQ 号（个人联系方式）"
    )


class SysRole(AuditMixin, Base):
    """角色表 sys_role。

    定义角色名称、是否为管理员角色（管理员专属菜单对其强制可见）与启停用状态。
    """

    __tablename__ = "pdd_sys_role"
    __table_args__ = {"comment": "角色表（角色名 / 是否管理员 / 启停用）"}

    role_name: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="角色名称"
    )
    # 是否管理员角色：管理员对「仅管理员可见」菜单强制可见（需求 2.9）
    is_admin: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="是否管理员角色"
    )
    # 是否注册默认角色：用户自助注册时自动分配该角色（同一时刻仅一个角色为默认，
    # 由业务层保证唯一）。管理员角色不应设为默认，避免注册用户获得管理员权限。
    # server_default="0"：为含历史数据的表新增本列时，历史行回填为 0（非默认），
    # 避免迁移器将「非空无默认」列降级为可空导致历史行为 NULL（需求 24.5）。
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False, comment="是否注册默认角色"
    )
    status: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, comment="状态：1=启用，0=停用"
    )


class SysPermission(AuditMixin, Base):
    """权限表 sys_permission。

    以 (resource_key, action) 描述一个可授权的操作点，供统一权限模块 check 使用。
    """

    __tablename__ = "pdd_sys_permission"
    __table_args__ = {"comment": "权限表（受保护资源键 + 操作）"}

    # 资源键：标识受保护资源（如 shop / keyword_rule 等）
    resource_key: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="资源键（受保护资源标识）"
    )
    # 操作：如 view / create / update / disable 等
    action: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="操作（view/create/update/disable 等）"
    )
    description: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="权限描述"
    )


class SysRolePermission(AuditMixin, Base):
    """角色-权限映射表 sys_role_permission（多对多桥接）。

    记录角色拥有的权限。role_id / permission_id 均为普通列（规范 10，无外键），
    同 (role_id, permission_id) 组合逻辑唯一，由代码层去重校验。
    """

    __tablename__ = "pdd_sys_role_permission"
    __table_args__ = (
        # 逻辑唯一：同一角色-权限映射唯一（upsert / 授权幂等的库层保障）。
        # 启动自检迁移器会在无重复数据时自动补建该唯一索引（有重复则跳过告警，不删数据）。
        UniqueConstraint(
            "role_id", "permission_id", name="uix_pdd_sys_role_perm_role_perm"
        ),
        {"comment": "角色-权限映射表（多对多桥接）"},
    )

    role_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="角色 ID（普通列，无外键）"
    )
    permission_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="权限 ID（普通列，无外键）"
    )
    # 是否有效：取消授权时置为 False（软删除，禁止物理删除映射 —— 规范 11）。
    # 权限加载与判定仅认 enabled=True 的映射；历史 / 重新授权时复用同一行翻转此值。
    # server_default="1"：为含历史数据的表新增本列时，历史映射行回填为 1（有效），
    # 保证升级后既有角色（含管理员）的历史授权不失效——若降级为可空则历史行会是
    # NULL，被 enabled=True 过滤掉，导致权限集体丢失（需求 24.5 历史数据安全）。
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False, comment="映射是否有效（软删除标记）"
    )


class SysMenu(AuditMixin, Base):
    """菜单表 sys_menu。

    定义系统菜单项，支持父子层级（parent_key）、仅管理员可见（admin_only）
    与排序（order_no）。菜单键枚举登记入 sys_dict（需求 24.7）。
    """

    __tablename__ = "pdd_sys_menu"
    __table_args__ = {"comment": "菜单表（菜单键 / 标题 / 层级 / 仅管理员可见 / 排序）"}

    # 菜单键：唯一标识一个菜单项，用于路由与权限映射
    menu_key: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="菜单键（唯一标识，用于路由/权限）"
    )
    title: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="菜单标题（中文）"
    )
    # 父级菜单键：顶级菜单为空；普通列维护层级关系（无外键）
    parent_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="父级菜单键（顶级为空，无外键）"
    )
    # 仅管理员可见：管理员对该类菜单强制可见（需求 2.9 / 21.10）
    admin_only: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="是否仅管理员可见"
    )
    order_no: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="菜单排序序号（升序）"
    )


__all__ = [
    "SysUser",
    "SysRole",
    "SysPermission",
    "SysRolePermission",
    "SysMenu",
]

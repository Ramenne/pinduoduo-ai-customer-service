# -*- coding: utf-8 -*-
"""
backend.tests.test_role_permission_migration —— 角色权限新列迁移历史数据安全测试
==============================================================================
本文件用途：回归验证「为已含历史数据的角色-权限相关表新增列」时，启动自检迁移器
（SchemaMigrator）能安全补列且不破坏历史授权（需求 24.5 / 规范 11/14）。

背景（关键 bug 防回归）：``SysRolePermission.enabled`` 与 ``SysRole.is_default`` 为
本次新增列。迁移器对「非空且无 server_default」的新列会降级为可空添加，历史行将
变为 NULL；而权限加载以 ``enabled=True`` 过滤，NULL 会被排除，导致升级后既有角色
（含管理员）权限集体失效。为此两列均声明 server_default，使历史行在 ALTER 时回填
默认值（enabled→1 有效、is_default→0 非默认）。本测试在「缺列的旧表 + 历史数据」
上运行迁移，断言历史行被正确回填。

测试基础设施：SQLite 内存库（与 conftest 一致），构造缺列的旧版表结构后插入历史
数据，再运行迁移器补列并校验。
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    text,
)
from sqlalchemy.pool import StaticPool

from common.db.init_database import SchemaMigrator, clear_dict_initializers


def _new_engine():
    """构造独立的 SQLite 内存引擎（StaticPool 保证同一连接贯穿测试）。"""
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


def test_role_permission_enabled_backfilled_on_migration():
    """旧 pdd_sys_role_permission 表缺 enabled 列：补列后历史映射应回填为有效（1）。"""
    engine = _new_engine()
    # 构造「旧版」表：仅含 id/role_id/permission_id，缺 enabled / 审计列。
    meta = MetaData()
    Table(
        "pdd_sys_role_permission",
        meta,
        Column("id", BigInteger, primary_key=True),
        Column("role_id", BigInteger),
        Column("permission_id", BigInteger),
    )
    meta.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO pdd_sys_role_permission (id, role_id, permission_id) VALUES (1, 10, 20)")
        )

    # 运行迁移（清空字典钩子，专注结构补列）。
    clear_dict_initializers()
    SchemaMigrator(engine=engine).run()

    with engine.connect() as conn:
        rows = list(conn.execute(text("SELECT role_id, permission_id, enabled FROM pdd_sys_role_permission")))
    # 历史映射仍在，且 enabled 被回填为 1（有效），不会因 NULL 被权限过滤丢弃。
    assert rows == [(10, 20, 1)]


def test_role_is_default_backfilled_on_migration():
    """旧 pdd_sys_role 表缺 is_default 列：补列后历史角色应回填为非默认（0）。"""
    engine = _new_engine()
    meta = MetaData()
    Table(
        "pdd_sys_role",
        meta,
        Column("id", BigInteger, primary_key=True),
        Column("role_name", String(64)),
        Column("is_admin", Integer),
        Column("status", Integer),
    )
    meta.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO pdd_sys_role (id, role_name, is_admin, status) VALUES (1, '超级管理员', 1, 1)")
        )

    clear_dict_initializers()
    SchemaMigrator(engine=engine).run()

    with engine.connect() as conn:
        rows = list(conn.execute(text("SELECT role_name, is_default FROM pdd_sys_role")))
    # 历史角色保留，is_default 回填为 0（非默认），不影响管理员判定。
    assert rows == [("超级管理员", 0)]

# -*- coding: utf-8 -*-
"""
common.db 数据库子包
====================
本文件为 common 公共库 db 子包的初始化文件。

db 子包集中管理数据库相关能力，统一所有 SQL（禁止在各业务文件随处拼接 SQL），
后续任务中提供：
- session：MySQL 连接池与会话管理
- retry：连接失败重试
- init_database：启动自检迁移器（缺表建表、缺字段补字段、缺字典补数据）
- 通用 CRUD / 分页 / 逻辑删除 / 按业务键 upsert（参数化查询，防 SQL 注入）

约束：所有 SQL 一律使用参数化查询，禁止字符串拼接；禁止物理删除业务数据。
具体实现在后续任务（2.6 / 2.8 / 2.12）中完成。
"""
from common.db.init_database import (
    MigrationResult,
    SchemaMigrator,
    clear_dict_initializers,
    init_database,
    register_dict_initializer,
    run_migration,
)
from common.db.repository import (
    Repository,
    run_in_session,
    run_with_retry,
)
from common.db.retry import (
    is_db_disconnect_error,
    retry_call,
    with_db_retry,
)
from common.db.session import (
    get_db,
    get_engine,
    get_session_factory,
    reset_engine,
    session_scope,
)

__all__ = [
    # 会话与引擎
    "get_engine",
    "get_session_factory",
    "session_scope",
    "get_db",
    "reset_engine",
    # 连接失败重试
    "is_db_disconnect_error",
    "with_db_retry",
    "retry_call",
    # 通用仓储层
    "Repository",
    "run_in_session",
    "run_with_retry",
    # 启动自检迁移器
    "SchemaMigrator",
    "MigrationResult",
    "run_migration",
    "init_database",
    "register_dict_initializer",
    "clear_dict_initializers",
]

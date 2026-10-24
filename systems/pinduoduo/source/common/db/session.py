# -*- coding: utf-8 -*-
"""
common.db.session —— MySQL 连接池与会话管理
============================================
本文件用途：为「拼多多自动回复」系统的各服务（backend / websocket /
scheduler）提供统一的数据库连接基础设施，基于 SQLAlchemy 2.0（同步）实现：
- 创建并复用一个进程内单例引擎（Engine），内置连接池配置；
- 提供会话工厂（sessionmaker）；
- 提供「上下文管理器」式获取会话（自动 commit/rollback/close）；
- 提供 FastAPI 依赖（dependency）式获取会话，供路由注入使用。

关键约束（开发规范）：
- 连接信息一律来自 common.core.config.get_settings()，禁止写死 localhost
  （规范 21）；驱动使用 mysql+pymysql，配合参数化查询防 SQL 注入（规范 16）。
- 数据库操作需考虑连接失败重试（规范 13），重试装饰器见 common.db.retry。
- 本模块只提供「连接池 / 会话 / 工厂」基础设施；建表迁移自检由 backend
  的 lifespan 调用 SchemaMigrator 执行（任务 4.1），本模块不负责建表。

设计说明：
- 引擎采用「惰性单例」：首次调用 get_engine() 时才依据当前配置构造，
  create_engine 本身不会立即建立 TCP 连接（连接在首次使用时才建立），
  因此仅导入本模块、构造引擎与会话工厂都不需要真实可用的 MySQL。
- 提供 reset_engine() 释放并清空单例（主要供测试在切换配置后重建引擎）。
"""
from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from common.core.config import get_settings

# ----------------------------------------------------------------------
# 连接池默认参数（可按部署规模调整）
# - POOL_SIZE：连接池常驻连接数
# - MAX_OVERFLOW：高峰期允许的额外溢出连接数
# - POOL_TIMEOUT：从连接池获取连接的最长等待秒数
# - POOL_RECYCLE：连接回收时间（秒），小于 MySQL 的 wait_timeout，
#   防止使用到被服务端断开的「僵尸连接」
# - POOL_PRE_PING：取连接前先 ping 一次，自动剔除失效连接
# ----------------------------------------------------------------------
POOL_SIZE: int = 20
MAX_OVERFLOW: int = 40
POOL_TIMEOUT: int = 30
POOL_RECYCLE: int = 1800
POOL_PRE_PING: bool = True

# 进程内引擎与会话工厂的单例缓存（惰性初始化）
_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _create_engine() -> Engine:
    """依据当前配置构造 SQLAlchemy 引擎并完成连接池设置。

    连接 URL 来自 get_settings().database_url（mysql+pymysql，密码已做 URL
    转义），不在此处写死任何地址或凭据（规范 21）。
    """
    settings = get_settings()
    return create_engine(
        settings.database_url,
        echo=False,            # 关闭 SQL 回显，避免噪声日志（规范 38：禁用 debug 级日志）
        future=True,           # 启用 SQLAlchemy 2.0 风格 API
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_timeout=POOL_TIMEOUT,
        pool_recycle=POOL_RECYCLE,
        pool_pre_ping=POOL_PRE_PING,
    )


def get_engine() -> Engine:
    """返回进程内单例引擎，首次调用时惰性构造。

    create_engine 不会立即建立数据库连接，故本函数可在无真实 MySQL 时安全调用。
    """
    global _engine
    if _engine is None:
        _engine = _create_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """返回进程内单例会话工厂（sessionmaker），首次调用时惰性构造。

    - expire_on_commit=False：提交后不过期对象属性，避免提交后访问属性触发额外查询；
    - autoflush=False：由业务显式控制 flush/commit 时机，行为更可预期。
    """
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
            future=True,
        )
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """以上下文管理器方式提供事务性会话。

    用法::

        with session_scope() as session:
            session.execute(text("INSERT ... VALUES (:v)"), {"v": value})

    行为：
    - 正常退出：自动 commit；
    - 抛出异常：自动 rollback 并向上抛出，保证事务一致性；
    - 无论成败：最终 close 释放连接回连接池。
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        # 出现任何异常一律回滚，避免半提交污染数据
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：在请求生命周期内提供数据库会话。

    用法（在路由中）::

        @router.get("/items")
        def list_items(db: Session = Depends(get_db)):
            ...

    事务语义（与 session_scope 一致）：
    - 请求正常结束：自动 commit，保证业务写操作落库；
    - 请求处理抛出异常：自动 rollback，避免半提交脏数据；
    - 无论成败：最终 close 释放连接回连接池。

    说明：仓储层（common.db.repository）仅 flush 不 commit，事务提交统一收口于
    本依赖；故各业务 service 无需再各自显式 commit（规范 36，事务管理集中一处），
    既避免「写操作忘记 commit 导致不落库」，也防止只读接口的无谓提交开销
    （SQLAlchemy 对无变更的事务 commit 为低成本操作）。
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        # 请求正常结束：提交事务，使经仓储层 flush 的写操作真正落库。
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """释放并清空进程内单例引擎与会话工厂（主要供测试在切换配置后重建）。

    会调用 engine.dispose() 关闭连接池中的全部连接；下次调用 get_engine()
    将依据最新配置重新构造。
    """
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


__all__ = [
    "get_engine",
    "get_session_factory",
    "session_scope",
    "get_db",
    "reset_engine",
]

# -*- coding: utf-8 -*-
"""
scheduler 测试公共夹具与路径配置
================================
本文件用途：保证 scheduler 测试可正确导入被测模块（``tasks.*``）与公共库
（``common.*``）。被测模块以 ``from tasks.xxx import ...`` 形式导入（tasks 为
scheduler 服务顶级包，需将 scheduler 目录加入 sys.path），其内部又以
``from common.xxx import ...`` 复用公共库（需将仓库根目录加入 sys.path）。

测试数据库（内存 SQLite）：
- 运行库为 MySQL，但单元测试不依赖真实 MySQL：用 SQLite 内存库承载相同 ORM 模型。
- 适配：MySQL 主键为 BIGINT 自增，SQLite 仅 INTEGER 列支持 rowid 自增，故通过
  方言编译钩子在 SQLite 下将 BigInteger 渲染为 INTEGER。
"""
import os
import sys

# scheduler 服务目录 = scheduler/tests 的父目录；仓库根目录 = scheduler 的父目录。
_SCHEDULER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_SCHEDULER_DIR)

# 仓库根目录加入 sys.path 以支持 `import common.*`
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
# scheduler 服务目录加入 sys.path 以支持 `import tasks.*`
if _SCHEDULER_DIR not in sys.path:
    sys.path.insert(0, _SCHEDULER_DIR)

# 以下导入依赖上方 sys.path 配置，故置于其后（pytest conftest 惯例例外）。
import pytest  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import common.db.session as db_session_module  # noqa: E402
from common.models.base import Base  # noqa: E402
# 导入全部模型，确保其表登记进 Base.metadata（建表时一并创建）。
import common.models  # noqa: E402,F401


@compiles(BigInteger, "sqlite")
def _compile_bigint_for_sqlite(element, compiler, **kw):  # noqa: ANN001
    """在 SQLite 方言下将 BIGINT 渲染为 INTEGER，以支持自增主键。"""
    return "INTEGER"


@pytest.fixture()
def db_session(monkeypatch):
    """提供基于内存 SQLite 的事务性会话，并重定向公共库会话工厂到测试引擎。

    建表后将 ``common.db.session`` 的引擎 / 会话工厂 / get_* 全部指向测试引擎，
    使被测代码经由公共库（run_with_retry / Repository）访问数据库时落到同一库。
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine, class_=Session, expire_on_commit=False, autoflush=False, future=True
    )

    # 将公共库会话基础设施重定向到测试引擎，覆盖惰性单例。
    monkeypatch.setattr(db_session_module, "_engine", engine, raising=False)
    monkeypatch.setattr(db_session_module, "_session_factory", factory, raising=False)
    monkeypatch.setattr(db_session_module, "get_engine", lambda: engine)
    monkeypatch.setattr(db_session_module, "get_session_factory", lambda: factory)

    session = factory()
    try:
        yield session
        session.commit()
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()

# -*- coding: utf-8 -*-
"""
backend 测试公共夹具与路径配置
==============================
本文件用途：保证 backend 测试可正确导入被测模块与公共库，并提供「内存 SQLite +
统一响应体」的测试基础设施，供认证接口测试（test_auth_api）与用户/权限管理测试
（test_user_service / test_users_api）复用。

路径配置（必须最先生效，因下方 import 依赖之）：
- 被测模块以 `from app.core.permission import ...` 形式（app 为 backend 服务顶级包，
  需将 backend 目录加入 sys.path），其内部又以 `from common.models... import ...`
  复用公共库（需将仓库根目录加入 sys.path）。故本 conftest 同时把「仓库根目录」与
  「backend 服务目录」加入 sys.path，使测试无论从何处启动都能解析 `app` 与 `common`
  顶级包。

测试数据库（内存 SQLite）：
- 项目运行库为 MySQL，但单元测试不依赖真实 MySQL：本 conftest 用 SQLite 内存库
  承载相同 ORM 模型，覆盖业务逻辑。
- 适配：MySQL 主键为 BIGINT 自增，SQLite 仅 `INTEGER PRIMARY KEY` 列支持自增
  （rowid 别名）。这里通过 SQLAlchemy 方言类型编译钩子，在 SQLite 下把
  `BigInteger` 渲染为 `INTEGER`，从而让自增主键在 SQLite 正常工作。

夹具说明：
- `db_session`：基于内存 SQLite 引擎建表，提供事务性会话，并把业务代码的
  `get_db` / `get_session_factory` / `get_engine` 重定向到该测试引擎，使经由
  service / 路由层访问数据库时落到同一内存库。
- `client`：FastAPI TestClient，复用 `db_session` 的引擎，覆盖应用的 `get_db`
  依赖，保证接口与测试断言读写同一数据库。
- `seed_permissions` / `test_users`：预置角色、权限映射与若干用户（管理员、
  普通授权用户、无权限用户、停用用户），供各测试直接取用。
"""
import os
import sys

# backend 服务目录 = backend/tests 的父目录；仓库根目录 = backend 的父目录。
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_BACKEND_DIR)

# 仓库根目录加入 sys.path 以支持 `import common.*`
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
# backend 服务目录加入 sys.path 以支持 `import app.*`
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# 以下导入依赖上方 sys.path 配置，故置于其后（pytest conftest 的惯例例外）。
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

import common.db.session as db_session_module
from common.models.base import Base
# 导入全部模型模块，确保其表登记进 Base.metadata（建表时一并创建）。
import common.models.user_models  # noqa: F401
from common.models.user_models import (
    SysPermission,
    SysRole,
    SysRolePermission,
    SysUser,
)
from common.utils.security import hash_password


# ----------------------------------------------------------------------
# SQLite 方言适配：BigInteger -> INTEGER（让自增主键在 SQLite 生效）
# ----------------------------------------------------------------------
@compiles(BigInteger, "sqlite")
def _compile_bigint_for_sqlite(element, compiler, **kw):  # noqa: ANN001
    """在 SQLite 方言下将 BIGINT 渲染为 INTEGER。

    SQLite 仅对 `INTEGER PRIMARY KEY` 列提供 rowid 自增；模型主键声明为
    BigInteger，若直接渲染为 BIGINT 则自增失效，故此处统一改写为 INTEGER。
    """
    return "INTEGER"


@pytest.fixture()
def db_session(monkeypatch):
    """提供基于内存 SQLite 的事务性会话，并重定向公共库会话工厂到测试引擎。

    建表后将 `common.db.session` 的引擎 / 会话工厂 / get_db 全部指向测试引擎，
    使 service 层与路由层经由公共库访问数据库时，落到同一内存库。
    """
    # 内存库需用 StaticPool + 关闭线程检查，保证同一连接贯穿整个测试。
    from sqlalchemy.pool import StaticPool

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

    # 将公共库的会话基础设施重定向到测试引擎，覆盖惰性单例。
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


@pytest.fixture()
def client(db_session):
    """提供 FastAPI TestClient，覆盖 get_db 依赖以复用测试内存库。

    依赖 `db_session` 先完成引擎重定向与建表；随后用一个共享同一引擎的会话
    覆盖应用的 `get_db` 依赖，保证接口请求与测试断言读写同一数据库。
    """
    from _bootstrap import app
    from common.db.session import get_db, get_session_factory

    def _override_get_db():
        # 每次请求新开会话，但绑定到同一测试引擎（与 db_session 同库）。
        session = get_session_factory()()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    # 直接实例化（不进入上下文管理器），避免触发应用 lifespan 启动迁移自检
    # （迁移面向 MySQL，单元测试使用内存 SQLite，不应执行真实迁移）。
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)


# ----------------------------------------------------------------------
# 数据预置夹具：角色 / 权限映射 / 用户
# ----------------------------------------------------------------------
@pytest.fixture()
def seed_permissions(db_session):
    """预置角色与权限映射，返回各角色 id 与权限 id 字典。

    构造：
    - 管理员角色 admin（is_admin=True）；
    - 授权角色 user_manager：被授予 user 资源的 view/create/update/disable 与
      role 资源的 view 权限（可正常访问 users / roles 接口）；
    - 无权限角色 guest：不授予任何权限（访问受保护接口应被拒）。
    """
    role_admin = SysRole(role_name="管理员", is_admin=True, status=1)
    role_manager = SysRole(role_name="用户管理员", is_admin=False, status=1)
    role_guest = SysRole(role_name="访客", is_admin=False, status=1)
    db_session.add_all([role_admin, role_manager, role_guest])
    db_session.flush()

    # 定义并落库权限点。
    perm_defs = [
        ("user", "view"),
        ("user", "create"),
        ("user", "update"),
        ("user", "disable"),
        ("role", "view"),
    ]
    perm_ids = {}
    for resource_key, action in perm_defs:
        perm = SysPermission(resource_key=resource_key, action=action)
        db_session.add(perm)
        db_session.flush()
        perm_ids[(resource_key, action)] = perm.id

    # 授权角色 user_manager 拥有全部上述权限。
    for pid in perm_ids.values():
        db_session.add(SysRolePermission(role_id=role_manager.id, permission_id=pid))
    db_session.flush()
    db_session.commit()

    return {
        "role_admin_id": role_admin.id,
        "role_manager_id": role_manager.id,
        "role_guest_id": role_guest.id,
        "perm_ids": perm_ids,
    }


@pytest.fixture()
def test_users(db_session, seed_permissions):
    """预置若干测试用户，返回其登录凭据与关键信息。

    包含：
    - admin：管理员账号（管理员角色，启用）；
    - manager：被授权用户（用户管理员角色，启用），可访问 users 接口；
    - guest：无权限用户（访客角色，启用），访问受保护接口应被拒；
    - disabled：停用账号（status=0），用于停用拒绝鉴权 / 登录场景。
    """
    admin_password = "admin-password-123"
    manager_password = "manager-password-123"
    guest_password = "guest-password-123"
    disabled_password = "disabled-password-123"

    admin_hash = hash_password(admin_password)
    admin = SysUser(
        username="admin",
        password_hash=admin_hash,
        role_id=seed_permissions["role_admin_id"],
        status=1,
    )
    manager = SysUser(
        username="manager",
        password_hash=hash_password(manager_password),
        role_id=seed_permissions["role_manager_id"],
        status=1,
    )
    guest = SysUser(
        username="guest",
        password_hash=hash_password(guest_password),
        role_id=seed_permissions["role_guest_id"],
        status=1,
    )
    disabled = SysUser(
        username="disabled_user",
        password_hash=hash_password(disabled_password),
        role_id=seed_permissions["role_guest_id"],
        status=0,
    )
    db_session.add_all([admin, manager, guest, disabled])
    db_session.flush()
    db_session.commit()

    return {
        "admin": {"id": admin.id, "username": "admin", "password": admin_password},
        "admin_password_hash": admin_hash,
        "manager": {
            "id": manager.id,
            "username": "manager",
            "password": manager_password,
        },
        "guest": {"id": guest.id, "username": "guest", "password": guest_password},
        "disabled": {
            "id": disabled.id,
            "username": "disabled_user",
            "password": disabled_password,
        },
    }

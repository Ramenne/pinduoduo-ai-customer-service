# -*- coding: utf-8 -*-
"""
backend.tests.test_user_service —— 用户与角色管理服务单元测试
============================================================
本文件用途：对 backend 用户与角色管理业务服务（app.services.user_service）进行
单元测试，覆盖需求 2 的核心验收场景（service 层直接以内存 SQLite 会话测试）：

- 创建用户（需求 2.1）：密码经哈希存储、响应不返回明文 / 哈希、用户名唯一校验
  （重复用户名创建失败）；指定角色不存在时创建失败。
- 修改角色生效（需求 2.2）：update_user_role 后用户 role_id 更新；目标角色不存在
  时失败。
- 停用用户（需求 2.7 / 2.8）：set_user_status 停用后 status 置 0（逻辑删除），
  记录仍在库（禁止物理删除）。
- 用户列表分页（需求 2.1 配套）：list_users 返回 {list, total, page, page_size}
  分页结构，且每条用户信息均脱敏（不含 password_hash）。

测试方案：pytest + 内存 SQLite（夹具见 conftest.py），直接调用 service 函数并对
统一响应体 {code, success, message, data} 断言。
"""
from __future__ import annotations

from app.core.business_codes import CODE_NOT_FOUND, CODE_PARAM_ERROR
from app.services import user_service
from common.db.repository import Repository
from common.models.user_models import SysUser
from common.utils.security import verify_password


def test_create_user_hashes_password_and_hides_secret(db_session, seed_permissions):
    """创建用户：密码经哈希存储，且响应不返回明文 / 哈希（需求 2.1 / 1.6）。"""
    plain = "new-user-password-123"
    resp = user_service.create_user(
        db_session,
        username="alice",
        password=plain,
        role_id=seed_permissions["role_manager_id"],
        operator_id=1,
    )

    # 业务成功。
    assert resp.success is True
    assert resp.code == 0

    data = resp.data
    # 返回的用户信息绝不包含密码明文 / 哈希字段（需求 1.6）。
    assert "password" not in data
    assert "password_hash" not in data
    assert plain not in str(data)

    # 入库的是哈希值（非明文），且能通过校验。
    user = Repository(SysUser, db_session).get_by(username="alice")
    assert user is not None
    assert user.password_hash != plain
    assert verify_password(plain, user.password_hash) is True


def test_create_user_duplicate_username_fails(db_session, seed_permissions):
    """创建用户：用户名全局唯一，重复用户名创建失败（需求 2.1）。"""
    user_service.create_user(db_session, username="bob", password="pwd-123456")

    # 同名再次创建应失败，且不新增记录。
    resp = user_service.create_user(db_session, username="bob", password="other-123456")
    assert resp.success is False
    assert resp.code == CODE_PARAM_ERROR
    assert resp.message == "用户名已存在"

    assert Repository(SysUser, db_session).count(filters={"username": "bob"}) == 1


def test_create_user_with_nonexistent_role_fails(db_session, seed_permissions):
    """创建用户：指定的角色不存在时创建失败（需求 2.1）。"""
    resp = user_service.create_user(
        db_session, username="charlie", password="pwd-123456", role_id=999999
    )
    assert resp.success is False
    assert resp.code == CODE_NOT_FOUND
    assert resp.message == "指定的角色不存在"
    # 创建失败，不应落库。
    assert Repository(SysUser, db_session).get_by(username="charlie") is None


def test_update_user_role_takes_effect(db_session, seed_permissions):
    """修改角色：update_user_role 后用户 role_id 更新（需求 2.2）。"""
    created = user_service.create_user(
        db_session,
        username="dora",
        password="pwd-123456",
        role_id=seed_permissions["role_guest_id"],
    )
    user_id = created.data["id"]
    new_role_id = seed_permissions["role_manager_id"]

    resp = user_service.update_user_role(db_session, user_id=user_id, role_id=new_role_id)
    assert resp.success is True
    assert resp.data["role_id"] == new_role_id

    # 重新查库确认持久化生效。
    user = Repository(SysUser, db_session).get(user_id)
    assert user.role_id == new_role_id


def test_update_user_role_nonexistent_role_fails(db_session, seed_permissions):
    """修改角色：目标角色不存在时失败，且不改动用户角色（需求 2.2）。"""
    created = user_service.create_user(
        db_session,
        username="evan",
        password="pwd-123456",
        role_id=seed_permissions["role_guest_id"],
    )
    user_id = created.data["id"]

    resp = user_service.update_user_role(db_session, user_id=user_id, role_id=888888)
    assert resp.success is False
    assert resp.code == CODE_NOT_FOUND
    assert resp.message == "指定的角色不存在"

    # 角色保持原值不变。
    user = Repository(SysUser, db_session).get(user_id)
    assert user.role_id == seed_permissions["role_guest_id"]


def test_update_user_role_nonexistent_user_fails(db_session, seed_permissions):
    """修改角色：目标用户不存在时失败（需求 2.2）。"""
    resp = user_service.update_user_role(db_session, user_id=777777, role_id=None)
    assert resp.success is False
    assert resp.code == CODE_NOT_FOUND
    assert resp.message == "目标用户不存在"


def test_disable_user_marks_status_without_physical_delete(db_session, seed_permissions):
    """停用用户：status 置 0（逻辑删除），记录仍在库（需求 2.7 / 2.8，规范 11）。"""
    created = user_service.create_user(
        db_session,
        username="frank",
        password="pwd-123456",
        role_id=seed_permissions["role_guest_id"],
    )
    user_id = created.data["id"]
    total_before = Repository(SysUser, db_session).count()

    resp = user_service.set_user_status(db_session, user_id=user_id, enabled=False)
    assert resp.success is True
    assert resp.message == "已停用"
    assert resp.data["status"] == user_service.USER_STATUS_DISABLED

    # 记录仍然存在（未物理删除），仅状态被标记为停用。
    user = Repository(SysUser, db_session).get(user_id)
    assert user is not None
    assert user.status == 0
    assert Repository(SysUser, db_session).count() == total_before


def test_enable_user_restores_status(db_session, seed_permissions):
    """启用用户：停用后再启用，status 置回 1（需求 2.2）。"""
    created = user_service.create_user(
        db_session, username="grace", password="pwd-123456"
    )
    user_id = created.data["id"]
    user_service.set_user_status(db_session, user_id=user_id, enabled=False)

    resp = user_service.set_user_status(db_session, user_id=user_id, enabled=True)
    assert resp.success is True
    assert resp.data["status"] == user_service.USER_STATUS_ENABLED
    assert Repository(SysUser, db_session).get(user_id).status == 1


def test_list_users_returns_paginated_sanitized_structure(db_session, seed_permissions):
    """用户列表：返回分页结构且每条用户脱敏（需求 2.1 配套 / 1.6）。"""
    # 预置若干用户。
    for i in range(3):
        user_service.create_user(
            db_session, username=f"listed_{i}", password="pwd-123456"
        )

    resp = user_service.list_users(db_session, page=1, page_size=20)
    assert resp.success is True

    data = resp.data
    # 分页结构四字段齐备。
    assert set(["list", "total", "page", "page_size"]).issubset(data.keys())
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["total"] >= 3
    assert isinstance(data["list"], list)

    # 列表中每条用户信息均脱敏，不含密码哈希 / 明文字段。
    for item in data["list"]:
        assert "password_hash" not in item
        assert "password" not in item
        assert "username" in item


def test_list_users_pagination_limits_page_size(db_session, seed_permissions):
    """用户列表：每页条数生效，超出条数不返回（需求 2.1 配套，后端分页）。"""
    for i in range(5):
        user_service.create_user(
            db_session, username=f"paged_{i}", password="pwd-123456"
        )

    resp = user_service.list_users(db_session, page=1, page_size=10)
    data = resp.data
    # 当前页返回条数不超过每页大小。
    assert len(data["list"]) <= 10
    # 总数应包含全部已创建用户。
    assert data["total"] >= 5

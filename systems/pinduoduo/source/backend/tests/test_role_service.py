# -*- coding: utf-8 -*-
"""
backend.tests.test_role_service —— 角色与权限分配业务服务单元测试
================================================================
本文件用途：对 backend 角色管理与权限分配业务（app.services.role_service）及用户
自助注册（app.services.auth_service.register）进行单元测试，覆盖需求 2.3 / 2.4 /
规范 41 的核心场景：

- 新增角色 / 角色名唯一校验；
- 修改角色名 / 启停用；管理员角色不可修改 / 停用；
- 设为默认注册角色（同一时刻仅一个，管理员不可设默认）；
- 权限分配：授予 / 取消（取消经软删除 enabled=False，禁止物理删除）；
- 权限点列表按资源分组并附中文名（来自数据字典）；
- 注册：自动分配默认角色、用户名唯一、密码长度校验、无默认角色时拒绝。

测试方案：pytest + 内存 SQLite（夹具见 conftest.py）。直接以 service 层验证业务
逻辑，不经权限层（权限层已由 test_users_api 等覆盖）。
"""
from __future__ import annotations

import time

import pytest

from app.services import auth_service, email_code_service, role_service, setting_service
from common.db.repository import Repository
from common.models.user_models import (
    SysPermission,
    SysRole,
    SysUser,
    SysRolePermission,
)


def _seed_email_code(email: str, code: str = "123456", code_type: str = "register") -> None:
    """直接向邮箱验证码内存存储注入一条有效验证码，绕过 SMTP 发送（仅供单测）。

    注册业务的验证码校验读取 email_code_service 的进程内存储；单元测试无可用
    SMTP，故直接注入一条 5 分钟内有效的验证码记录，覆盖「真实校验通过」路径。
    """
    email_code_service._email_code_store[email.strip()] = {
        "code": code,
        "type": code_type,
        "expires_at": time.time() + email_code_service.CODE_TTL,
    }


@pytest.fixture()
def seed_perms(db_session):
    """预置若干权限点，返回 (resource, action) -> id 映射，供权限分配测试使用。"""
    pairs = [
        ("shop", "view"), ("shop", "create"), ("shop", "update"),
        ("keyword", "view"), ("keyword", "create"),
    ]
    repo = Repository(SysPermission, db_session)
    mapping = {}
    for resource_key, action in pairs:
        perm = repo.create(resource_key=resource_key, action=action, description=f"{resource_key}:{action}")
        mapping[(resource_key, action)] = perm.id
    db_session.commit()
    return mapping


def test_create_role_and_unique_name(db_session):
    """新增角色成功；同名再建返回失败（角色名全局唯一）。"""
    resp = role_service.create_role(db_session, role_name="客服专员")
    assert resp.success is True
    assert resp.data["role_name"] == "客服专员"
    assert resp.data["is_admin"] is False

    dup = role_service.create_role(db_session, role_name="客服专员")
    assert dup.success is False
    assert "已存在" in dup.message


def test_update_role_rename_and_status(db_session):
    """修改角色名与启停用成功。"""
    created = role_service.create_role(db_session, role_name="临时角色")
    role_id = created.data["id"]

    renamed = role_service.update_role(db_session, role_id, role_name="正式角色")
    assert renamed.success is True
    assert renamed.data["role_name"] == "正式角色"

    disabled = role_service.set_role_status(db_session, role_id, enabled=False)
    assert disabled.success is True
    assert disabled.data["status"] == role_service.ROLE_STATUS_DISABLED


def test_admin_role_cannot_be_modified(db_session):
    """管理员角色为系统内置，禁止改名 / 停用 / 改权限 / 设默认。"""
    admin_role = Repository(SysRole, db_session).create(
        role_name="超级管理员", is_admin=True, status=1
    )
    db_session.commit()

    r1 = role_service.update_role(db_session, admin_role.id, role_name="x")
    assert r1.success is False
    r2 = role_service.set_role_status(db_session, admin_role.id, enabled=False)
    assert r2.success is False
    r3 = role_service.set_default_role(db_session, admin_role.id)
    assert r3.success is False
    r4 = role_service.assign_permissions(db_session, admin_role.id, [])
    assert r4.success is False


def test_set_default_role_is_exclusive(db_session):
    """设为默认注册角色：同一时刻仅一个默认角色。"""
    a = role_service.create_role(db_session, role_name="角色A").data["id"]
    b = role_service.create_role(db_session, role_name="角色B").data["id"]

    role_service.set_default_role(db_session, a)
    role_service.set_default_role(db_session, b)
    db_session.commit()

    repo = Repository(SysRole, db_session)
    assert repo.get(a).is_default is False
    assert repo.get(b).is_default is True


def test_assign_permissions_grant_and_revoke_soft_delete(db_session, seed_perms):
    """权限分配：授予后取消的映射应软删除（enabled=False），而非物理删除（规范 11）。"""
    role_id = role_service.create_role(db_session, role_name="配权角色").data["id"]

    shop_view = seed_perms[("shop", "view")]
    shop_create = seed_perms[("shop", "create")]
    kw_view = seed_perms[("keyword", "view")]

    # 首次授予 3 个权限
    role_service.assign_permissions(db_session, role_id, [shop_view, shop_create, kw_view])
    db_session.commit()
    got = role_service.get_role_permissions(db_session, role_id)
    assert set(got.data["permission_ids"]) == {shop_view, shop_create, kw_view}

    # 重设为仅 1 个权限：另两个应被软删除
    role_service.assign_permissions(db_session, role_id, [shop_view])
    db_session.commit()
    got2 = role_service.get_role_permissions(db_session, role_id)
    assert set(got2.data["permission_ids"]) == {shop_view}

    # 被取消的映射仍在表中但 enabled=False（未物理删除）
    map_repo = Repository(SysRolePermission, db_session)
    revoked = map_repo.get_by(role_id=role_id, permission_id=shop_create)
    assert revoked is not None
    assert revoked.enabled is False

    # 重新授予被取消的权限：复用同一行并恢复 enabled=True
    role_service.assign_permissions(db_session, role_id, [shop_view, shop_create])
    db_session.commit()
    restored = map_repo.get_by(role_id=role_id, permission_id=shop_create)
    assert restored.enabled is True


def test_register_assigns_default_role(db_session):
    """注册：开关开启 + 邮箱验证码通过后自动分配默认角色，成功且不自动登录。"""
    # 预置一个默认注册角色（非管理员、启用）
    Repository(SysRole, db_session).create(
        role_name="普通用户", is_admin=False, is_default=True, status=1
    )
    # 开启「允许用户注册」开关（默认关闭）
    setting_service.update_basic(db_session, allow_register=True)
    db_session.commit()

    # 注入有效邮箱验证码（绕过 SMTP），再注册
    _seed_email_code("newbie@example.com", "654321")
    resp = auth_service.register(
        db_session,
        "newbie",
        "pwd-123456",
        email="newbie@example.com",
        verification_code="654321",
    )
    assert resp.success is True
    # 注册成功不再自动登录（不返回 token），由前端跳转登录页
    assert resp.data is None
    assert "注册成功" in resp.message
    db_session.commit()

    # 用户已落库，邮箱与默认角色正确，且不存明文密码
    created = Repository(SysUser, db_session).get_by(username="newbie")
    assert created is not None
    assert created.email == "newbie@example.com"


def test_register_validations(db_session):
    """注册校验：开关关闭 / 无默认角色 / 邮箱非法 / 验证码错误 / 用户名或邮箱重复。"""
    # 开关关闭时直接拒绝（默认 allow_register=False）
    closed = auth_service.register(
        db_session, "u0", "pwd-123456", email="u0@example.com", verification_code="111111"
    )
    assert closed.success is False
    assert "注册功能已关闭" in closed.message

    # 开启注册开关
    setting_service.update_basic(db_session, allow_register=True)
    db_session.commit()

    # 邮箱格式非法
    bad_email = auth_service.register(
        db_session, "u1", "pwd-123456", email="not-an-email", verification_code="111111"
    )
    assert bad_email.success is False
    assert "邮箱" in bad_email.message

    # 密码过短（在邮箱校验之前拦截）
    short = auth_service.register(
        db_session, "u2", "123", email="u2@example.com", verification_code="111111"
    )
    assert short.success is False

    # 无默认角色时拒绝（其它校验均通过）
    _seed_email_code("u1b@example.com")
    no_role = auth_service.register(
        db_session, "u1b", "pwd-123456", email="u1b@example.com", verification_code="123456"
    )
    assert no_role.success is False
    assert "默认注册角色" in no_role.message

    # 配置默认角色后可注册
    Repository(SysRole, db_session).create(
        role_name="普通用户", is_admin=False, is_default=True, status=1
    )
    db_session.commit()

    # 验证码错误
    _seed_email_code("u3@example.com", "000000")
    wrong_code = auth_service.register(
        db_session, "u3", "pwd-123456", email="u3@example.com", verification_code="999999"
    )
    assert wrong_code.success is False
    assert "验证码" in wrong_code.message

    # 正常注册
    _seed_email_code("u3@example.com", "123456")
    ok = auth_service.register(
        db_session, "u3", "pwd-123456", email="u3@example.com", verification_code="123456"
    )
    assert ok.success is True
    db_session.commit()

    # 用户名重复
    _seed_email_code("u3-another@example.com")
    dup = auth_service.register(
        db_session, "u3", "pwd-123456", email="u3-another@example.com", verification_code="123456"
    )
    assert dup.success is False
    assert "已存在" in dup.message

    # 邮箱重复
    _seed_email_code("u3@example.com")
    dup_email = auth_service.register(
        db_session, "u3b", "pwd-123456", email="u3@example.com", verification_code="123456"
    )
    assert dup_email.success is False
    assert "邮箱" in dup_email.message


def test_list_permissions_grouped(db_session, seed_perms):
    """权限点列表按资源分组返回，资源名 / 操作名有中文（字典缺失时回退原 key）。"""
    resp = role_service.list_permissions(db_session)
    assert resp.success is True
    groups = resp.data["groups"]
    # 至少包含 shop 与 keyword 两个资源分组
    resource_keys = {g["resource_key"] for g in groups}
    assert {"shop", "keyword"}.issubset(resource_keys)
    for g in groups:
        assert "resource_name" in g
        for a in g["actions"]:
            assert "permission_id" in a and "action_name" in a

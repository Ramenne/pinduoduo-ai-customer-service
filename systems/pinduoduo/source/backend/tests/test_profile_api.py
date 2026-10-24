# -*- coding: utf-8 -*-
"""
backend.tests.test_profile_api —— 个人设置接口单元测试
====================================================
本文件用途：对 backend 个人设置接口（账户信息只读展示 / 修改密码 / 联系方式）
进行单元测试，覆盖需求 22 的核心验收场景：

- 账户信息只读展示（需求 22.1）：返回用户名、角色，且不含密码哈希；
- 修改密码当前密码错误（需求 22.3）：返回 success=false、message「当前密码错误」；
- 修改密码成功令牌失效（需求 22.5）：成功后哈希更新、原令牌再访问受保护接口被拒；
- 联系方式按用户维度隔离（需求 22.6/22.7）：保存仅作用于当前用户，互不可见。

测试方案：pytest + FastAPI TestClient + SQLite 内存库（夹具见 conftest.py），
所有接口 HTTP 恒返回 200，业务成败由统一响应体 {code, success, message, data}
表达。
"""
from __future__ import annotations

from app.core.business_codes import CODE_AUTH_REQUIRED, CODE_PARAM_ERROR
from app.services.profile_service import MSG_CURRENT_PASSWORD_WRONG
from common.utils.security import verify_password

# 业务路由统一前缀（与 _bootstrap.API_PREFIX 默认值一致）。
API_PREFIX = "/api/v1"
LOGIN_URL = f"{API_PREFIX}/login"
PROFILE_URL = f"{API_PREFIX}/profile"
PASSWORD_URL = f"{API_PREFIX}/profile/password"
CONTACT_URL = f"{API_PREFIX}/profile/contact"


def _login(client, username: str, password: str) -> str:
    """登录并返回访问令牌字符串。"""
    resp = client.post(LOGIN_URL, json={"username": username, "password": password})
    body = resp.json()
    return body["data"]["token"]


def _auth(token: str) -> dict:
    """构造带 Bearer 令牌的鉴权请求头。"""
    return {"Authorization": f"Bearer {token}"}


def test_get_profile_returns_account_info_without_password(client, test_users):
    """账户信息只读展示：返回用户名与角色，且不含密码哈希（需求 22.1/1.6）。"""
    admin = test_users["admin"]
    token = _login(client, admin["username"], admin["password"])

    resp = client.get(PROFILE_URL, headers=_auth(token))
    body = resp.json()

    assert resp.status_code == 200
    assert body["success"] is True
    data = body["data"]
    assert data["username"] == admin["username"]
    # 角色信息随账户信息返回（只读展示）。
    assert data.get("role_name") == "管理员"
    # 不泄露密码哈希 / 明文。
    assert "password_hash" not in data
    assert test_users["admin_password_hash"] not in resp.text


def test_change_password_wrong_current_returns_fixed_message(client, test_users):
    """当前密码错误：返回 success=false、固定中文「当前密码错误」（需求 22.3）。"""
    admin = test_users["admin"]
    token = _login(client, admin["username"], admin["password"])

    resp = client.put(
        PASSWORD_URL,
        headers=_auth(token),
        json={"current_password": "wrong-current", "new_password": "new-password-123"},
    )
    body = resp.json()

    assert resp.status_code == 200
    assert body["success"] is False
    assert body["code"] == CODE_PARAM_ERROR
    assert body["message"] == MSG_CURRENT_PASSWORD_WRONG


def test_change_password_too_short_blocked(client, test_users):
    """新密码长度兜底：少于 6 位返回 success=false（需求 22.4 配套兜底）。"""
    admin = test_users["admin"]
    token = _login(client, admin["username"], admin["password"])

    resp = client.put(
        PASSWORD_URL,
        headers=_auth(token),
        json={"current_password": admin["password"], "new_password": "12345"},
    )
    body = resp.json()

    assert resp.status_code == 200
    assert body["success"] is False
    assert body["code"] == CODE_PARAM_ERROR


def test_change_password_success_updates_hash_and_revokes_token(
    client, test_users, db_session
):
    """修改成功：哈希更新 + 原令牌失效（需求 22.5）。"""
    admin = test_users["admin"]
    token = _login(client, admin["username"], admin["password"])
    new_password = "brand-new-password-123"

    resp = client.put(
        PASSWORD_URL,
        headers=_auth(token),
        json={"current_password": admin["password"], "new_password": new_password},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is True

    # 数据库中密码哈希已更新为新密码可校验。
    from common.db.repository import Repository
    from common.models.user_models import SysUser

    updated = Repository(SysUser, db_session).get(admin["id"])
    assert verify_password(new_password, updated.password_hash)

    # 原令牌已失效：再访问个人信息接口被拒（需求 22.5 / 1.4）。
    again = client.get(PROFILE_URL, headers=_auth(token))
    again_body = again.json()
    assert again.status_code == 200
    assert again_body["success"] is False
    assert again_body["code"] == CODE_AUTH_REQUIRED


def test_update_contact_persists_and_isolated_per_user(client, test_users):
    """联系方式：保存仅作用于当前用户，按用户维度隔离（需求 22.6/22.7）。"""
    admin = test_users["admin"]
    guest = test_users["guest"]

    admin_token = _login(client, admin["username"], admin["password"])
    guest_token = _login(client, guest["username"], guest["password"])

    # 管理员保存联系方式。
    resp = client.put(
        CONTACT_URL,
        headers=_auth(admin_token),
        json={"wechat": "admin_wx", "qq": "10001"},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is True
    assert body["data"]["wechat"] == "admin_wx"
    assert body["data"]["qq"] == "10001"

    # 管理员再查询，联系方式持久化生效。
    admin_profile = client.get(PROFILE_URL, headers=_auth(admin_token)).json()
    assert admin_profile["data"]["wechat"] == "admin_wx"

    # 访客查询：看不到管理员的联系方式（按用户维度隔离）。
    guest_profile = client.get(PROFILE_URL, headers=_auth(guest_token)).json()
    assert guest_profile["data"]["wechat"] is None
    assert guest_profile["data"]["qq"] is None

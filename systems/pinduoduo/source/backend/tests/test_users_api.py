# -*- coding: utf-8 -*-
"""
backend.tests.test_users_api —— 用户与角色管理接口单元测试
=========================================================
本文件用途：对 backend 用户与角色管理接口（app.api.routes.users）进行单元测试，
覆盖需求 2 中与鉴权 / 权限相关的接口级验收场景：

- 无权限访问被拒（需求 2.4）：非授权用户（角色未被授予对应权限）调用 users 接口，
  返回 success=false、message「无访问权限」（permission.check 返回 false 的场景）。
- 授权用户正常访问（需求 2.4）：被授予权限的用户调用 users 接口成功放行。
- 停用用户鉴权被拒（需求 2.7）：停用用户携带其令牌访问受保护接口被拒（结合
  get_current_user 校验 status）。
- 创建用户接口（需求 2.1）：经接口创建用户，响应不返回密码明文 / 哈希。

测试方案：pytest + FastAPI TestClient + 内存 SQLite（夹具见 conftest.py）。
所有接口 HTTP 恒返回 200，业务成败由统一响应体 {code, success, message, data}
表达，断言据此进行。
"""
from __future__ import annotations

from app.core.business_codes import (
    CODE_AUTH_REQUIRED,
    CODE_FORBIDDEN,
    MSG_FORBIDDEN,
)

API_PREFIX = "/api/v1"
LOGIN_URL = f"{API_PREFIX}/login"
USERS_URL = f"{API_PREFIX}/users"


def _login_token(client, username: str, password: str) -> str:
    """登录并返回访问令牌（断言登录成功）。"""
    resp = client.post(LOGIN_URL, json={"username": username, "password": password})
    body = resp.json()
    assert body["success"] is True, body
    return body["data"]["token"]


def _auth_header(token: str) -> dict:
    """构造 Bearer 鉴权请求头。"""
    return {"Authorization": f"Bearer {token}"}


def test_list_users_denied_for_unauthorized_user(client, test_users):
    """无权限访问被拒：访客角色（未授权）调用用户列表返回「无访问权限」（需求 2.4）。"""
    guest = test_users["guest"]
    token = _login_token(client, guest["username"], guest["password"])

    resp = client.get(USERS_URL, headers=_auth_header(token))
    body = resp.json()

    # HTTP 恒 200；业务失败、固定中文提示「无访问权限」。
    assert resp.status_code == 200
    assert body["success"] is False
    assert body["code"] == CODE_FORBIDDEN
    assert body["message"] == MSG_FORBIDDEN
    assert body["data"] is None


def test_create_user_denied_for_unauthorized_user(client, test_users):
    """无权限访问被拒：访客角色调用创建用户接口返回「无访问权限」（需求 2.4）。"""
    guest = test_users["guest"]
    token = _login_token(client, guest["username"], guest["password"])

    resp = client.post(
        USERS_URL,
        headers=_auth_header(token),
        json={"username": "should_not_create", "password": "pwd-123456"},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is False
    assert body["code"] == CODE_FORBIDDEN
    assert body["message"] == MSG_FORBIDDEN


def test_list_users_allowed_for_authorized_user(client, test_users):
    """授权用户正常访问：用户管理员角色（已授权）调用用户列表成功（需求 2.4）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])

    resp = client.get(USERS_URL, headers=_auth_header(token))
    body = resp.json()

    assert resp.status_code == 200
    assert body["success"] is True
    # 返回分页结构。
    assert set(["list", "total", "page", "page_size"]).issubset(body["data"].keys())


def test_create_user_via_api_hides_secret(client, test_users):
    """创建用户接口：授权用户创建成功，响应不返回密码明文 / 哈希（需求 2.1 / 1.6）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])

    plain = "api-created-password-123"
    resp = client.post(
        USERS_URL,
        headers=_auth_header(token),
        json={"username": "api_user", "password": plain},
    )
    body = resp.json()

    assert resp.status_code == 200
    assert body["success"] is True
    user = body["data"]
    assert user["username"] == "api_user"
    assert "password" not in user
    assert "password_hash" not in user
    # 整个响应体也不应泄露明文密码。
    assert plain not in resp.text


def test_disabled_user_rejected_on_protected_endpoint(client, test_users):
    """停用用户鉴权被拒：停用用户携带其令牌访问受保护接口被拒（需求 2.7）。

    先以授权管理员登录并通过接口将目标用户停用，再用其原令牌访问受保护接口，
    应被 get_current_user 依据 status 拒绝（业务码 40100）。
    """
    manager = test_users["manager"]
    manager_token = _login_token(client, manager["username"], manager["password"])

    # 创建一个普通用户并令其登录拿到令牌。
    create_resp = client.post(
        USERS_URL,
        headers=_auth_header(manager_token),
        json={"username": "to_be_disabled", "password": "pwd-123456"},
    )
    target = create_resp.json()
    assert target["success"] is True
    target_id = target["data"]["id"]

    target_token = _login_token(client, "to_be_disabled", "pwd-123456")
    # 停用前该用户可正常通过鉴权（无权限接口返回 403 而非 40100，说明已通过鉴权）。
    before = client.get(USERS_URL, headers=_auth_header(target_token)).json()
    assert before["code"] == CODE_FORBIDDEN

    # 管理员经接口停用该用户。
    status_resp = client.put(
        f"{USERS_URL}/{target_id}/status",
        headers=_auth_header(manager_token),
        json={"enabled": False},
    )
    assert status_resp.json()["success"] is True

    # 停用后，该用户原令牌再访问受保护接口应被拒（未登录 / 已失效，需求 2.7）。
    after = client.get(USERS_URL, headers=_auth_header(target_token)).json()
    assert after["success"] is False
    assert after["code"] == CODE_AUTH_REQUIRED


def test_missing_token_rejected(client, test_users):
    """缺失令牌：未携带 Authorization 访问 users 接口被拒（需求 1.4 / 2.7 前置鉴权）。"""
    resp = client.get(USERS_URL)
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is False
    assert body["code"] == CODE_AUTH_REQUIRED

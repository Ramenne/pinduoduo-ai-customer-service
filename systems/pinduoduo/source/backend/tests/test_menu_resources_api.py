# -*- coding: utf-8 -*-
"""
backend.tests.test_menu_resources_api —— 当前用户菜单授权资源接口测试
====================================================================
本文件用途：验证 ``GET /me/menu-resources`` 接口（需求 2.6）正确返回当前用户
「是否管理员」与「被授予 view 的资源键列表」，供前端按权限渲染左侧菜单。

覆盖场景：
- 管理员：is_admin=True；
- 授权用户（manager）：is_admin=False，resources 含被授予 view 的资源（user / role）；
- 缺令牌：返回未登录业务码（前置鉴权）。

测试方案：pytest + FastAPI TestClient + 内存 SQLite（夹具见 conftest.py）。
"""
from __future__ import annotations

from app.core.business_codes import CODE_AUTH_REQUIRED

API_PREFIX = "/api/v1"
LOGIN_URL = f"{API_PREFIX}/login"
MENU_RES_URL = f"{API_PREFIX}/me/menu-resources"


def _login_token(client, username: str, password: str) -> str:
    resp = client.post(LOGIN_URL, json={"username": username, "password": password})
    body = resp.json()
    assert body["success"] is True, body
    return body["data"]["token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_admin_menu_resources(client, test_users):
    """管理员：is_admin=True。"""
    admin = test_users["admin"]
    token = _login_token(client, admin["username"], admin["password"])
    body = client.get(MENU_RES_URL, headers=_auth_header(token)).json()
    assert body["success"] is True
    assert body["data"]["is_admin"] is True
    assert isinstance(body["data"]["resources"], list)


def test_authorized_user_menu_resources(client, test_users):
    """授权用户：非管理员，resources 含被授予 view 的资源（seed 中 user/role view）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    body = client.get(MENU_RES_URL, headers=_auth_header(token)).json()
    assert body["success"] is True
    assert body["data"]["is_admin"] is False
    resources = body["data"]["resources"]
    # conftest 的 manager 角色被授予 user 的 view/create/update/disable 与 role 的 view。
    assert "user" in resources
    assert "role" in resources


def test_menu_resources_requires_auth(client, test_users):
    """缺令牌：未登录访问返回未登录业务码。"""
    body = client.get(MENU_RES_URL).json()
    assert body["success"] is False
    assert body["code"] == CODE_AUTH_REQUIRED

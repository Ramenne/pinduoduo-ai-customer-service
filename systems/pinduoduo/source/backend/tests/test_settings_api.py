# -*- coding: utf-8 -*-
"""
backend.tests.test_settings_api —— 系统设置接口单元测试（任务 8.1）
=================================================================
本文件用途：对 backend 系统设置（app.api.routes.settings 与
app.services.setting_service）进行单元测试，覆盖任务 8.1 对应需求核心验收场景：

- 主题外观持久化并生效（需求 21.9）：保存后可查出，明暗模式非法返回失败。
- 分页默认值（需求 21.1）：仅允许 10/20/50/100，非法值返回失败。
- 基础设置（需求 21.6）：日志保留天数 1~365 边界校验。
- 登录页品牌 / 免责声明 / 二维码持久化（需求 21.11/21.12/21.13）。
- 非管理员拒绝访问系统设置接口（需求 21.17）：返回「无访问权限」。
- 系统设置总览返回全部分组（需求 21.1）。

测试方案：pytest + FastAPI TestClient + 内存 SQLite（夹具见 conftest.py）。
所有接口 HTTP 恒返回 200，业务成败由统一响应体表达。
"""
from __future__ import annotations

import pytest

from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from common.models.user_models import SysRole, SysUser
from common.utils.security import hash_password

API_PREFIX = "/api/v1"
LOGIN_URL = f"{API_PREFIX}/login"
SETTINGS_URL = f"{API_PREFIX}/settings"


@pytest.fixture()
def settings_env(db_session):
    """预置管理员与非管理员用户，用于系统设置权限校验（需求 21.17）。"""
    role_admin = SysRole(role_name="管理员", is_admin=True, status=1)
    role_normal = SysRole(role_name="普通用户", is_admin=False, status=1)
    db_session.add_all([role_admin, role_normal])
    db_session.flush()

    admin_password = "admin-password-123"
    normal_password = "normal-password-123"
    admin = SysUser(
        username="settings_admin",
        password_hash=hash_password(admin_password),
        role_id=role_admin.id,
        status=1,
    )
    normal = SysUser(
        username="settings_normal",
        password_hash=hash_password(normal_password),
        role_id=role_normal.id,
        status=1,
    )
    db_session.add_all([admin, normal])
    db_session.flush()
    db_session.commit()

    return {
        "admin": {"username": "settings_admin", "password": admin_password},
        "normal": {"username": "settings_normal", "password": normal_password},
    }


def _login_token(client, username: str, password: str) -> str:
    """登录并返回访问令牌（断言登录成功）。"""
    resp = client.post(LOGIN_URL, json={"username": username, "password": password})
    body = resp.json()
    assert body["success"] is True, body
    return body["data"]["token"]


def _auth_header(token: str) -> dict:
    """构造 Bearer 鉴权请求头。"""
    return {"Authorization": f"Bearer {token}"}


# ----------------------------------------------------------------------
# 权限：非管理员拒绝访问（需求 21.17）
# ----------------------------------------------------------------------
def test_settings_denied_for_non_admin(client, settings_env):
    """非管理员访问系统设置被拒（需求 21.17）。"""
    token = _login_token(
        client, settings_env["normal"]["username"], settings_env["normal"]["password"]
    )
    resp = client.get(SETTINGS_URL, headers=_auth_header(token))
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is False
    assert body["code"] == CODE_FORBIDDEN
    assert body["message"] == MSG_FORBIDDEN


def test_settings_update_denied_for_non_admin(client, settings_env):
    """非管理员保存系统设置被拒（需求 21.17）。"""
    token = _login_token(
        client, settings_env["normal"]["username"], settings_env["normal"]["password"]
    )
    resp = client.put(
        f"{SETTINGS_URL}/theme",
        headers=_auth_header(token),
        json={"theme_color": "#ff0000"},
    )
    body = resp.json()
    assert body["success"] is False
    assert body["code"] == CODE_FORBIDDEN


# ----------------------------------------------------------------------
# 主题外观（需求 21.9）
# ----------------------------------------------------------------------
def test_theme_persist_and_get(client, settings_env):
    """主题外观保存后可查出（需求 21.9）。"""
    token = _login_token(
        client, settings_env["admin"]["username"], settings_env["admin"]["password"]
    )
    resp = client.put(
        f"{SETTINGS_URL}/theme",
        headers=_auth_header(token),
        json={"theme_color": "#ff0000", "dark_mode": "dark", "font_family": "宋体"},
    )
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["theme_color"] == "#ff0000"
    assert body["data"]["dark_mode"] == "dark"

    # 再查询确认持久化生效。
    resp2 = client.get(f"{SETTINGS_URL}/theme", headers=_auth_header(token))
    body2 = resp2.json()
    assert body2["success"] is True
    assert body2["data"]["theme_color"] == "#ff0000"
    assert body2["data"]["font_family"] == "宋体"


def test_theme_invalid_dark_mode_rejected(client, settings_env):
    """明暗模式非法返回失败（需求 21.9）。"""
    token = _login_token(
        client, settings_env["admin"]["username"], settings_env["admin"]["password"]
    )
    resp = client.put(
        f"{SETTINGS_URL}/theme",
        headers=_auth_header(token),
        json={"dark_mode": "rainbow"},
    )
    body = resp.json()
    assert body["success"] is False


# ----------------------------------------------------------------------
# 分页默认值（需求 21.1）
# ----------------------------------------------------------------------
def test_pagination_valid_and_invalid(client, settings_env):
    """分页默认值仅允许 10/20/50/100（需求 21.1）。"""
    token = _login_token(
        client, settings_env["admin"]["username"], settings_env["admin"]["password"]
    )
    ok = client.put(
        f"{SETTINGS_URL}/pagination",
        headers=_auth_header(token),
        json={"default_page_size": 50},
    ).json()
    assert ok["success"] is True
    assert ok["data"]["default_page_size"] == 50

    bad = client.put(
        f"{SETTINGS_URL}/pagination",
        headers=_auth_header(token),
        json={"default_page_size": 33},
    ).json()
    assert bad["success"] is False


# ----------------------------------------------------------------------
# 基础设置（需求 21.6）
# ----------------------------------------------------------------------
def test_basic_log_retention_boundary(client, settings_env):
    """日志保留天数 1~365 边界校验（需求 21.6）。"""
    token = _login_token(
        client, settings_env["admin"]["username"], settings_env["admin"]["password"]
    )
    ok = client.put(
        f"{SETTINGS_URL}/basic",
        headers=_auth_header(token),
        json={"log_retention_days": 365, "allow_register": True},
    ).json()
    assert ok["success"] is True
    assert ok["data"]["log_retention_days"] == 365
    assert ok["data"]["allow_register"] is True

    for bad_value in (0, 366):
        bad = client.put(
            f"{SETTINGS_URL}/basic",
            headers=_auth_header(token),
            json={"log_retention_days": bad_value},
        ).json()
        assert bad["success"] is False, bad_value


# ----------------------------------------------------------------------
# 品牌 / 免责声明 / 二维码
# ----------------------------------------------------------------------
def test_brand_disclaimer_qrcodes(client, settings_env):
    """品牌/免责声明/二维码持久化（需求 21.11/21.12/21.13）。"""
    token = _login_token(
        client, settings_env["admin"]["username"], settings_env["admin"]["password"]
    )
    header = _auth_header(token)

    brand = client.put(
        f"{SETTINGS_URL}/brand",
        headers=header,
        json={"system_name": "测试系统", "title": "标题", "description": "描述"},
    ).json()
    assert brand["success"] is True
    assert brand["data"]["system_name"] == "测试系统"

    disclaimer = client.put(
        f"{SETTINGS_URL}/disclaimer",
        headers=header,
        json={"title": "免责", "content": "正文内容"},
    ).json()
    assert disclaimer["success"] is True
    assert disclaimer["data"]["content"] == "正文内容"

    qr = client.put(
        f"{SETTINGS_URL}/qrcodes",
        headers=header,
        json={"items": [{"type": "wechat", "image_url": "http://x/y.png"}]},
    ).json()
    assert qr["success"] is True
    assert qr["data"]["items"][0]["type"] == "wechat"

    # 二维码项缺少字段返回失败。
    qr_bad = client.put(
        f"{SETTINGS_URL}/qrcodes",
        headers=header,
        json={"items": [{"type": "wechat"}]},
    ).json()
    assert qr_bad["success"] is False


# ----------------------------------------------------------------------
# 总览（需求 21.1）
# ----------------------------------------------------------------------
def test_get_all_settings(client, settings_env):
    """系统设置总览返回全部分组（需求 21.1）。"""
    token = _login_token(
        client, settings_env["admin"]["username"], settings_env["admin"]["password"]
    )
    resp = client.get(SETTINGS_URL, headers=_auth_header(token))
    body = resp.json()
    assert body["success"] is True
    for key in (
        "theme",
        "pagination",
        "basic",
        "brand",
        "disclaimer",
        "qrcodes",
    ):
        assert key in body["data"]

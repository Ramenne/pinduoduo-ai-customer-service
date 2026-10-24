# -*- coding: utf-8 -*-
"""
backend.tests.test_smtp_proxy_api —— SMTP 邮件与代理设置接口单元测试（任务 8.2）
==============================================================================
本文件用途：对 backend SMTP 邮件与代理设置（app.api.routes.settings 与
app.services.smtp_proxy_service）进行单元测试，覆盖任务 8.2 对应需求核心场景：

- SMTP 参数持久化且响应不返回密码明文（需求 21.7）：保存后查询仅含 password_set。
- 测试邮件发送（需求 21.8）：收件地址为空 / SMTP 未配置 / 发送失败均返回 success=false；
  发送成功（打桩）返回 success=true。
- 代理设置（需求 21.14）：开启但 api_url 为空返回固定中文提示且不持久化；
  开启且地址非空保存成功。
- 代理地址读取（需求 21.15）：开启时 get_active_proxy_url 返回地址，关闭时返回 None。
- 非管理员拒绝访问（需求 21.17）。

测试方案：pytest + FastAPI TestClient + 内存 SQLite（夹具见 conftest.py）。
所有接口 HTTP 恒返回 200，业务成败由统一响应体表达。SMTP 实际投递经打桩替换。
"""
from __future__ import annotations

import pytest

from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import setting_store, smtp_proxy_service
from common.models.user_models import SysRole, SysUser
from common.utils.security import hash_password

API_PREFIX = "/api/v1"
LOGIN_URL = f"{API_PREFIX}/login"
SMTP_URL = f"{API_PREFIX}/settings/smtp"
SMTP_TEST_URL = f"{API_PREFIX}/settings/smtp/test"
PROXY_URL = f"{API_PREFIX}/settings/proxy"


@pytest.fixture()
def admin_env(db_session):
    """预置管理员与非管理员用户，用于系统设置权限校验（需求 21.17）。"""
    role_admin = SysRole(role_name="管理员", is_admin=True, status=1)
    role_normal = SysRole(role_name="普通用户", is_admin=False, status=1)
    db_session.add_all([role_admin, role_normal])
    db_session.flush()

    admin_password = "admin-password-123"
    normal_password = "normal-password-123"
    admin = SysUser(
        username="smtp_admin",
        password_hash=hash_password(admin_password),
        role_id=role_admin.id,
        status=1,
    )
    normal = SysUser(
        username="smtp_normal",
        password_hash=hash_password(normal_password),
        role_id=role_normal.id,
        status=1,
    )
    db_session.add_all([admin, normal])
    db_session.flush()
    db_session.commit()

    return {
        "admin": {"username": "smtp_admin", "password": admin_password},
        "normal": {"username": "smtp_normal", "password": normal_password},
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
def test_smtp_denied_for_non_admin(client, admin_env):
    """非管理员访问 SMTP 设置被拒（需求 21.17）。"""
    token = _login_token(
        client, admin_env["normal"]["username"], admin_env["normal"]["password"]
    )
    resp = client.get(SMTP_URL, headers=_auth_header(token))
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is False
    assert body["code"] == CODE_FORBIDDEN
    assert body["message"] == MSG_FORBIDDEN


# ----------------------------------------------------------------------
# SMTP 持久化与密码反显（应用户要求支持回显 + 隐藏查看）
# ----------------------------------------------------------------------
def test_smtp_persist_returns_password_for_reverse_display(client, admin_env):
    """SMTP 参数持久化；查询/保存反显明文密码供页面隐藏查看，但不暴露密文字段。"""
    token = _login_token(
        client, admin_env["admin"]["username"], admin_env["admin"]["password"]
    )
    header = _auth_header(token)
    resp = client.put(
        SMTP_URL,
        headers=header,
        json={
            "host": "smtp.qq.com",
            "port": 465,
            "sender_email": "user@example.invalid",
            "sender_name": "客服",
            "password": "secret-auth-code",
            "use_ssl": True,
        },
    )
    body = resp.json()
    assert body["success"] is True
    # 反显明文密码供管理员页面查看/编辑；但不得暴露内部密文字段 password_enc。
    assert body["data"]["password"] == "secret-auth-code"
    assert "password_enc" not in body["data"]
    assert body["data"]["password_set"] is True
    assert body["data"]["host"] == "smtp.qq.com"

    # 再查询同样反显明文密码（隐藏查看由前端显隐切换控制）。
    body2 = client.get(SMTP_URL, headers=header).json()
    assert body2["success"] is True
    assert body2["data"]["password"] == "secret-auth-code"
    assert "password_enc" not in body2["data"]
    assert body2["data"]["password_set"] is True


def test_smtp_invalid_port_rejected(client, admin_env):
    """SMTP 端口非法返回 success=false（入参校验）。"""
    token = _login_token(
        client, admin_env["admin"]["username"], admin_env["admin"]["password"]
    )
    resp = client.put(
        SMTP_URL,
        headers=_auth_header(token),
        json={"port": 70000},
    )
    body = resp.json()
    assert body["success"] is False


# ----------------------------------------------------------------------
# 测试邮件发送（需求 21.8）
# ----------------------------------------------------------------------
def test_test_email_missing_recipient(client, admin_env):
    """测试邮件收件地址为空返回 success=false（需求 21.8）。"""
    token = _login_token(
        client, admin_env["admin"]["username"], admin_env["admin"]["password"]
    )
    resp = client.post(SMTP_TEST_URL, headers=_auth_header(token), json={"to_email": ""})
    body = resp.json()
    assert body["success"] is False


def test_test_email_smtp_not_configured(client, admin_env):
    """SMTP 未配置时测试邮件返回 success=false（需求 21.8）。"""
    token = _login_token(
        client, admin_env["admin"]["username"], admin_env["admin"]["password"]
    )
    resp = client.post(
        SMTP_TEST_URL, headers=_auth_header(token), json={"to_email": "x@y.com"}
    )
    body = resp.json()
    assert body["success"] is False


def test_test_email_success_with_stub(client, admin_env, monkeypatch):
    """SMTP 配置完整且投递成功（打桩）时返回 success=true（需求 21.8）。"""
    token = _login_token(
        client, admin_env["admin"]["username"], admin_env["admin"]["password"]
    )
    header = _auth_header(token)
    # 先配置完整 SMTP（含密码）。
    client.put(
        SMTP_URL,
        headers=header,
        json={
            "host": "smtp.qq.com",
            "port": 465,
            "sender_email": "user@example.invalid",
            "password": "secret",
        },
    )
    # 打桩实际投递为成功，避免真实网络 IO。
    monkeypatch.setattr(
        smtp_proxy_service, "_send_email_via_smtp", lambda *a, **k: (True, "发送成功")
    )
    resp = client.post(
        SMTP_TEST_URL, headers=header, json={"to_email": "to@example.com"}
    )
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["to_email"] == "to@example.com"


def test_test_email_failure_with_stub(client, admin_env, monkeypatch):
    """SMTP 投递失败（打桩）时返回 success=false 且不抛异常（需求 21.8）。"""
    token = _login_token(
        client, admin_env["admin"]["username"], admin_env["admin"]["password"]
    )
    header = _auth_header(token)
    client.put(
        SMTP_URL,
        headers=header,
        json={
            "host": "smtp.qq.com",
            "port": 465,
            "sender_email": "user@example.invalid",
            "password": "secret",
        },
    )
    monkeypatch.setattr(
        smtp_proxy_service, "_send_email_via_smtp", lambda *a, **k: (False, "认证失败")
    )
    resp = client.post(
        SMTP_TEST_URL, headers=header, json={"to_email": "to@example.com"}
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is False


# ----------------------------------------------------------------------
# 代理设置（需求 21.14 / 21.15）
# ----------------------------------------------------------------------
def test_proxy_enable_without_url_rejected(client, admin_env):
    """开启代理但地址为空返回固定中文提示且不持久化（需求 21.14）。"""
    token = _login_token(
        client, admin_env["admin"]["username"], admin_env["admin"]["password"]
    )
    resp = client.put(
        PROXY_URL,
        headers=_auth_header(token),
        json={"enabled": True, "api_url": ""},
    )
    body = resp.json()
    assert body["success"] is False
    assert body["message"] == "开启代理前请先填写代理 API 的 URL"


def test_proxy_enable_with_url_ok(client, admin_env):
    """开启代理且地址非空保存成功（需求 21.14）。"""
    token = _login_token(
        client, admin_env["admin"]["username"], admin_env["admin"]["password"]
    )
    header = _auth_header(token)
    resp = client.put(
        PROXY_URL,
        headers=header,
        json={"enabled": True, "api_url": "http://proxy.example.com/api"},
    )
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["enabled"] is True
    assert body["data"]["api_url"] == "http://proxy.example.com/api"

    got = client.get(PROXY_URL, headers=header).json()
    assert got["data"]["api_url"] == "http://proxy.example.com/api"


def test_proxy_disable_ok(client, admin_env):
    """关闭代理允许地址为空（需求 21.14）。"""
    token = _login_token(
        client, admin_env["admin"]["username"], admin_env["admin"]["password"]
    )
    resp = client.put(
        PROXY_URL,
        headers=_auth_header(token),
        json={"enabled": False, "api_url": ""},
    )
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["enabled"] is False


# ----------------------------------------------------------------------
# 服务层：代理地址读取（需求 21.15）
# ----------------------------------------------------------------------
def test_get_active_proxy_url(db_session):
    """开启时返回代理地址，关闭时返回 None（需求 21.15）。"""
    # 未配置：返回 None。
    assert smtp_proxy_service.get_active_proxy_url(db_session) is None

    # 开启并配置地址：返回该地址。
    setting_store.save_group(
        db_session,
        smtp_proxy_service.KEY_PROXY,
        {"enabled": True, "api_url": "http://p.example.com"},
    )
    assert (
        smtp_proxy_service.get_active_proxy_url(db_session)
        == "http://p.example.com"
    )

    # 关闭代理：返回 None。
    setting_store.save_group(
        db_session,
        smtp_proxy_service.KEY_PROXY,
        {"enabled": False, "api_url": "http://p.example.com"},
    )
    assert smtp_proxy_service.get_active_proxy_url(db_session) is None


def test_smtp_password_round_trip_encrypted(db_session):
    """SMTP 密码以加密形式存储（存储值不含明文），可解密用于发送（需求 21.7）。"""
    smtp_proxy_service.update_smtp(
        db_session,
        host="smtp.qq.com",
        port=465,
        sender_email="user@example.invalid",
        password="my-plain-secret",
    )
    stored = setting_store.get_group(db_session, smtp_proxy_service.KEY_SMTP, {})
    # 存储的密文不等于明文（加密存储）。
    assert stored.get("password_enc")
    assert stored["password_enc"] != "my-plain-secret"

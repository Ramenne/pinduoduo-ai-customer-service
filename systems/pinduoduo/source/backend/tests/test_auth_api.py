# -*- coding: utf-8 -*-
"""
backend.tests.test_auth_api —— 认证接口单元测试
==============================================
本文件用途：对 backend 认证接口（登录 / 登出 / 受保护接口鉴权）进行单元测试，
覆盖需求 1 的核心验收场景：

- 登录成功（需求 1.1）：正确用户名密码返回 token 与 user，且 user 不含密码哈希；
- 登录失败（需求 1.2）：用户名 / 密码错误返回 success=false、message「用户名或
  密码错误」，HTTP 状态码恒为 200；
- 登出令牌失效（需求 1.5）：登出后原 token 再访问受保护接口被拒，业务码标识
  「未登录或登录已过期」（40100）；
- 无效 / 过期令牌拒绝（需求 1.4）：无效令牌访问受保护接口返回 success=false、
  业务码 40100；
- 停用用户拒绝（需求 2.7/2.8 相关）：停用用户登录被拒。

测试方案：pytest + FastAPI TestClient + SQLite 内存库（夹具见 conftest.py）。
所有接口 HTTP 恒返回 200，业务成败由统一响应体 {code, success, message, data}
表达，断言据此进行。
"""
from __future__ import annotations

from app.api.deps import get_current_user
from app.core.business_codes import (
    CODE_AUTH_REQUIRED,
    CODE_LOGIN_FAILED,
    MSG_LOGIN_FAILED,
)
from common.models.user_models import SysUser

# 业务路由统一前缀（与 _bootstrap.API_PREFIX 默认值一致）。
API_PREFIX = "/api/v1"
LOGIN_URL = f"{API_PREFIX}/login"
LOGOUT_URL = f"{API_PREFIX}/logout"


def _login(client, username: str, password: str):
    """发起登录请求并返回 (HTTP 响应, 响应体 JSON) 二元组。"""
    resp = client.post(LOGIN_URL, json={"username": username, "password": password})
    return resp, resp.json()


def test_login_success_returns_token_and_user_without_password(client, test_users):
    """登录成功：返回 token 与 user，且 user 不含密码哈希（需求 1.1/1.6）。"""
    admin = test_users["admin"]
    resp, body = _login(client, admin["username"], admin["password"])

    # HTTP 恒 200，业务成功 code=0、success=true。
    assert resp.status_code == 200
    assert body["code"] == 0
    assert body["success"] is True

    data = body["data"]
    # 返回包含非空 token 与用户信息。
    assert isinstance(data.get("token"), str) and data["token"]
    user = data["user"]
    assert user["username"] == admin["username"]

    # 关键：响应中绝不出现密码哈希 / 明文等敏感字段（需求 1.6）。
    assert "password_hash" not in user
    assert "password" not in user
    # 整个响应体序列化后也不应泄露入库的哈希值。
    assert test_users["admin_password_hash"] not in resp.text


def test_login_wrong_password_fails_with_http_200(client, test_users):
    """登录失败：密码错误返回 success=false、固定中文 message、HTTP 200（需求 1.2）。"""
    admin = test_users["admin"]
    resp, body = _login(client, admin["username"], "wrong-password")

    assert resp.status_code == 200
    assert body["success"] is False
    assert body["code"] == CODE_LOGIN_FAILED
    assert body["message"] == MSG_LOGIN_FAILED
    assert body["data"] is None


def test_login_unknown_username_fails(client, test_users):
    """登录失败：用户名不存在同样返回「用户名或密码错误」，不泄露用户是否存在（需求 1.2）。"""
    resp, body = _login(client, "no_such_user", "whatever123456")

    assert resp.status_code == 200
    assert body["success"] is False
    assert body["code"] == CODE_LOGIN_FAILED
    assert body["message"] == MSG_LOGIN_FAILED


def test_login_disabled_user_rejected(client, test_users):
    """停用用户拒绝登录：返回 success=false（需求 2.7/2.8）。"""
    disabled = test_users["disabled"]
    resp, body = _login(client, disabled["username"], disabled["password"])

    assert resp.status_code == 200
    assert body["success"] is False
    # 停用账号使用「账号已被停用」提示，业务码仍为登录失败码。
    assert body["code"] == CODE_LOGIN_FAILED
    assert "停用" in body["message"]


def test_logout_revokes_token_then_protected_access_rejected(client, test_users):
    """登出令牌失效：登出后原 token 再访问受保护接口被拒（需求 1.5/1.4）。"""
    admin = test_users["admin"]
    _, login_body = _login(client, admin["username"], admin["password"])
    token = login_body["data"]["token"]
    auth_header = {"Authorization": f"Bearer {token}"}

    # 登出成功。
    logout_resp = client.post(LOGOUT_URL, headers=auth_header)
    logout_body = logout_resp.json()
    assert logout_resp.status_code == 200
    assert logout_body["success"] is True

    # 注入一个临时受保护接口以验证鉴权依赖对「已登出令牌」的拒绝。
    _mount_protected_probe()
    try:
        probe_resp = client.get("/__test_protected__", headers=auth_header)
        probe_body = probe_resp.json()
        # HTTP 恒 200；业务码标识「未登录或登录已过期」（40100）。
        assert probe_resp.status_code == 200
        assert probe_body["success"] is False
        assert probe_body["code"] == CODE_AUTH_REQUIRED
    finally:
        _unmount_protected_probe()


def test_invalid_token_rejected_on_protected_endpoint(client, test_users):
    """无效令牌拒绝：携带伪造令牌访问受保护接口返回 40100（需求 1.4）。"""
    _mount_protected_probe()
    try:
        resp = client.get(
            "/__test_protected__",
            headers={"Authorization": "Bearer this.is.invalid.token"},
        )
        body = resp.json()
        assert resp.status_code == 200
        assert body["success"] is False
        assert body["code"] == CODE_AUTH_REQUIRED
    finally:
        _unmount_protected_probe()


def test_missing_token_rejected_on_protected_endpoint(client, test_users):
    """缺失令牌拒绝：未携带 Authorization 头访问受保护接口返回 40100（需求 1.4）。"""
    _mount_protected_probe()
    try:
        resp = client.get("/__test_protected__")
        body = resp.json()
        assert resp.status_code == 200
        assert body["success"] is False
        assert body["code"] == CODE_AUTH_REQUIRED
    finally:
        _unmount_protected_probe()


# ----------------------------------------------------------------------
# 测试专用：临时受保护探针接口
# ----------------------------------------------------------------------
# backend 当前已实现的受保护接口为登出（POST /logout），但登出依赖会主动
# 失效令牌，不适合复用为「鉴权是否放行」的探针。这里在测试期临时挂载一个仅
# 依赖 get_current_user 的 GET 接口，用于纯粹验证鉴权依赖的放行 / 拒绝行为。
from _bootstrap import app  # noqa: E402

_PROBE_PATH = "/__test_protected__"


def _protected_probe(current_user: SysUser = None):  # pragma: no cover - 由 FastAPI 注入
    """受保护探针：成功通过鉴权时返回当前用户名。"""
    return {"code": 0, "success": True, "message": "ok", "data": {"username": current_user.username}}


def _mount_protected_probe() -> None:
    """在应用上临时注册受保护探针接口（依赖 get_current_user）。"""
    from fastapi import Depends

    # 避免重复注册：若已存在同路径路由则跳过。
    if any(getattr(r, "path", None) == _PROBE_PATH for r in app.router.routes):
        return

    async def _endpoint(current_user: SysUser = Depends(get_current_user)):
        return {
            "code": 0,
            "success": True,
            "message": "ok",
            "data": {"username": current_user.username},
        }

    app.add_api_route(_PROBE_PATH, _endpoint, methods=["GET"])


def _unmount_protected_probe() -> None:
    """移除测试期临时注册的受保护探针接口，避免污染其它用例。"""
    app.router.routes = [
        r for r in app.router.routes if getattr(r, "path", None) != _PROBE_PATH
    ]

# -*- coding: utf-8 -*-
"""
backend.tests.test_risk_control_api —— 风控规则配置接口/服务单元测试
==================================================================
本文件用途：对 backend 风控规则配置（app.api.routes.risk_control 与
app.services.risk_control_service）进行单元测试，覆盖需求 13（风控管理）
核心验收场景：

- 配置风控规则成功并持久化（需求 13.1）：合法入参保存并返回配置信息。
- 风控规则按店铺 upsert 幂等（需求 13.1）：重复配置覆盖更新，单条记录。
- 查询未配置返回 data=null：便于前端区分未配置 / 已配置。
- 入参校验失败：负数频率上限返回 success=false。
- 风控类型枚举字典查询（需求 13.4）：返回 risk_type 字典中文文案。
- 数据范围隔离（需求 3.7）：非管理员对非本人店铺配置被拒。
- 无权限访问被拒（需求 2.4）：未授权用户调用返回「无访问权限」。

测试方案：pytest + FastAPI TestClient + 内存 SQLite（夹具见 conftest.py）。
本模块为 risk_control 资源补充权限点与授权用户，并预置店铺与风控类型字典。
所有接口 HTTP 恒返回 200，业务成败由统一响应体表达。
"""
from __future__ import annotations

import pytest

from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import risk_control_service
from common.models.config_models import RiskRule
from common.models.setting_models import SysDict
from common.models.shop_models import Shop
from common.models.user_models import (
    SysPermission,
    SysRole,
    SysRolePermission,
    SysUser,
)
from common.utils.security import hash_password

API_PREFIX = "/api/v1"
LOGIN_URL = f"{API_PREFIX}/login"
RISK_TYPES_URL = f"{API_PREFIX}/risk-types"


def _risk_rule_url(shop_pk: int) -> str:
    """构造店铺风控规则接口 URL。"""
    return f"{API_PREFIX}/shops/{shop_pk}/risk-rule"


# ----------------------------------------------------------------------
# 夹具：权限点 + 授权 / 无权限用户 + 店铺 + 风控类型字典
# ----------------------------------------------------------------------
@pytest.fixture()
def risk_env(db_session):
    """预置 shop 资源权限、用户、店铺与风控类型字典。

    构造：
    - rc 角色：被授予 shop 资源的 view/update 权限（风控等店铺级设置统一用 shop
      判权）；rc_user 为其下授权用户（非管理员）；
    - 另一普通用户 other_user（无任何权限，用于越权 / 无权限场景）；
    - rc_user 名下店铺 shop_a（owner=rc_user），用于数据范围隔离；
    - 风控类型字典 risk_type: frequency_limit/risk_message/reconnect_fail。
    """
    role_rc = SysRole(role_name="风控管理员", is_admin=False, status=1)
    role_other = SysRole(role_name="其它用户", is_admin=False, status=1)
    db_session.add_all([role_rc, role_other])
    db_session.flush()

    perm_ids = []
    for action in ("view", "update"):
        perm = SysPermission(resource_key="shop", action=action)
        db_session.add(perm)
        db_session.flush()
        perm_ids.append(perm.id)
    for pid in perm_ids:
        db_session.add(SysRolePermission(role_id=role_rc.id, permission_id=pid))
    db_session.flush()

    rc_password = "rc-password-123"
    other_password = "other-password-123"
    rc_user = SysUser(
        username="rc_user",
        password_hash=hash_password(rc_password),
        role_id=role_rc.id,
        status=1,
    )
    other_user = SysUser(
        username="other_user",
        password_hash=hash_password(other_password),
        role_id=role_other.id,
        status=1,
    )
    db_session.add_all([rc_user, other_user])
    db_session.flush()

    shop_a = Shop(
        shop_id="pdd-shop-a",
        shop_name="店铺A",
        owner_user_id=rc_user.id,
        status=1,
    )
    db_session.add(shop_a)
    db_session.flush()

    # 风控类型字典（需求 13.4）。
    for idx, (key, label) in enumerate(
        (
            ("frequency_limit", "频率限制"),
            ("risk_message", "风险消息"),
            ("reconnect_fail", "重连失败"),
        ),
        start=1,
    ):
        db_session.add(
            SysDict(
                dict_type="risk_type",
                dict_key=key,
                dict_label=label,
                order_no=idx,
                enabled=True,
            )
        )
    db_session.flush()
    db_session.commit()

    return {
        "rc_user": {"username": "rc_user", "password": rc_password, "id": rc_user.id},
        "other_user": {
            "username": "other_user",
            "password": other_password,
            "id": other_user.id,
        },
        "shop_a_pk": shop_a.id,
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
# 接口测试
# ----------------------------------------------------------------------
def test_configure_risk_rule_success(client, risk_env):
    """配置风控规则成功：合法入参保存并返回配置信息（需求 13.1）。"""
    token = _login_token(
        client, risk_env["rc_user"]["username"], risk_env["rc_user"]["password"]
    )
    resp = client.put(
        _risk_rule_url(risk_env["shop_a_pk"]),
        headers=_auth_header(token),
        json={
            "session_reply_limit": 5,
            "shop_reply_limit": 100,
            "window_seconds": 60,
            "enabled": True,
        },
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is True
    rule = body["data"]
    assert rule["session_reply_limit"] == 5
    assert rule["shop_reply_limit"] == 100
    assert rule["window_seconds"] == 60
    assert rule["enabled"] is True


def test_configure_risk_rule_upsert_idempotent(client, risk_env):
    """风控规则按店铺 upsert 幂等：重复配置覆盖更新，仅单条记录（需求 13.1）。"""
    token = _login_token(
        client, risk_env["rc_user"]["username"], risk_env["rc_user"]["password"]
    )
    url = _risk_rule_url(risk_env["shop_a_pk"])
    first = client.put(
        url, headers=_auth_header(token), json={"session_reply_limit": 5}
    ).json()
    second = client.put(
        url, headers=_auth_header(token), json={"session_reply_limit": 8}
    ).json()
    # 同一店铺仅一条记录（ID 不变），内容为最后一次写入。
    assert first["data"]["id"] == second["data"]["id"]
    assert second["data"]["session_reply_limit"] == 8


def test_get_risk_rule_unconfigured_returns_null(client, risk_env):
    """查询未配置返回 data=null：便于前端区分未配置 / 已配置。"""
    token = _login_token(
        client, risk_env["rc_user"]["username"], risk_env["rc_user"]["password"]
    )
    resp = client.get(
        _risk_rule_url(risk_env["shop_a_pk"]), headers=_auth_header(token)
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is True
    assert body["data"] is None


def test_configure_risk_rule_negative_limit_rejected(db_session, risk_env):
    """入参校验失败：负数频率上限返回 success=false。"""
    # 走服务层验证负数被拒（路由层 Pydantic ge=0 亦会拦截）。
    resp = risk_control_service.configure_risk_rule(
        db_session,
        shop_pk=risk_env["shop_a_pk"],
        session_reply_limit=-1,
        operator_id=risk_env["rc_user"]["id"],
    )
    assert resp.success is False


def test_list_risk_types(client, risk_env):
    """风控类型枚举字典查询：返回 risk_type 中文文案（需求 13.4）。"""
    token = _login_token(
        client, risk_env["rc_user"]["username"], risk_env["rc_user"]["password"]
    )
    resp = client.get(RISK_TYPES_URL, headers=_auth_header(token))
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is True
    labels = {item["key"]: item["label"] for item in body["data"]}
    assert labels["frequency_limit"] == "频率限制"
    assert labels["reconnect_fail"] == "重连失败"


def test_risk_control_access_denied_for_unauthorized(client, risk_env):
    """无权限访问被拒：未授权用户调用返回「无访问权限」（需求 2.4）。"""
    token = _login_token(
        client,
        risk_env["other_user"]["username"],
        risk_env["other_user"]["password"],
    )
    resp = client.get(RISK_TYPES_URL, headers=_auth_header(token))
    body = resp.json()
    assert body["success"] is False
    assert body["code"] == CODE_FORBIDDEN
    assert body["message"] == MSG_FORBIDDEN


# ----------------------------------------------------------------------
# 服务层测试（不经 HTTP，直接验证数据范围隔离与持久化）
# ----------------------------------------------------------------------
def test_service_configure_non_owned_shop_denied(db_session, risk_env):
    """服务层：非管理员对非本人店铺配置风控被拒（数据范围隔离，需求 3.7）。"""
    resp = risk_control_service.configure_risk_rule(
        db_session,
        shop_pk=risk_env["shop_a_pk"],
        session_reply_limit=5,
        operator_id=risk_env["other_user"]["id"],
    )
    assert resp.success is False
    assert resp.code == CODE_FORBIDDEN


def test_service_configure_persists_record(db_session, risk_env):
    """服务层：配置风控规则后记录持久化（需求 13.1）。"""
    resp = risk_control_service.configure_risk_rule(
        db_session,
        shop_pk=risk_env["shop_a_pk"],
        session_reply_limit=3,
        shop_reply_limit=30,
        window_seconds=120,
        operator_id=risk_env["rc_user"]["id"],
    )
    assert resp.success is True
    record = db_session.get(RiskRule, resp.data["id"])
    assert record is not None
    assert record.session_reply_limit == 3
    assert record.window_seconds == 120

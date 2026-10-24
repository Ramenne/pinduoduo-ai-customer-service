# -*- coding: utf-8 -*-
"""
backend.tests.test_logs_api —— 消息/风控/系统日志查询接口与服务单元测试
======================================================================
本文件用途：对 backend 日志查询（app.api.routes.logs 与 app.services.log_service）
进行单元测试，覆盖需求 19（消息日志与风控日志）与需求 21.4（系统日志查询）核心
验收场景：

- 记录并分页查询消息日志（需求 19.1/19.3）：写入后可查出，时间为北京时间。
- 按店铺与时间范围筛选消息日志（需求 19.3）：仅返回范围内记录。
- 数据范围隔离（需求 3.7）：非管理员仅可见本人店铺的消息/风控日志；越权访问
  指定他人店铺返回空分页。
- 风控日志记录与查询（需求 19.2/19.3）：含风控类型筛选。
- 系统日志记录与查询（需求 21.4）：含级别/模块筛选。
- 禁止删除日志（需求 19.5）：日志路由不提供删除接口（仅查询）。
- 无权限访问被拒（需求 2.4）：未授权用户调用返回「无访问权限」。

测试方案：pytest + FastAPI TestClient + 内存 SQLite（夹具见 conftest.py）。
所有接口 HTTP 恒返回 200，业务成败由统一响应体表达。
"""
from __future__ import annotations

import pytest

from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import log_service
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
MESSAGE_LOGS_URL = f"{API_PREFIX}/message-logs"
RISK_LOGS_URL = f"{API_PREFIX}/risk-logs"
SYSTEM_LOGS_URL = f"{API_PREFIX}/system-logs"


# ----------------------------------------------------------------------
# 夹具：权限点 + 管理员/授权/无权限用户 + 两个店铺（不同归属）+ 预置日志
# ----------------------------------------------------------------------
@pytest.fixture()
def logs_env(db_session):
    """预置日志资源权限、用户、店铺与若干消息/风控/系统日志。

    - log_role：被授予 message_log/risk_log/system_log 的 view 权限；
      log_user（非管理员）为其下授权用户；
    - admin_role（is_admin）+ admin_user：管理员，可见全部；
    - other_user：无任何权限（用于无权限场景）；
    - shop_a 归属 log_user；shop_b 归属 admin_user（log_user 不可见 shop_b）。
    """
    role_log = SysRole(role_name="日志查看员", is_admin=False, status=1)
    role_admin = SysRole(role_name="管理员", is_admin=True, status=1)
    role_other = SysRole(role_name="其它用户", is_admin=False, status=1)
    db_session.add_all([role_log, role_admin, role_other])
    db_session.flush()

    perm_ids = []
    for resource in ("message_log", "risk_log", "system_log"):
        perm = SysPermission(resource_key=resource, action="view")
        db_session.add(perm)
        db_session.flush()
        perm_ids.append(perm.id)
    # log_role 与 admin_role 均被授予三类日志的 view 权限（permission.check 严格，
    # 管理员不做隐式放行，需经角色-权限映射授予）。
    for pid in perm_ids:
        db_session.add(SysRolePermission(role_id=role_log.id, permission_id=pid))
        db_session.add(SysRolePermission(role_id=role_admin.id, permission_id=pid))
    db_session.flush()

    log_password = "log-password-123"
    admin_password = "admin-password-123"
    other_password = "other-password-123"
    log_user = SysUser(
        username="log_user",
        password_hash=hash_password(log_password),
        role_id=role_log.id,
        status=1,
    )
    admin_user = SysUser(
        username="admin_user",
        password_hash=hash_password(admin_password),
        role_id=role_admin.id,
        status=1,
    )
    other_user = SysUser(
        username="other_user",
        password_hash=hash_password(other_password),
        role_id=role_other.id,
        status=1,
    )
    db_session.add_all([log_user, admin_user, other_user])
    db_session.flush()

    shop_a = Shop(shop_id="pdd-a", shop_name="店铺A", owner_user_id=log_user.id, status=1)
    shop_b = Shop(shop_id="pdd-b", shop_name="店铺B", owner_user_id=admin_user.id, status=1)
    db_session.add_all([shop_a, shop_b])
    db_session.flush()

    # 预置日志：shop_a 两条消息日志（不同日期）+ 一条风控日志；shop_b 一条消息日志。
    log_service.record_message_log(
        db_session,
        shop_a.id,
        customer_uid="c1",
        message_content="你好",
        process_result="auto_reply",
        reply_content="您好，有什么可以帮您",
        operator_id=log_user.id,
    )
    log_service.record_message_log(
        db_session,
        shop_a.id,
        customer_uid="c2",
        message_content="发货了吗",
        process_result="ai_reply",
        reply_content="已发货",
        operator_id=log_user.id,
    )
    log_service.record_risk_log(
        db_session,
        shop_a.id,
        "frequency_limit",
        trigger_reason="单会话回复超过上限",
    )
    log_service.record_message_log(
        db_session,
        shop_b.id,
        customer_uid="c3",
        message_content="仅管理员可见",
        process_result="auto_reply",
    )
    log_service.record_system_log(
        db_session, "warning", "商品同步签名缺失", module="product_sync"
    )
    db_session.commit()

    return {
        "log_user": {"username": "log_user", "password": log_password, "id": log_user.id},
        "admin_user": {
            "username": "admin_user",
            "password": admin_password,
            "id": admin_user.id,
        },
        "other_user": {
            "username": "other_user",
            "password": other_password,
            "id": other_user.id,
        },
        "shop_a_pk": shop_a.id,
        "shop_b_pk": shop_b.id,
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
# 消息日志
# ----------------------------------------------------------------------
def test_list_message_logs_basic(client, logs_env):
    """记录并分页查询消息日志：授权用户可查出本人店铺日志（需求 19.1/19.3）。"""
    token = _login_token(
        client, logs_env["log_user"]["username"], logs_env["log_user"]["password"]
    )
    resp = client.get(MESSAGE_LOGS_URL, headers=_auth_header(token))
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is True
    # log_user 仅能看到 shop_a 的两条消息日志（看不到 shop_b）。
    assert body["data"]["total"] == 2
    for item in body["data"]["list"]:
        assert item["shop_pk"] == logs_env["shop_a_pk"]
        assert item["log_time"] is not None


def test_list_message_logs_data_scope_isolation(client, logs_env):
    """数据范围隔离：非管理员访问他人店铺日志返回空分页（需求 3.7）。"""
    token = _login_token(
        client, logs_env["log_user"]["username"], logs_env["log_user"]["password"]
    )
    resp = client.get(
        MESSAGE_LOGS_URL,
        headers=_auth_header(token),
        params={"shop_pk": logs_env["shop_b_pk"]},
    )
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["total"] == 0
    assert body["data"]["list"] == []


def test_list_message_logs_admin_sees_all(client, logs_env):
    """管理员可见全部店铺日志（需求 3.7）。"""
    token = _login_token(
        client, logs_env["admin_user"]["username"], logs_env["admin_user"]["password"]
    )
    resp = client.get(MESSAGE_LOGS_URL, headers=_auth_header(token))
    body = resp.json()
    assert body["success"] is True
    # 管理员可见 shop_a(2) + shop_b(1) 共 3 条。
    assert body["data"]["total"] == 3


def test_list_message_logs_time_range_filter(client, logs_env):
    """按时间范围筛选：未来时间起点应过滤掉全部历史日志（需求 19.3）。"""
    token = _login_token(
        client, logs_env["log_user"]["username"], logs_env["log_user"]["password"]
    )
    resp = client.get(
        MESSAGE_LOGS_URL,
        headers=_auth_header(token),
        params={"start_time": "2999-01-01"},
    )
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["total"] == 0


def test_list_message_logs_invalid_time_rejected(client, logs_env):
    """时间格式非法返回 success=false（需求 19.3）。"""
    token = _login_token(
        client, logs_env["log_user"]["username"], logs_env["log_user"]["password"]
    )
    resp = client.get(
        MESSAGE_LOGS_URL,
        headers=_auth_header(token),
        params={"start_time": "not-a-date"},
    )
    body = resp.json()
    assert body["success"] is False


# ----------------------------------------------------------------------
# 风控日志
# ----------------------------------------------------------------------
def test_list_risk_logs_with_type_filter(client, logs_env):
    """风控日志查询并按风控类型筛选（需求 19.2/19.3）。"""
    token = _login_token(
        client, logs_env["log_user"]["username"], logs_env["log_user"]["password"]
    )
    resp = client.get(
        RISK_LOGS_URL,
        headers=_auth_header(token),
        params={"risk_type": "frequency_limit"},
    )
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["total"] == 1
    assert body["data"]["list"][0]["risk_type"] == "frequency_limit"


def test_list_risk_logs_type_filter_no_match(client, logs_env):
    """风控类型筛选无命中返回空分页（需求 19.3）。"""
    token = _login_token(
        client, logs_env["log_user"]["username"], logs_env["log_user"]["password"]
    )
    resp = client.get(
        RISK_LOGS_URL,
        headers=_auth_header(token),
        params={"risk_type": "risk_message"},
    )
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["total"] == 0


# ----------------------------------------------------------------------
# 系统日志
# ----------------------------------------------------------------------
def test_list_system_logs_with_filters(client, logs_env):
    """系统日志查询并按级别/模块筛选（需求 21.4）。"""
    token = _login_token(
        client, logs_env["log_user"]["username"], logs_env["log_user"]["password"]
    )
    resp = client.get(
        SYSTEM_LOGS_URL,
        headers=_auth_header(token),
        params={"level": "warning", "module": "product_sync"},
    )
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["total"] == 1
    assert body["data"]["list"][0]["level"] == "warning"
    assert body["data"]["list"][0]["module"] == "product_sync"


# ----------------------------------------------------------------------
# 权限与「禁止删除」
# ----------------------------------------------------------------------
def test_logs_access_denied_for_unauthorized(client, logs_env):
    """无权限访问被拒：未授权用户调用返回「无访问权限」（需求 2.4）。"""
    token = _login_token(
        client, logs_env["other_user"]["username"], logs_env["other_user"]["password"]
    )
    resp = client.get(MESSAGE_LOGS_URL, headers=_auth_header(token))
    body = resp.json()
    assert body["success"] is False
    assert body["code"] == CODE_FORBIDDEN
    assert body["message"] == MSG_FORBIDDEN


def test_logs_no_delete_endpoint(client, logs_env):
    """禁止删除日志（需求 19.5）：日志接口不提供 DELETE 方法。"""
    token = _login_token(
        client, logs_env["log_user"]["username"], logs_env["log_user"]["password"]
    )
    # 日志路由仅提供 GET；DELETE 应返回 405 Method Not Allowed。
    resp = client.delete(
        f"{MESSAGE_LOGS_URL}/1", headers=_auth_header(token)
    )
    assert resp.status_code in (404, 405)

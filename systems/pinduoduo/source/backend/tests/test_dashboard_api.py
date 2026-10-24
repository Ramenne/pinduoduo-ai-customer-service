# -*- coding: utf-8 -*-
"""
backend.tests.test_dashboard_api —— 仪表盘与数据分析接口/服务单元测试
==================================================================
本文件用途：对 backend 仪表盘与数据分析（app.api.routes.dashboard 与
app.services.dashboard_service）进行单元测试，覆盖需求 20 核心验收场景：

- 关键指标统计（需求 20.1）：在线店铺数、今日消息数、今日自动回复数、AI 回复数、
  风控触发数，且按北京时间口径仅统计今日数据（需求 20.3）。
- 趋势分析（需求 20.2）：指定时间范围内按天聚合消息量与回复量，无数据日期补 0。
- 数据范围隔离（需求 3.7）：非管理员仅统计本人 / 被授权店铺数据。
- 无权限访问被拒（需求 2.4）：未授权用户调用返回「无访问权限」。

测试方案：pytest + FastAPI TestClient + 内存 SQLite（夹具见 conftest.py）。
所有接口 HTTP 恒返回 200，业务成败由统一响应体表达。
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import dashboard_service
from common.models.log_models import MessageLog, RiskLog
from common.models.shop_models import Account, Shop
from common.models.user_models import (
    SysPermission,
    SysRole,
    SysRolePermission,
    SysUser,
)
from common.utils.security import hash_password
from common.utils.time_utils import now_beijing_naive

API_PREFIX = "/api/v1"
LOGIN_URL = f"{API_PREFIX}/login"
OVERVIEW_URL = f"{API_PREFIX}/dashboard/overview"
TREND_URL = f"{API_PREFIX}/dashboard/trend"


@pytest.fixture()
def dash_env(db_session):
    """预置 dashboard 资源权限、用户、店铺、账号与日志数据。

    构造：
    - dash 角色：被授予 dashboard 资源 view 权限；dash_user 为其下授权用户；
    - other_user：无权限用户（用于无权限场景）；
    - dash_user 名下店铺 shop_a（启用）+ 停用店铺 shop_a2 + other_user 名下店铺 shop_b（启用）；
    - 今日与昨日的消息日志、风控日志，用于验证今日口径与数据范围隔离。
    """
    role_dash = SysRole(role_name="仪表盘用户", is_admin=False, status=1)
    role_other = SysRole(role_name="其它用户", is_admin=False, status=1)
    db_session.add_all([role_dash, role_other])
    db_session.flush()

    perm = SysPermission(resource_key="dashboard", action="view")
    db_session.add(perm)
    db_session.flush()
    db_session.add(SysRolePermission(role_id=role_dash.id, permission_id=perm.id))
    db_session.flush()

    dash_password = "dash-password-123"
    other_password = "other-password-123"
    dash_user = SysUser(
        username="dash_user",
        password_hash=hash_password(dash_password),
        role_id=role_dash.id,
        status=1,
    )
    other_user = SysUser(
        username="other_user",
        password_hash=hash_password(other_password),
        role_id=role_other.id,
        status=1,
    )
    db_session.add_all([dash_user, other_user])
    db_session.flush()

    shop_a = Shop(shop_id="pdd-a", shop_name="店铺A", owner_user_id=dash_user.id, status=1)
    shop_b = Shop(shop_id="pdd-b", shop_name="店铺B", owner_user_id=other_user.id, status=1)
    # dash_user 名下的停用店铺（status=0）：不应计入「启用店铺数」。
    shop_a_disabled = Shop(
        shop_id="pdd-a2", shop_name="店铺A2(停用)", owner_user_id=dash_user.id, status=0
    )
    db_session.add_all([shop_a, shop_b, shop_a_disabled])
    db_session.flush()

    # 两启用店铺各一个在线账号（停用店铺不再关心账号登录态）。
    db_session.add_all(
        [
            Account(shop_pk=shop_a.id, user_id=dash_user.id, login_state="online"),
            Account(shop_pk=shop_b.id, user_id=other_user.id, login_state="online"),
        ]
    )

    today = now_beijing_naive().replace(hour=12, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)

    # shop_a 今日：2 条消息（1 自动回复 + 1 AI 回复），昨日 1 条（不计入今日）。
    db_session.add_all(
        [
            MessageLog(shop_pk=shop_a.id, process_result="auto_reply", log_time=today),
            MessageLog(shop_pk=shop_a.id, process_result="ai_reply", log_time=today),
            MessageLog(shop_pk=shop_a.id, process_result="auto_reply", log_time=yesterday),
        ]
    )
    # shop_b 今日：1 条自动回复（属于 other_user，不应被 dash_user 统计到）。
    db_session.add(
        MessageLog(shop_pk=shop_b.id, process_result="auto_reply", log_time=today)
    )
    # 风控日志：shop_a 今日 1 条。
    db_session.add(
        RiskLog(shop_pk=shop_a.id, risk_type="frequency_limit", log_time=today)
    )
    db_session.flush()
    db_session.commit()

    return {
        "dash_user": {"username": "dash_user", "password": dash_password, "id": dash_user.id},
        "other_user": {"username": "other_user", "password": other_password, "id": other_user.id},
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


def test_overview_metrics_scoped_to_user(client, dash_env):
    """关键指标仅统计本人店铺今日数据（需求 20.1/20.3/3.7）。"""
    token = _login_token(
        client, dash_env["dash_user"]["username"], dash_env["dash_user"]["password"]
    )
    resp = client.get(OVERVIEW_URL, headers=_auth_header(token))
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is True
    data = body["data"]
    # dash_user 仅可见本人店铺：启用 1 个（shop_a），停用的 shop_a2 不计入。
    assert data["online_shops"] == 1
    # 今日消息 2 条（昨日 1 条不计入），其中自动回复 1、AI 回复 1。
    assert data["today_messages"] == 2
    assert data["today_auto_replies"] == 1
    assert data["today_ai_replies"] == 1
    # 风控触发今日 1 条。
    assert data["today_risk_triggers"] == 1


def test_trend_aggregates_by_day(db_session, dash_env):
    """趋势按天聚合消息量与回复量，无数据日期补 0（需求 20.2/20.3）。"""
    dash_user = db_session.get(SysUser, dash_env["dash_user"]["id"])
    today_key = now_beijing_naive().strftime("%Y-%m-%d")
    yesterday_key = (now_beijing_naive() - timedelta(days=1)).strftime("%Y-%m-%d")

    resp = dashboard_service.get_trend(
        db_session,
        dash_user,
        start_date=yesterday_key,
        end_date=today_key,
    )
    assert resp.success is True
    points = {p["date"]: p for p in resp.data["points"]}
    # 含两天数据点。
    assert set(points.keys()) == {yesterday_key, today_key}
    # 今日：2 条消息，2 条回复（自动+AI）。
    assert points[today_key]["messages"] == 2
    assert points[today_key]["replies"] == 2
    # 昨日：1 条消息（自动回复），1 条回复。
    assert points[yesterday_key]["messages"] == 1
    assert points[yesterday_key]["replies"] == 1


def test_trend_invalid_date_rejected(db_session, dash_env):
    """趋势查询非法日期格式返回 success=false。"""
    dash_user = db_session.get(SysUser, dash_env["dash_user"]["id"])
    resp = dashboard_service.get_trend(
        db_session, dash_user, start_date="2024/01/01", end_date=None
    )
    assert resp.success is False


def test_dashboard_access_denied_for_unauthorized(client, dash_env):
    """无权限访问被拒：未授权用户返回「无访问权限」（需求 2.4）。"""
    token = _login_token(
        client, dash_env["other_user"]["username"], dash_env["other_user"]["password"]
    )
    resp = client.get(OVERVIEW_URL, headers=_auth_header(token))
    body = resp.json()
    assert body["success"] is False
    assert body["code"] == CODE_FORBIDDEN
    assert body["message"] == MSG_FORBIDDEN


def test_enabled_count_excludes_merged_shop_records(client, db_session, dash_env):
    actor = dash_env['dash_user']
    db_session.add(Shop(shop_id='merged-old', shop_name='旧记录', owner_user_id=actor['id'], status=1,
                        merged_into_shop_pk=dash_env['shop_a_pk']))
    db_session.commit()
    token = _login_token(client, actor['username'], actor['password'])
    body = client.get(OVERVIEW_URL, headers=_auth_header(token)).json()
    assert body['success']
    assert body['data']['enabled_shops'] == 1
    assert body['data']['online_shops'] == 1  # legacy field retained without changing its meaning

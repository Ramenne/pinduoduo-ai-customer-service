# -*- coding: utf-8 -*-
"""
backend.tests.test_filter_blacklist_api —— 消息过滤与黑名单接口/服务单元测试
==========================================================================
本文件用途：对 backend 消息过滤与黑名单（app.api.routes.message_filters /
app.api.routes.blacklist 与 app.services.filter_service）进行单元测试，覆盖
需求 12（消息过滤与黑名单）核心验收场景：

- 创建过滤规则成功（需求 12.1）：合法入参创建并返回规则信息。
- 创建过滤规则校验失败：非法条件类型 / 空条件值返回 success=false。
- 过滤规则列表后端分页（需求 12.6）：返回 {list, total, page, page_size} 结构。
- 过滤规则停用（逻辑删除，规范 11）：停用经 enabled=False 标记，记录保留。
- 黑名单加入幂等（需求 12.3）：重复加入不新建重复记录。
- 黑名单移出逻辑失效（需求 12.5）：移出经 is_active=False 标记，记录保留、
  总数不变，禁止物理删除。
- 黑名单列表后端分页（需求 12.6）。
- 数据范围隔离（需求 3.7）：非管理员仅可操作 / 查看本人店铺数据，越权被拒。
- 无权限访问被拒（需求 2.4）：未授权用户调用返回「无访问权限」。

测试方案：pytest + FastAPI TestClient + 内存 SQLite（夹具见 conftest.py）。
本模块为 message_filter / blacklist 资源补充权限点与授权用户，并预置店铺与
过滤条件字典。所有接口 HTTP 恒返回 200，业务成败由统一响应体表达。
"""
from __future__ import annotations

import pytest

from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import filter_service
from common.models.config_models import Blacklist
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
FILTERS_URL = f"{API_PREFIX}/message-filters"
BLACKLIST_URL = f"{API_PREFIX}/blacklist"


# ----------------------------------------------------------------------
# 夹具：权限点 + 授权 / 无权限用户 + 店铺 + 过滤条件字典
# ----------------------------------------------------------------------
@pytest.fixture()
def filter_env(db_session):
    """预置 message_filter / blacklist 资源权限、用户、店铺与过滤条件字典。

    构造：
    - mf 角色：被授予 message_filter 与 blacklist 资源的 view/create/update/
      disable 权限；mf_user 为其下授权用户（非管理员）；
    - 另一普通用户 other_user（无任何权限，用于越权场景）；
    - mf_user 名下店铺 shop_a（owner=mf_user），用于数据范围隔离；
    - 过滤条件字典 filter_condition: contains/regex/msg_type。
    """
    role_mf = SysRole(role_name="过滤管理员", is_admin=False, status=1)
    role_other = SysRole(role_name="其它用户", is_admin=False, status=1)
    db_session.add_all([role_mf, role_other])
    db_session.flush()

    perm_ids = []
    for resource in ("shop",):
        for action in ("view", "create", "update", "disable"):
            perm = SysPermission(resource_key=resource, action=action)
            db_session.add(perm)
            db_session.flush()
            perm_ids.append(perm.id)
    for pid in perm_ids:
        db_session.add(SysRolePermission(role_id=role_mf.id, permission_id=pid))
    db_session.flush()

    mf_password = "mf-password-123"
    other_password = "other-password-123"
    mf_user = SysUser(
        username="mf_user",
        password_hash=hash_password(mf_password),
        role_id=role_mf.id,
        status=1,
    )
    other_user = SysUser(
        username="other_user",
        password_hash=hash_password(other_password),
        role_id=role_other.id,
        status=1,
    )
    db_session.add_all([mf_user, other_user])
    db_session.flush()

    # mf_user 名下店铺（数据范围隔离用）。
    shop_a = Shop(
        shop_id="pdd-shop-a",
        shop_name="店铺A",
        owner_user_id=mf_user.id,
        status=1,
    )
    db_session.add(shop_a)
    db_session.flush()

    # 过滤条件字典（服务层创建时校验合法性）。
    for idx, key in enumerate(("contains", "regex", "msg_type"), start=1):
        db_session.add(
            SysDict(
                dict_type="filter_condition",
                dict_key=key,
                dict_label=key,
                order_no=idx,
                enabled=True,
            )
        )
    db_session.flush()
    db_session.commit()

    return {
        "mf_user": {"username": "mf_user", "password": mf_password, "id": mf_user.id},
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
# 过滤规则接口测试
# ----------------------------------------------------------------------
def test_create_filter_rule_success(client, filter_env):
    """创建过滤规则成功：合法入参创建并返回规则信息（需求 12.1）。"""
    token = _login_token(
        client, filter_env["mf_user"]["username"], filter_env["mf_user"]["password"]
    )
    resp = client.post(
        FILTERS_URL,
        headers=_auth_header(token),
        json={
            "shop_pk": filter_env["shop_a_pk"],
            "condition_type": "contains",
            "condition_value": "广告",
        },
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is True
    rule = body["data"]
    assert rule["condition_type"] == "contains"
    assert rule["condition_value"] == "广告"
    assert rule["enabled"] is True


def test_create_filter_rule_invalid_condition_type(client, filter_env):
    """创建校验失败：非法条件类型返回 success=false（需求 12.1）。"""
    token = _login_token(
        client, filter_env["mf_user"]["username"], filter_env["mf_user"]["password"]
    )
    resp = client.post(
        FILTERS_URL,
        headers=_auth_header(token),
        json={
            "shop_pk": filter_env["shop_a_pk"],
            "condition_type": "unknown",
            "condition_value": "x",
        },
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is False
    assert body["data"] is None


def test_list_filter_rules_pagination(client, filter_env):
    """过滤规则列表后端分页：返回分页结构（需求 12.6）。"""
    token = _login_token(
        client, filter_env["mf_user"]["username"], filter_env["mf_user"]["password"]
    )
    for idx in range(3):
        client.post(
            FILTERS_URL,
            headers=_auth_header(token),
            json={
                "shop_pk": filter_env["shop_a_pk"],
                "condition_type": "contains",
                "condition_value": f"word-{idx}",
            },
        )
    resp = client.get(
        FILTERS_URL,
        headers=_auth_header(token),
        params={"page": 1, "page_size": 20, "shop_pk": filter_env["shop_a_pk"]},
    )
    body = resp.json()
    assert resp.status_code == 200
    data = body["data"]
    assert set(["list", "total", "page", "page_size"]).issubset(data.keys())
    assert data["total"] == 3
    assert data["page_size"] == 20


def test_disable_filter_rule_is_logical(client, filter_env):
    """过滤规则停用：经 enabled=False 逻辑标记，记录保留（规范 11）。"""
    token = _login_token(
        client, filter_env["mf_user"]["username"], filter_env["mf_user"]["password"]
    )
    create = client.post(
        FILTERS_URL,
        headers=_auth_header(token),
        json={
            "shop_pk": filter_env["shop_a_pk"],
            "condition_type": "regex",
            "condition_value": "^spam",
        },
    ).json()
    rule_id = create["data"]["id"]

    resp = client.put(
        f"{FILTERS_URL}/{rule_id}/status",
        headers=_auth_header(token),
        json={"enabled": False},
    )
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["enabled"] is False

    # 停用后记录仍存在（按 enabled=False 筛选可查到）。
    listed = client.get(
        FILTERS_URL,
        headers=_auth_header(token),
        params={"shop_pk": filter_env["shop_a_pk"], "enabled": False},
    ).json()
    assert listed["data"]["total"] == 1


def test_filter_access_denied_for_unauthorized(client, filter_env):
    """无权限访问被拒：未授权用户调用返回「无访问权限」（需求 2.4）。"""
    token = _login_token(
        client,
        filter_env["other_user"]["username"],
        filter_env["other_user"]["password"],
    )
    resp = client.get(FILTERS_URL, headers=_auth_header(token))
    body = resp.json()
    assert body["success"] is False
    assert body["code"] == CODE_FORBIDDEN
    assert body["message"] == MSG_FORBIDDEN


# ----------------------------------------------------------------------
# 黑名单接口测试
# ----------------------------------------------------------------------
def test_add_to_blacklist_idempotent(client, filter_env):
    """黑名单加入幂等：重复加入不新建重复记录（需求 12.3）。"""
    token = _login_token(
        client, filter_env["mf_user"]["username"], filter_env["mf_user"]["password"]
    )
    payload = {"shop_pk": filter_env["shop_a_pk"], "customer_uid": "cust-001"}
    first = client.post(BLACKLIST_URL, headers=_auth_header(token), json=payload).json()
    second = client.post(BLACKLIST_URL, headers=_auth_header(token), json=payload).json()
    assert first["success"] is True
    assert second["success"] is True
    # 两次加入返回同一记录 ID（幂等）。
    assert first["data"]["id"] == second["data"]["id"]

    listed = client.get(
        BLACKLIST_URL,
        headers=_auth_header(token),
        params={"shop_pk": filter_env["shop_a_pk"]},
    ).json()
    assert listed["data"]["total"] == 1


def test_remove_from_blacklist_logical(client, filter_env, db_session):
    """黑名单移出逻辑失效：经 is_active=False 标记，记录保留、总数不变（需求 12.5）。"""
    token = _login_token(
        client, filter_env["mf_user"]["username"], filter_env["mf_user"]["password"]
    )
    add = client.post(
        BLACKLIST_URL,
        headers=_auth_header(token),
        json={"shop_pk": filter_env["shop_a_pk"], "customer_uid": "cust-002"},
    ).json()
    blacklist_id = add["data"]["id"]

    resp = client.put(
        f"{BLACKLIST_URL}/{blacklist_id}/remove", headers=_auth_header(token)
    )
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["is_active"] is False

    # 记录未被物理删除：仍可在数据库中查到（is_active=False）。
    item = db_session.get(Blacklist, blacklist_id)
    assert item is not None
    assert bool(item.is_active) is False

    # 总数不变（含失效记录）。
    listed = client.get(
        BLACKLIST_URL,
        headers=_auth_header(token),
        params={"shop_pk": filter_env["shop_a_pk"]},
    ).json()
    assert listed["data"]["total"] == 1


def test_blacklist_data_scope_isolation(client, filter_env):
    """数据范围隔离：非管理员对非本人店铺加入黑名单被拒（需求 3.7）。"""
    token = _login_token(
        client,
        filter_env["other_user"]["username"],
        filter_env["other_user"]["password"],
    )
    # other_user 无 blacklist 权限，先验证权限拦截即可（无权限优先于范围）。
    resp = client.post(
        BLACKLIST_URL,
        headers=_auth_header(token),
        json={"shop_pk": filter_env["shop_a_pk"], "customer_uid": "x"},
    )
    body = resp.json()
    assert body["success"] is False
    assert body["code"] == CODE_FORBIDDEN


# ----------------------------------------------------------------------
# 服务层测试（不经 HTTP，直接验证数据范围隔离与逻辑失效）
# ----------------------------------------------------------------------
def test_service_add_to_non_owned_shop_denied(db_session, filter_env):
    """服务层：非管理员对非本人店铺加入黑名单被拒（数据范围隔离，需求 3.7）。"""
    # other_user 操作 mf_user 名下店铺，应被数据范围隔离拦截。
    resp = filter_service.add_to_blacklist(
        db_session,
        shop_pk=filter_env["shop_a_pk"],
        customer_uid="cust-x",
        operator_id=filter_env["other_user"]["id"],
    )
    assert resp.success is False
    assert resp.code == CODE_FORBIDDEN


def test_service_remove_keeps_record(db_session, filter_env):
    """服务层：移出黑名单后记录保留（逻辑失效，需求 12.5）。"""
    add = filter_service.add_to_blacklist(
        db_session,
        shop_pk=filter_env["shop_a_pk"],
        customer_uid="cust-keep",
        operator_id=filter_env["mf_user"]["id"],
    )
    blacklist_id = add.data["id"]

    filter_service.remove_from_blacklist(
        db_session, blacklist_id=blacklist_id, operator_id=filter_env["mf_user"]["id"]
    )
    item = db_session.get(Blacklist, blacklist_id)
    assert item is not None
    assert bool(item.is_active) is False

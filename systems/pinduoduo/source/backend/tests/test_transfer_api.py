# -*- coding: utf-8 -*-
"""
backend.tests.test_transfer_api —— 转人工设置接口单元测试（需求 16.1）
====================================================================
本文件用途：对 backend「转人工设置」接口（app.api.routes.transfer）进行单元测试，
覆盖需求 16.1 的核心验收场景：

- 客服列表查询：经 websocket 取客服列表成功返回；websocket 不可达时降级返回空列表
  并附中文提示，不整体失败（健壮性兜底）。
- 转人工关键词新增 / 列表（后端分页）/ 启停用：基本 CRUD 与幂等去重。
- 无权限访问被拒（需求 2.4）。

测试方案：pytest + FastAPI TestClient + 内存 SQLite（夹具见 conftest.py）。
对 websocket 客服列表查询（transfer_client.fetch_cs_list）做 monkeypatch，避免真实
HTTP 调用。所有接口 HTTP 恒返回 200。
"""
from __future__ import annotations

import pytest

from app.services import transfer_client, transfer_service
from common.models.config_models import TransferKeyword
from common.models.shop_models import Shop
from common.models.user_models import SysPermission, SysRolePermission

API_PREFIX = "/api/v1"
LOGIN_URL = f"{API_PREFIX}/login"
KEYWORDS_URL = f"{API_PREFIX}/transfer-keywords"


def _login_token(client, username: str, password: str) -> str:
    """登录并返回访问令牌（断言登录成功）。"""
    resp = client.post(LOGIN_URL, json={"username": username, "password": password})
    body = resp.json()
    assert body["success"] is True, body
    return body["data"]["token"]


def _auth_header(token: str) -> dict:
    """构造 Bearer 鉴权请求头。"""
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def transfer_env(db_session, seed_permissions, test_users):
    """预置 shop 资源权限点、授权用户与一家归属店铺。"""
    manager_id = test_users["manager"]["id"]
    role_manager_id = seed_permissions["role_manager_id"]

    # 追加 shop 资源权限点并授予 user_manager 角色（转人工等店铺级设置统一用 shop 判权）。
    for action in ("view", "create", "update", "disable"):
        perm = SysPermission(resource_key="shop", action=action)
        db_session.add(perm)
        db_session.flush()
        db_session.add(
            SysRolePermission(role_id=role_manager_id, permission_id=perm.id)
        )

    shop = Shop(shop_id="pdd_a", shop_name="店铺A", owner_user_id=manager_id, status=1)
    db_session.add(shop)
    db_session.flush()
    db_session.commit()

    return {"shop_pk": shop.id, "manager_id": manager_id}


def test_cs_list_success(client, test_users, transfer_env, monkeypatch):
    """客服列表查询成功：返回 websocket 取回的客服列表（需求 16.1）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    shop_pk = transfer_env["shop_pk"]

    def fake_fetch(shop_id, owner_user_id):
        return transfer_client.CsListResult(
            ok=True, cs_list=[{"cs_uid": "cs1", "cs_name": "客服一"}]
        )

    monkeypatch.setattr(transfer_client, "fetch_cs_list", fake_fetch)

    resp = client.get(
        f"{API_PREFIX}/shops/{shop_pk}/cs-list", headers=_auth_header(token)
    ).json()
    assert resp["success"] is True
    assert resp["data"]["list"] == [{"cs_uid": "cs1", "cs_name": "客服一"}]
    assert resp["data"]["message"] == ""


def test_cs_list_degrade_when_unavailable(client, test_users, transfer_env, monkeypatch):
    """websocket 不可达时降级：返回空客服列表并附中文提示，不整体失败（需求 26）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    shop_pk = transfer_env["shop_pk"]

    def fake_fetch(shop_id, owner_user_id):
        return transfer_client.CsListResult(ok=False, message="客服列表服务暂不可用")

    monkeypatch.setattr(transfer_client, "fetch_cs_list", fake_fetch)

    resp = client.get(
        f"{API_PREFIX}/shops/{shop_pk}/cs-list", headers=_auth_header(token)
    ).json()
    assert resp["success"] is True
    assert resp["data"]["list"] == []
    assert "暂不可用" in resp["data"]["message"]


def test_create_and_list_transfer_keyword(client, test_users, transfer_env):
    """新增转人工关键词并分页查询：列表含新增项（需求 16.1）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    shop_pk = transfer_env["shop_pk"]

    created = client.post(
        KEYWORDS_URL,
        headers=_auth_header(token),
        json={"shop_pk": shop_pk, "keyword": "人工", "enabled": True},
    ).json()
    assert created["success"] is True
    assert created["data"]["keyword"] == "人工"

    listed = client.get(
        KEYWORDS_URL,
        headers=_auth_header(token),
        params={"shop_pk": shop_pk, "page": 1, "page_size": 20},
    ).json()
    assert listed["success"] is True
    assert listed["data"]["total"] == 1
    assert listed["data"]["list"][0]["keyword"] == "人工"


def test_create_transfer_keyword_idempotent(client, test_users, transfer_env, db_session):
    """同店铺同关键词去重：重复新增复用原记录，不重复入库（需求 16.1）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    shop_pk = transfer_env["shop_pk"]

    for _ in range(2):
        client.post(
            KEYWORDS_URL,
            headers=_auth_header(token),
            json={"shop_pk": shop_pk, "keyword": "退款", "enabled": True},
        )

    rows = db_session.query(TransferKeyword).filter_by(shop_pk=shop_pk, keyword="退款").all()
    assert len(rows) == 1


def test_set_transfer_keyword_status(client, test_users, transfer_env, db_session):
    """启停用转人工关键词：状态落库（需求 16.1）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    shop_pk = transfer_env["shop_pk"]

    created = client.post(
        KEYWORDS_URL,
        headers=_auth_header(token),
        json={"shop_pk": shop_pk, "keyword": "投诉", "enabled": True},
    ).json()
    keyword_id = created["data"]["id"]

    resp = client.put(
        f"{KEYWORDS_URL}/{keyword_id}/status",
        headers=_auth_header(token),
        json={"enabled": False},
    ).json()
    assert resp["success"] is True
    assert resp["data"]["enabled"] is False
    assert bool(db_session.get(TransferKeyword, keyword_id).enabled) is False


def test_transfer_requires_permission(client, test_users, transfer_env):
    """无权限用户访问转人工接口被拒（需求 2.4）。"""
    guest = test_users["guest"]
    token = _login_token(client, guest["username"], guest["password"])
    shop_pk = transfer_env["shop_pk"]

    resp = client.get(
        KEYWORDS_URL,
        headers=_auth_header(token),
        params={"shop_pk": shop_pk},
    ).json()
    assert resp["success"] is False

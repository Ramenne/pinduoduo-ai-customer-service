# -*- coding: utf-8 -*-
"""
backend.tests.test_replies_api —— 默认回复与商品专属回复接口单元测试
====================================================================
本文件用途：对 backend「默认回复 / 商品专属回复」接口（app.api.routes.replies）
进行单元测试，覆盖需求 7 的核心验收场景：

- 保存默认回复（upsert 幂等）：同店铺重复保存仅一条配置、内容为最后一次写入
  （需求 7.1）。
- 默认回复内容为空校验失败。
- 新增商品专属回复（绑定 goods_id，upsert 幂等）：同 (shop_pk, goods_id) 多次
  写入仅一条（需求 7.3）。
- 商品专属回复后端分页列表（需求 7.5）。
- 逻辑删除商品专属回复：删除后 enabled=False、记录不被物理删除（需求 24.6）。
- 数据范围隔离：非管理员不可操作他人店铺下的回复配置（需求 3.7）。
- 无权限访问被拒（需求 2.4）。

测试方案：pytest + FastAPI TestClient + 内存 SQLite（夹具见 conftest.py），并在
本文件追加「reply 权限点 + 店铺」预置夹具。所有接口 HTTP 恒返回 200。
"""
from __future__ import annotations

import pytest

from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from common.models.reply_models import GoodsReply
from common.models.shop_models import Shop
from common.models.user_models import SysPermission, SysRolePermission, SysUser
from common.utils.security import hash_password

API_PREFIX = "/api/v1"
LOGIN_URL = f"{API_PREFIX}/login"
DEFAULT_REPLY_URL = f"{API_PREFIX}/default-replies"
GOODS_REPLY_URL = f"{API_PREFIX}/goods-replies"


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
def reply_env(db_session, seed_permissions, test_users):
    """预置 shop 资源权限点、授权用户与店铺。

    - 为 user_manager 角色追加 shop 资源的 view/create/update/disable 权限，
      使 manager 用户可访问回复接口（店铺级设置统一用 shop 判权）；
    - 创建两个店铺：shop_a 归属 manager（id），shop_b 归属其他用户，用于校验
      数据范围隔离。
    """
    manager_id = test_users["manager"]["id"]
    role_manager_id = seed_permissions["role_manager_id"]

    # 追加 shop 资源权限点并授予 user_manager 角色（默认/商品回复等店铺级设置统一用 shop 判权）。
    for action in ("view", "create", "update", "disable"):
        perm = SysPermission(resource_key="shop", action=action)
        db_session.add(perm)
        db_session.flush()
        db_session.add(
            SysRolePermission(role_id=role_manager_id, permission_id=perm.id)
        )

    # 创建店铺：shop_a 归属 manager，shop_b 归属他人。
    shop_a = Shop(shop_id="pdd_a", shop_name="店铺A", owner_user_id=manager_id, status=1)
    shop_b = Shop(shop_id="pdd_b", shop_name="店铺B", owner_user_id=999999, status=1)
    db_session.add_all([shop_a, shop_b])
    db_session.flush()
    db_session.commit()

    return {"shop_a_pk": shop_a.id, "shop_b_pk": shop_b.id, "manager_id": manager_id}


def test_save_default_reply_upsert_idempotent(client, test_users, reply_env):
    """保存默认回复 upsert 幂等：同店铺重复保存仅一条、内容为最后一次（需求 7.1）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    shop_pk = reply_env["shop_a_pk"]

    # 首次保存。
    r1 = client.put(
        DEFAULT_REPLY_URL,
        headers=_auth_header(token),
        json={"shop_pk": shop_pk, "content": "您好，有什么可以帮您？", "enabled": True},
    ).json()
    assert r1["success"] is True
    first_id = r1["data"]["id"]

    # 再次保存（内容更新）。
    r2 = client.put(
        DEFAULT_REPLY_URL,
        headers=_auth_header(token),
        json={"shop_pk": shop_pk, "content": "稍后回复", "enabled": True},
    ).json()
    assert r2["success"] is True
    # upsert 幂等：主键不变、内容为最后一次写入。
    assert r2["data"]["id"] == first_id
    assert r2["data"]["content"] == "稍后回复"

    # 查询确认。
    got = client.get(
        DEFAULT_REPLY_URL, headers=_auth_header(token), params={"shop_pk": shop_pk}
    ).json()
    assert got["data"]["content"] == "稍后回复"


def test_save_default_reply_persists_reply_once(client, test_users, reply_env):
    """保存默认回复时「只回复一次」开关持久化并可回读（需求 7.1）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    shop_pk = reply_env["shop_a_pk"]

    # 开启只回复一次保存。
    saved = client.put(
        DEFAULT_REPLY_URL,
        headers=_auth_header(token),
        json={
            "shop_pk": shop_pk,
            "content": "您好，有什么可以帮您？",
            "enabled": True,
            "reply_once": True,
        },
    ).json()
    assert saved["success"] is True
    assert saved["data"]["reply_once"] is True

    # 查询回读确认持久化。
    got = client.get(
        DEFAULT_REPLY_URL, headers=_auth_header(token), params={"shop_pk": shop_pk}
    ).json()
    assert got["data"]["reply_once"] is True

    # 不传 reply_once 时默认为 False（关闭只回复一次）。
    saved2 = client.put(
        DEFAULT_REPLY_URL,
        headers=_auth_header(token),
        json={"shop_pk": shop_pk, "content": "稍后回复", "enabled": True},
    ).json()
    assert saved2["data"]["reply_once"] is False


def test_save_default_reply_empty_content_fails(client, test_users, reply_env):
    """默认回复内容为空：返回业务失败（需求 7.1 配套校验）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    shop_pk = reply_env["shop_a_pk"]

    resp = client.put(
        DEFAULT_REPLY_URL,
        headers=_auth_header(token),
        json={"shop_pk": shop_pk, "content": "   ", "enabled": True},
    ).json()
    assert resp["success"] is False
    assert resp["data"] is None


def test_create_goods_reply_upsert_idempotent(client, test_users, reply_env):
    """新增商品专属回复 upsert 幂等：同 (shop_pk, goods_id) 多次写入仅一条（需求 7.3）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    shop_pk = reply_env["shop_a_pk"]

    payload = {
        "shop_pk": shop_pk,
        "goods_id": "G1001",
        "reply_content": "该商品现货充足",
        "reply_type": "text",
        "enabled": True,
    }
    r1 = client.post(GOODS_REPLY_URL, headers=_auth_header(token), json=payload).json()
    assert r1["success"] is True
    first_id = r1["data"]["id"]

    payload["reply_content"] = "该商品已售罄"
    r2 = client.post(GOODS_REPLY_URL, headers=_auth_header(token), json=payload).json()
    assert r2["success"] is True
    assert r2["data"]["id"] == first_id
    assert r2["data"]["reply_content"] == "该商品已售罄"

    # 列表仅一条。
    listed = client.get(
        GOODS_REPLY_URL, headers=_auth_header(token), params={"shop_pk": shop_pk}
    ).json()
    assert listed["data"]["total"] == 1


def test_list_goods_replies_pagination(client, test_users, reply_env):
    """商品专属回复后端分页：返回 {list,total,page,page_size} 且分页约束生效（需求 7.5）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    shop_pk = reply_env["shop_a_pk"]

    # 新增 3 条不同商品的专属回复。
    for i in range(3):
        client.post(
            GOODS_REPLY_URL,
            headers=_auth_header(token),
            json={
                "shop_pk": shop_pk,
                "goods_id": f"G{i}",
                "reply_content": f"回复{i}",
            },
        )

    resp = client.get(
        GOODS_REPLY_URL,
        headers=_auth_header(token),
        params={"shop_pk": shop_pk, "page": 1, "page_size": 10},
    ).json()
    data = resp["data"]
    assert data["total"] == 3
    assert data["page"] == 1
    assert data["page_size"] == 10
    assert len(data["list"]) == 3


def test_delete_goods_reply_is_logical(client, test_users, reply_env, db_session):
    """逻辑删除商品专属回复：删除后 enabled=False、记录仍存在（需求 24.6）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    shop_pk = reply_env["shop_a_pk"]

    created = client.post(
        GOODS_REPLY_URL,
        headers=_auth_header(token),
        json={"shop_pk": shop_pk, "goods_id": "G_DEL", "reply_content": "x"},
    ).json()
    reply_id = created["data"]["id"]

    resp = client.delete(
        f"{GOODS_REPLY_URL}/{reply_id}", headers=_auth_header(token)
    ).json()
    assert resp["success"] is True

    # 记录未被物理删除，且 enabled=False。
    row = db_session.get(GoodsReply, reply_id)
    assert row is not None
    assert bool(row.enabled) is False


def test_goods_reply_data_scope_isolation(client, test_users, reply_env):
    """数据范围隔离：非管理员不可在他人店铺下创建商品专属回复（需求 3.7）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    shop_b_pk = reply_env["shop_b_pk"]  # 归属他人

    resp = client.post(
        GOODS_REPLY_URL,
        headers=_auth_header(token),
        json={"shop_pk": shop_b_pk, "goods_id": "G_X", "reply_content": "y"},
    ).json()
    assert resp["success"] is False
    assert resp["code"] == CODE_FORBIDDEN
    assert resp["message"] == MSG_FORBIDDEN


def test_reply_denied_for_unauthorized_user(client, test_users, reply_env):
    """无权限访问被拒：访客角色（未授权 reply）调用列表返回「无访问权限」（需求 2.4）。"""
    guest = test_users["guest"]
    token = _login_token(client, guest["username"], guest["password"])

    resp = client.get(
        GOODS_REPLY_URL,
        headers=_auth_header(token),
        params={"shop_pk": reply_env["shop_a_pk"]},
    ).json()
    assert resp["success"] is False
    assert resp["code"] == CODE_FORBIDDEN

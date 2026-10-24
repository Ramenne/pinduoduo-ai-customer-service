# -*- coding: utf-8 -*-
"""
backend.tests.test_knowledge_api —— 知识库管理接口与服务单元测试
================================================================
本文件用途：对 backend 知识库管理（app.api.routes.knowledge 与
app.services.product_knowledge_service / cs_knowledge_service）进行单元测试，
覆盖需求 9（商品知识库）与需求 10（客服知识库）中「配置管理」核心验收场景：

- 商品知识 upsert 幂等（需求 9.2）：同 (shop_pk, goods_id) 多次写入记录数恒为 1。
- 商品知识列表后端分页（需求 9.3）：返回 {list,total,page,page_size}。
- 商品知识逻辑删除（需求 9.5）：删除经 status=0，记录保留、总数不变。
- 客服知识新增 / 列表分页（需求 10.1 / 10.6）。
- 客服知识批量导入去重（需求 10.2）：跳过同店铺内 (标题, 内容) 完全相同项，
  返回成功 / 跳过数量。
- 无权限访问被拒（需求 2.4）。

测试方案：pytest + FastAPI TestClient + 内存 SQLite（夹具见 conftest.py）。
本模块额外为 product_knowledge / cs_knowledge 资源补充权限点与授权用户，并预置
归属当前用户的店铺以通过数据范围隔离校验。所有接口 HTTP 恒返回 200。
"""
from __future__ import annotations

import pytest

from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import cs_knowledge_service, product_knowledge_service
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
PK_URL = f"{API_PREFIX}/product-knowledge"
CS_URL = f"{API_PREFIX}/cs-knowledge"


# ----------------------------------------------------------------------
# 夹具：权限点 + 授权 / 无权限用户 + 归属店铺
# ----------------------------------------------------------------------
@pytest.fixture()
def kb_users(db_session):
    """预置知识库资源权限点、授权 / 无权限用户与归属店铺。

    构造：
    - kb_manager 角色：被授予 product_knowledge / cs_knowledge 资源的
      view/create/update/disable 权限；
    - kb_user：授权用户（kb_manager 角色，启用），并拥有一家归属店铺；
    - kb_guest：无权限用户（访客角色，启用）。
    返回登录凭据与归属店铺主键 shop_pk。
    """
    role_kb = SysRole(role_name="知识库管理员", is_admin=False, status=1)
    role_guest = SysRole(role_name="访客KB", is_admin=False, status=1)
    db_session.add_all([role_kb, role_guest])
    db_session.flush()

    for resource in ("product_knowledge", "cs_knowledge"):
        for action in ("view", "create", "update", "disable"):
            perm = SysPermission(resource_key=resource, action=action)
            db_session.add(perm)
            db_session.flush()
            db_session.add(
                SysRolePermission(role_id=role_kb.id, permission_id=perm.id)
            )
    db_session.flush()

    kb_password = "kb-password-123"
    guest_password = "kbguest-password-123"
    kb_user = SysUser(
        username="kb_user",
        password_hash=hash_password(kb_password),
        role_id=role_kb.id,
        status=1,
    )
    kb_guest = SysUser(
        username="kb_guest",
        password_hash=hash_password(guest_password),
        role_id=role_guest.id,
        status=1,
    )
    db_session.add_all([kb_user, kb_guest])
    db_session.flush()

    # 归属 kb_user 的店铺，使其通过数据范围隔离校验。
    shop = Shop(shop_id="shop-kb-001", shop_name="知识库测试店", owner_user_id=kb_user.id, status=1)
    db_session.add(shop)
    db_session.flush()
    db_session.commit()

    return {
        "kb_user": {"username": "kb_user", "password": kb_password},
        "kb_guest": {"username": "kb_guest", "password": guest_password},
        "shop_pk": shop.id,
        "kb_user_id": kb_user.id,
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
# 商品知识接口测试
# ----------------------------------------------------------------------
def test_product_knowledge_upsert_idempotent(client, kb_users):
    """商品知识 upsert 幂等：同 (shop_pk, goods_id) 多次写入记录数恒为 1（需求 9.2）。"""
    token = _login_token(
        client, kb_users["kb_user"]["username"], kb_users["kb_user"]["password"]
    )
    shop_pk = kb_users["shop_pk"]
    for name in ("商品A", "商品A改名"):
        resp = client.post(
            PK_URL,
            headers=_auth_header(token),
            json={"shop_pk": shop_pk, "goods_id": "g-1", "goods_name": name},
        )
        assert resp.json()["success"] is True, resp.json()

    listed = client.get(
        PK_URL, headers=_auth_header(token), params={"shop_pk": shop_pk}
    ).json()
    assert listed["data"]["total"] == 1
    assert listed["data"]["list"][0]["goods_name"] == "商品A改名"


def test_product_knowledge_logical_delete(client, kb_users):
    """商品知识逻辑删除：删除经 status=0，记录保留、总数不变（需求 9.5）。"""
    token = _login_token(
        client, kb_users["kb_user"]["username"], kb_users["kb_user"]["password"]
    )
    shop_pk = kb_users["shop_pk"]
    create = client.post(
        PK_URL,
        headers=_auth_header(token),
        json={"shop_pk": shop_pk, "goods_id": "g-del", "goods_name": "待删除"},
    ).json()
    item_id = create["data"]["id"]

    resp = client.delete(f"{PK_URL}/{item_id}", headers=_auth_header(token))
    assert resp.json()["success"] is True

    detail = client.get(f"{PK_URL}/{item_id}", headers=_auth_header(token)).json()
    assert detail["success"] is True
    assert detail["data"]["status"] == 0
    listed = client.get(
        PK_URL, headers=_auth_header(token), params={"shop_pk": shop_pk}
    ).json()
    assert listed["data"]["total"] == 1


def test_product_knowledge_access_denied(client, kb_users):
    """无权限访问被拒：未授权用户调用返回「无访问权限」（需求 2.4）。"""
    token = _login_token(
        client, kb_users["kb_guest"]["username"], kb_users["kb_guest"]["password"]
    )
    resp = client.get(
        PK_URL, headers=_auth_header(token), params={"shop_pk": kb_users["shop_pk"]}
    )
    body = resp.json()
    assert body["success"] is False
    assert body["code"] == CODE_FORBIDDEN
    assert body["message"] == MSG_FORBIDDEN


# ----------------------------------------------------------------------
# 客服知识接口测试
# ----------------------------------------------------------------------
def test_cs_knowledge_create_and_list(client, kb_users):
    """客服知识新增与列表分页（需求 10.1 / 10.6）。"""
    token = _login_token(
        client, kb_users["kb_user"]["username"], kb_users["kb_user"]["password"]
    )
    shop_pk = kb_users["shop_pk"]
    for idx in range(3):
        resp = client.post(
            CS_URL,
            headers=_auth_header(token),
            json={
                "shop_pk": shop_pk,
                "title": f"标题-{idx}",
                "content": f"内容-{idx}",
                "tags": "售后,物流",
            },
        )
        assert resp.json()["success"] is True

    listed = client.get(
        CS_URL,
        headers=_auth_header(token),
        params={"shop_pk": shop_pk, "page": 1, "page_size": 20},
    ).json()
    assert listed["success"] is True
    data = listed["data"]
    assert set(["list", "total", "page", "page_size"]).issubset(data.keys())
    assert data["total"] == 3


def test_cs_knowledge_import_dedup(client, kb_users):
    """批量导入去重：跳过同店铺内 (标题, 内容) 完全相同项，返回成功 / 跳过数（需求 10.2）。"""
    token = _login_token(
        client, kb_users["kb_user"]["username"], kb_users["kb_user"]["password"]
    )
    shop_pk = kb_users["shop_pk"]
    # 先存在一条 (退货, 七天无理由)。
    client.post(
        CS_URL,
        headers=_auth_header(token),
        json={"shop_pk": shop_pk, "title": "退货", "content": "七天无理由"},
    )
    payload = {
        "shop_pk": shop_pk,
        "items": [
            {"title": "退货", "content": "七天无理由"},   # 与已有重复 → 跳过
            {"title": "发货", "content": "48小时内"},       # 新增
            {"title": "发货", "content": "48小时内"},       # 与本批次重复 → 跳过
            {"title": "保修", "content": "全国联保"},        # 新增
        ],
    }
    resp = client.post(
        f"{CS_URL}/import", headers=_auth_header(token), json=payload
    )
    body = resp.json()
    assert body["success"] is True, body
    assert body["data"]["imported"] == 2
    assert body["data"]["skipped"] == 2
    assert body["data"]["total"] == 4

    # 店铺内最终客服知识总数 = 1（原有） + 2（新增） = 3。
    listed = client.get(
        CS_URL, headers=_auth_header(token), params={"shop_pk": shop_pk}
    ).json()
    assert listed["data"]["total"] == 3


# ----------------------------------------------------------------------
# 服务层测试（不经 HTTP）
# ----------------------------------------------------------------------
def test_service_import_skips_invalid_items(db_session, kb_users):
    """服务层：空标题 / 空内容的导入项计入跳过，不入库（需求 10.2 配套）。"""
    user = db_session.get(SysUser, kb_users["kb_user_id"])
    shop_pk = kb_users["shop_pk"]
    result = cs_knowledge_service.import_cs_knowledge(
        db_session,
        user,
        shop_pk=shop_pk,
        items=[
            {"title": "", "content": "无标题"},
            {"title": "正常", "content": "正常内容"},
        ],
    )
    assert result.success is True
    assert result.data["imported"] == 1
    assert result.data["skipped"] == 1

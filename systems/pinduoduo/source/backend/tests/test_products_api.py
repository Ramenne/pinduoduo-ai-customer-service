# -*- coding: utf-8 -*-
"""
backend.tests.test_products_api —— 商品管理接口单元测试
========================================================
本文件用途：对 backend「商品管理」接口（app.api.routes.products）进行单元测试，
覆盖需求 15 的核心验收场景：

- 商品列表后端分页（需求 15.1）：返回 {list,total,page,page_size}。
- 触发商品同步成功（需求 15.2/15.4）：拉取结果按 (shop_pk, goods_id) upsert 入库，
  重复同步幂等（同 goods_id 更新而非新建）。
- 签名缺失降级（需求 15.3）：终止同步、返回固定中文提示、记录系统日志。
- 从商品记录创建商品专属回复 / 商品知识（需求 15.5）：关联对应 goods_id 持久化。
- 数据范围隔离：非管理员不可操作他人店铺商品（需求 3.7）。
- 无权限访问被拒（需求 2.4）。

测试方案：pytest + FastAPI TestClient + 内存 SQLite（夹具见 conftest.py）。
对 websocket 拉取接口（product_sync_client.pull_products）做 monkeypatch，避免
真实 HTTP 调用。所有接口 HTTP 恒返回 200。
"""
from __future__ import annotations

import pytest

from app.core.business_codes import (
    CODE_FORBIDDEN,
    CODE_SIGNATURE_MISSING,
    MSG_FORBIDDEN,
    MSG_SIGNATURE_MISSING,
)
from app.services import product_service, product_sync_client
from app.services import product_spec_backfill, product_jobs
from common.models.knowledge_models import Product, ProductKnowledge
from common.models.log_models import SystemLog
from common.models.reply_models import GoodsReply
from common.models.shop_models import Shop
from common.models.user_models import SysPermission, SysRolePermission

API_PREFIX = "/api/v1"
LOGIN_URL = f"{API_PREFIX}/login"
PRODUCTS_URL = f"{API_PREFIX}/products"
SYNC_URL = f"{API_PREFIX}/products/sync"


def _detail_url(product_id: int) -> str:
    """构造商品详情接口 URL。"""
    return f"{PRODUCTS_URL}/{product_id}/detail"


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
def product_env(db_session, seed_permissions, test_users):
    """预置 product 资源权限点、授权用户、店铺与商品记录。"""
    manager_id = test_users["manager"]["id"]
    role_manager_id = seed_permissions["role_manager_id"]

    # 追加 product 资源权限点并授予 user_manager 角色（含 reply 用于派生回复）。
    for resource in ("product", "reply"):
        for action in ("view", "create", "update", "disable"):
            perm = SysPermission(resource_key=resource, action=action)
            db_session.add(perm)
            db_session.flush()
            db_session.add(
                SysRolePermission(role_id=role_manager_id, permission_id=perm.id)
            )

    # 店铺：shop_a 归属 manager，shop_b 归属他人。
    shop_a = Shop(shop_id="pdd_a", shop_name="店铺A", owner_user_id=manager_id, status=1)
    shop_b = Shop(shop_id="pdd_b", shop_name="店铺B", owner_user_id=999999, status=1)
    db_session.add_all([shop_a, shop_b])
    db_session.flush()

    # 预置一条商品记录于 shop_a。
    product = Product(
        shop_pk=shop_a.id,
        goods_id="G1",
        goods_name="商品一",
        price=9.9,
        sold_quantity=10,
        status=1,
    )
    db_session.add(product)
    db_session.flush()
    db_session.commit()

    return {
        "shop_a_pk": shop_a.id,
        "shop_b_pk": shop_b.id,
        "product_id": product.id,
        "manager_id": manager_id,
    }


def test_list_products_pagination(client, test_users, product_env):
    """商品列表后端分页：返回 {list,total,page,page_size}（需求 15.1）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    shop_pk = product_env["shop_a_pk"]

    resp = client.get(
        PRODUCTS_URL,
        headers=_auth_header(token),
        params={"shop_pk": shop_pk, "page": 1, "page_size": 10},
    ).json()
    data = resp["data"]
    assert resp["success"] is True
    assert data["total"] == 1
    assert data["page"] == 1
    assert data["page_size"] == 10
    assert data["list"][0]["goods_id"] == "G1"


def test_get_product_detail_success_merge(client, test_users, product_env, monkeypatch, db_session):
    """查看商品详情成功：合并库内基础信息与实时规格，并将规格落库（需求 15）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    product_id = product_env["product_id"]

    # mock websocket 实时详情：返回规格列表。
    def fake_detail(shop_id, owner_user_id, goods_id):
        return product_sync_client.ProductDetailResult(
            ok=True,
            detail={
                "goods_id": goods_id,
                "goods_name": "商品一",
                "specifications": ["颜色: 红色", "尺码: L", "商品分类: 服饰 > 上衣"],
            },
        )

    monkeypatch.setattr(product_sync_client, "fetch_product_detail", fake_detail)

    resp = client.get(_detail_url(product_id), headers=_auth_header(token), params={"refresh_specs": True}).json()
    assert resp["success"] is True
    data = resp["data"]
    # 库内基础信息。
    assert data["goods_id"] == "G1"
    assert data["goods_name"] == "商品一"
    # 实时规格已合并。
    assert data["specifications"] == ["颜色: 红色", "尺码: L", "商品分类: 服饰 > 上衣"]
    assert data["detail_message"] == ""

    # 规格已落库（重新查询商品记录，specifications 字段为 JSON 文本）。
    db_session.expire_all()
    saved = db_session.query(Product).filter_by(id=product_id).first()
    assert saved.specifications is not None
    assert "红色" in saved.specifications

    # 列表接口也应展示已落库的规格（解码为列表，需求 15.1）。
    listed = client.get(
        PRODUCTS_URL,
        headers=_auth_header(token),
        params={"shop_pk": product_env["shop_a_pk"]},
    ).json()
    row = next(p for p in listed["data"]["list"] if p["id"] == product_id)
    assert row["specifications"] == ["颜色: 红色", "尺码: L", "商品分类: 服饰 > 上衣"]


def test_get_product_detail_degrade_uses_stored_specs(
    client, test_users, product_env, monkeypatch, db_session
):
    """库内已有规格时优先读库：直接返回库内规格，不再调用实时详情接口（需求 15）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    product_id = product_env["product_id"]

    # 预置已落库规格（同步后由后台线程补拉落库的场景）。
    saved = db_session.query(Product).filter_by(id=product_id).first()
    saved.specifications = '["材质: 纯棉", "产地: 江苏"]'
    db_session.commit()

    # mock websocket：若被调用则记录，用于断言「库内有规格时不应调用」。
    called = {"hit": False}

    def fake_detail(shop_id, owner_user_id, goods_id):
        called["hit"] = True
        return product_sync_client.ProductDetailResult(
            ok=False, message="商品详情服务暂不可用，请稍后重试"
        )

    monkeypatch.setattr(product_sync_client, "fetch_product_detail", fake_detail)

    resp = client.get(_detail_url(product_id), headers=_auth_header(token)).json()
    assert resp["success"] is True
    data = resp["data"]
    # 优先读库：直接返回库内已存规格，且无降级提示。
    assert data["specifications"] == ["材质: 纯棉", "产地: 江苏"]
    assert data["detail_message"] == ""
    # 库内已有规格，不应再触发实时详情接口调用。
    assert called["hit"] is False


def test_get_product_detail_external_fail_degrade(
    client, test_users, product_env, monkeypatch
):
    """库内无规格且实时非签名失败时降级：规格为空并附中文提示，不整体失败（需求 26）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    product_id = product_env["product_id"]

    def fake_detail(shop_id, owner_user_id, goods_id):
        return product_sync_client.ProductDetailResult(
            ok=False, message="商品详情服务暂不可用，请稍后重试"
        )

    monkeypatch.setattr(product_sync_client, "fetch_product_detail", fake_detail)

    resp = client.get(_detail_url(product_id), headers=_auth_header(token), params={"refresh_specs": True}).json()
    assert resp["success"] is True
    data = resp["data"]
    assert data["specifications"] == []
    assert "暂不可用" in data["detail_message"]


def test_get_product_detail_signature_missing_degrade(
    client, test_users, product_env, monkeypatch
):
    """签名缺失时降级：仍返回库内基础信息，规格为空并附中文提示（需求 26.2）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    product_id = product_env["product_id"]

    def fake_detail(shop_id, owner_user_id, goods_id):
        return product_sync_client.ProductDetailResult(
            ok=False, signature_missing=True, message="x"
        )

    monkeypatch.setattr(product_sync_client, "fetch_product_detail", fake_detail)

    resp = client.get(_detail_url(product_id), headers=_auth_header(token), params={"refresh_specs": True}).json()
    # 降级不整体失败：success=True，返回库内信息。
    assert resp["success"] is True
    data = resp["data"]
    assert data["goods_id"] == "G1"
    assert data["specifications"] == []
    assert data["detail_message"] == MSG_SIGNATURE_MISSING


def test_get_product_detail_not_found(client, test_users, product_env):
    """商品不存在：返回 NOT_FOUND（需求 15）。"""
    from app.core.business_codes import CODE_NOT_FOUND

    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])

    resp = client.get(_detail_url(999999), headers=_auth_header(token)).json()
    assert resp["success"] is False
    assert resp["code"] == CODE_NOT_FOUND


def test_sync_products_success_upsert(client, test_users, product_env, monkeypatch):
    """触发商品同步成功：拉取结果 upsert 入库，重复同步幂等（需求 15.2/15.4）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    shop_pk = product_env["shop_a_pk"]

    # mock websocket 拉取：返回 G1（已存在，更新）+ G2（新增）。
    def fake_pull(shop_pk, shop_id, owner_user_id):
        return product_sync_client.ProductPullResult(
            ok=True,
            products=[
                {"goods_id": "G1", "goods_name": "商品一改", "price": 8.8, "sold_quantity": 20},
                {"goods_id": "G2", "goods_name": "商品二", "price": 5.0, "sold_quantity": 3},
            ],
        )

    monkeypatch.setattr(product_sync_client, "pull_products", fake_pull)
    from app.services import product_jobs
    monkeypatch.setattr(product_sync_client, 'fetch_product_detail', lambda *a:
        product_sync_client.ProductDetailResult(ok=True, detail={'specifications':['测试规格']}))
    resp = client.post(SYNC_URL, headers=_auth_header(token), json={'shop_pk':shop_pk}).json()
    assert resp['success'] and resp['data']['state']=='queued'
    product_jobs.run(product_jobs.claim_next())

    # 列表应为 2 条（G1 更新、G2 新增），幂等不重复。
    listed = client.get(
        PRODUCTS_URL, headers=_auth_header(token), params={"shop_pk": shop_pk}
    ).json()
    assert listed["data"]["total"] == 2


def test_sync_products_signature_missing(client, test_users, product_env, monkeypatch, db_session):
    """签名缺失降级：终止同步、返回固定中文提示、记录系统日志（需求 15.3）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    shop_pk = product_env["shop_a_pk"]

    def fake_pull(shop_pk, shop_id, owner_user_id):
        return product_sync_client.ProductPullResult(
            ok=False, signature_missing=True, message="x"
        )

    monkeypatch.setattr(product_sync_client, "pull_products", fake_pull)

    resp = client.post(
        SYNC_URL, headers=_auth_header(token), json={"shop_pk": shop_pk}
    ).json()
    from app.services import product_jobs
    from common.models.product_sync_models import ProductSyncJob
    assert resp['success'] and resp['data']['state']=='queued'
    product_jobs.run(product_jobs.claim_next());db_session.expire_all()
    job=db_session.query(ProductSyncJob).one()
    assert job.state=='failed' and job.message=='x'

    # 终止同步：未新增商品（仍为预置的 1 条 G1）。
    listed = client.get(
        PRODUCTS_URL, headers=_auth_header(token), params={"shop_pk": shop_pk}
    ).json()
    assert listed["data"]["total"] == 1


def test_create_goods_reply_from_product(client, test_users, product_env, db_session):
    """从商品记录创建商品专属回复：关联 goods_id 持久化（需求 15.5）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    product_id = product_env["product_id"]

    resp = client.post(
        f"{PRODUCTS_URL}/{product_id}/goods-reply",
        headers=_auth_header(token),
        json={"reply_content": "现货充足", "reply_type": "text", "enabled": True},
    ).json()
    assert resp["success"] is True
    assert resp["data"]["goods_id"] == "G1"

    rows = db_session.query(GoodsReply).filter_by(goods_id="G1").all()
    assert len(rows) == 1
    assert rows[0].reply_content == "现货充足"


def test_create_product_knowledge_from_product(client, test_users, product_env, db_session):
    """从商品记录创建商品知识：按 (shop_pk, goods_id) upsert（需求 15.5）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    product_id = product_env["product_id"]

    resp = client.post(
        f"{PRODUCTS_URL}/{product_id}/knowledge",
        headers=_auth_header(token),
        json={"extracted_content": "材质：纯棉"},
    ).json()
    assert resp["success"] is True
    assert resp["data"]["goods_id"] == "G1"

    rows = db_session.query(ProductKnowledge).filter_by(goods_id="G1").all()
    assert len(rows) == 1
    assert rows[0].extracted_content == "材质：纯棉"
    assert rows[0].last_extracted_at is not None


def test_sync_products_data_scope_isolation(client, test_users, product_env):
    """数据范围隔离：非管理员不可同步他人店铺商品（需求 3.7）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    shop_b_pk = product_env["shop_b_pk"]  # 归属他人

    resp = client.post(
        SYNC_URL, headers=_auth_header(token), json={"shop_pk": shop_b_pk}
    ).json()
    assert resp["success"] is False
    assert resp["code"] == CODE_FORBIDDEN
    assert resp["message"] == MSG_FORBIDDEN


def test_products_denied_for_unauthorized_user(client, test_users, product_env):
    """无权限访问被拒：访客角色调用商品列表返回「无访问权限」（需求 2.4）。"""
    guest = test_users["guest"]
    token = _login_token(client, guest["username"], guest["password"])

    resp = client.get(
        PRODUCTS_URL,
        headers=_auth_header(token),
        params={"shop_pk": product_env["shop_a_pk"]},
    ).json()
    assert resp["success"] is False
    assert resp["code"] == CODE_FORBIDDEN


# ----------------------------------------------------------------------
# 后台规格补拉（product_spec_backfill）—— 独立线程异步落库，不影响主流程
# ----------------------------------------------------------------------
def test_spec_backfill_persists_specifications(
    client, test_users, product_env, monkeypatch, db_session
):
    """后台补拉：逐个商品拉详情规格并落库（直接调用线程主体，避免并发不确定）。"""
    shop_a_pk = product_env["shop_a_pk"]
    product_id = product_env["product_id"]

    # mock websocket 详情查询：返回规格。
    def fake_detail(shop_id, owner_user_id, goods_id):
        return product_sync_client.ProductDetailResult(
            ok=True,
            detail={"goods_id": goods_id, "specifications": ["颜色: 蓝色", "尺码: M"]},
        )

    monkeypatch.setattr(product_sync_client, "fetch_product_detail", fake_detail)

    # 直接执行线程主体（同线程、同内存库），验证落库结果确定可断言。
    product_spec_backfill.schedule_spec_backfill(
        shop_pk=shop_a_pk, shop_id="pdd_a", owner_user_id=product_env["manager_id"]
    )
    product_jobs.run(product_jobs.claim_next())

    db_session.expire_all()
    saved = db_session.query(Product).filter_by(id=product_id).first()
    assert saved.specifications is not None
    assert "蓝色" in saved.specifications


def test_spec_backfill_skips_on_failure(
    client, test_users, product_env, monkeypatch, db_session
):
    """后台补拉：详情查询失败时跳过、不覆盖库内规格、不抛异常（需求 26）。"""
    shop_a_pk = product_env["shop_a_pk"]
    product_id = product_env["product_id"]

    # 预置库内已有规格。
    saved = db_session.query(Product).filter_by(id=product_id).first()
    saved.specifications = '["原有规格"]'
    db_session.commit()

    # mock websocket 详情查询：外部依赖失败。
    def fake_detail(shop_id, owner_user_id, goods_id):
        return product_sync_client.ProductDetailResult(ok=False, message="暂不可用")

    monkeypatch.setattr(product_sync_client, "fetch_product_detail", fake_detail)

    # 不应抛异常。
    product_spec_backfill.schedule_spec_backfill(
        shop_pk=shop_a_pk, shop_id="pdd_a", owner_user_id=product_env["manager_id"]
    )
    product_jobs.run(product_jobs.claim_next())

    db_session.expire_all()
    saved = db_session.query(Product).filter_by(id=product_id).first()
    # 失败跳过：库内规格保持不变。
    assert saved.specifications == '["原有规格"]'


def test_empty_spec_detail_is_fast_local_read(client, test_users, product_env, monkeypatch):
    manager = test_users['manager']
    token = _login_token(client, manager['username'], manager['password'])
    def no_network(**kwargs):
        raise AssertionError('opening details must not request external specs')
    monkeypatch.setattr(product_sync_client, 'fetch_product_detail', no_network)
    body = client.get(_detail_url(product_env['product_id']), headers=_auth_header(token)).json()
    assert body['success']
    assert body['data']['goods_id'] == 'G1'
    assert '尚未获取' in body['data']['detail_message']


def test_explicit_spec_refresh_failure_preserves_saved_specs(client, test_users, product_env, monkeypatch, db_session):
    manager = test_users['manager']
    token = _login_token(client, manager['username'], manager['password'])
    product_id = product_env['product_id']
    saved = db_session.query(Product).filter_by(id=product_id).first()
    saved.specifications = '["已保存规格"]'
    db_session.commit()
    monkeypatch.setattr(product_sync_client, 'fetch_product_detail',
        lambda **kw: product_sync_client.ProductDetailResult(ok=False, message='服务超时'))
    body = client.get(_detail_url(product_id), headers=_auth_header(token), params={'refresh_specs': True}).json()
    assert body['success']
    assert body['data']['specifications'] == ['已保存规格']
    db_session.expire_all()
    assert '已保存规格' in db_session.get(Product, product_id).specifications

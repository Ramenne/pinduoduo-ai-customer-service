# -*- coding: utf-8 -*-
"""
backend.tests.test_keywords_api —— 关键词规则管理接口与服务单元测试
==================================================================
本文件用途：对 backend 关键词规则管理（app.api.routes.keywords 与
app.services.keyword_service）进行单元测试，覆盖需求 6（关键词自动回复）中
「配置管理」部分的核心验收场景：

- 创建规则成功（需求 6.1）：合法入参创建规则并返回规则信息。
- 创建规则校验失败：非法匹配方式 / 空关键词 / 非法正则返回 success=false。
- 列表后端分页（需求 6.6）：返回 {list, total, page, page_size} 分页结构，
  支持按店铺、启用状态筛选。
- 启停用（需求 6.7）：停用经状态字段标记，停用规则不参与匹配（enabled=False）。
- 逻辑删除（规范 11）：删除经 enabled=False 标记，记录保留、总数不变。
- 无权限访问被拒（需求 2.4）：未授权用户调用返回「无访问权限」。

测试方案：pytest + FastAPI TestClient + 内存 SQLite（夹具见 conftest.py）。
本模块额外为 ``keyword`` 资源补充权限点与授权用户，复用 conftest 的内存库与角色。
所有接口 HTTP 恒返回 200，业务成败由统一响应体表达，断言据此进行。
"""
from __future__ import annotations

import pytest

from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import keyword_service
from common.models.reply_models import KeywordRule
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
KEYWORDS_URL = f"{API_PREFIX}/keywords"


# ----------------------------------------------------------------------
# 夹具：为 keyword 资源补充权限点 + 授权用户 / 无权限用户
# ----------------------------------------------------------------------
@pytest.fixture()
def keyword_users(db_session):
    """预置 keyword 资源权限点与授权 / 无权限用户。

    构造：
    - keyword_manager 角色：被授予 keyword 资源的 view/create/update/disable 权限；
    - kw_user：授权用户（keyword_manager 角色，启用）；
    - kw_guest：无权限用户（访客角色，不授予任何权限，启用）。
    """
    role_kw = SysRole(role_name="关键词管理员", is_admin=False, status=1)
    role_guest = SysRole(role_name="访客KW", is_admin=False, status=1)
    db_session.add_all([role_kw, role_guest])
    db_session.flush()

    perm_ids = []
    for action in ("view", "create", "update", "disable"):
        perm = SysPermission(resource_key="keyword", action=action)
        db_session.add(perm)
        db_session.flush()
        perm_ids.append(perm.id)
    for pid in perm_ids:
        db_session.add(SysRolePermission(role_id=role_kw.id, permission_id=pid))
    db_session.flush()

    kw_password = "kw-password-123"
    guest_password = "kwguest-password-123"
    kw_user = SysUser(
        username="kw_user",
        password_hash=hash_password(kw_password),
        role_id=role_kw.id,
        status=1,
    )
    kw_guest = SysUser(
        username="kw_guest",
        password_hash=hash_password(guest_password),
        role_id=role_guest.id,
        status=1,
    )
    db_session.add_all([kw_user, kw_guest])
    db_session.flush()

    # 预置 kw_user 名下店铺（pk=1/2/7，与各用例硬编码 shop_pk 对齐），
    # 使数据范围隔离校验（需求 3.7 / 规范 42a）通过：kw_user 可操作本人店铺。
    for shop_pk in (1, 2, 7):
        db_session.add(
            Shop(
                id=shop_pk,
                shop_id=f"pdd-shop-{shop_pk}",
                shop_name=f"店铺{shop_pk}",
                owner_user_id=kw_user.id,
                status=1,
            )
        )
    db_session.flush()
    db_session.commit()

    return {
        "kw_user": {"id": kw_user.id, "username": "kw_user", "password": kw_password},
        "kw_guest": {"username": "kw_guest", "password": guest_password},
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
# 接口级测试
# ----------------------------------------------------------------------
def test_create_keyword_rule_success(client, keyword_users):
    """创建规则成功：合法入参创建并返回规则信息（需求 6.1）。"""
    token = _login_token(
        client, keyword_users["kw_user"]["username"], keyword_users["kw_user"]["password"]
    )
    resp = client.post(
        KEYWORDS_URL,
        headers=_auth_header(token),
        json={
            "shop_pk": 1,
            "keyword": "发货",
            "match_type": "contains",
            "reply_content": "您好，48小时内发货",
            "reply_type": "text",
            "priority": 10,
        },
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is True
    rule = body["data"]
    assert rule["keyword"] == "发货"
    assert rule["match_type"] == "contains"
    assert rule["priority"] == 10
    assert rule["enabled"] is True


def test_create_keyword_rule_invalid_match_type(client, keyword_users):
    """创建校验失败：非法匹配方式返回 success=false（需求 6.1）。"""
    token = _login_token(
        client, keyword_users["kw_user"]["username"], keyword_users["kw_user"]["password"]
    )
    resp = client.post(
        KEYWORDS_URL,
        headers=_auth_header(token),
        json={
            "shop_pk": 1,
            "keyword": "发货",
            "match_type": "unknown",
            "reply_content": "内容",
        },
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is False
    assert body["data"] is None


def test_create_keyword_rule_invalid_regex(client, keyword_users):
    """创建校验失败：正则匹配方式但表达式非法返回 success=false（需求 6.1）。"""
    token = _login_token(
        client, keyword_users["kw_user"]["username"], keyword_users["kw_user"]["password"]
    )
    resp = client.post(
        KEYWORDS_URL,
        headers=_auth_header(token),
        json={
            "shop_pk": 1,
            "keyword": "[未闭合",
            "match_type": "regex",
            "reply_content": "内容",
        },
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is False


def test_list_keyword_rules_pagination(client, keyword_users):
    """列表后端分页：返回分页结构并支持筛选（需求 6.6）。"""
    token = _login_token(
        client, keyword_users["kw_user"]["username"], keyword_users["kw_user"]["password"]
    )
    # 先创建 3 条规则。
    for idx in range(3):
        client.post(
            KEYWORDS_URL,
            headers=_auth_header(token),
            json={
                "shop_pk": 1,
                "keyword": f"kw-{idx}",
                "match_type": "full",
                "reply_content": f"reply-{idx}",
            },
        )

    resp = client.get(
        KEYWORDS_URL,
        headers=_auth_header(token),
        params={"page": 1, "page_size": 20, "shop_pk": 1},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is True
    data = body["data"]
    assert set(["list", "total", "page", "page_size"]).issubset(data.keys())
    assert data["total"] == 3
    assert data["page_size"] == 20
    assert len(data["list"]) == 3


def test_update_keyword_rule_status_disable(client, keyword_users):
    """启停用：停用经状态字段标记，停用规则 enabled=False（需求 6.7）。"""
    token = _login_token(
        client, keyword_users["kw_user"]["username"], keyword_users["kw_user"]["password"]
    )
    create = client.post(
        KEYWORDS_URL,
        headers=_auth_header(token),
        json={
            "shop_pk": 1,
            "keyword": "退款",
            "match_type": "contains",
            "reply_content": "请提供订单号",
        },
    ).json()
    rule_id = create["data"]["id"]

    resp = client.put(
        f"{KEYWORDS_URL}/{rule_id}/status",
        headers=_auth_header(token),
        json={"enabled": False},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is True
    assert body["data"]["enabled"] is False


def test_delete_keyword_rule_is_logical(client, keyword_users):
    """逻辑删除：删除经 enabled=False 标记，记录保留、总数不变（规范 11）。"""
    token = _login_token(
        client, keyword_users["kw_user"]["username"], keyword_users["kw_user"]["password"]
    )
    create = client.post(
        KEYWORDS_URL,
        headers=_auth_header(token),
        json={
            "shop_pk": 7,
            "keyword": "保修",
            "match_type": "contains",
            "reply_content": "全国联保一年",
        },
    ).json()
    rule_id = create["data"]["id"]

    # 删除。
    resp = client.delete(f"{KEYWORDS_URL}/{rule_id}", headers=_auth_header(token))
    assert resp.json()["success"] is True

    # 记录仍存在（逻辑删除），但 enabled=False，且总数不变。
    detail = client.get(f"{KEYWORDS_URL}/{rule_id}", headers=_auth_header(token)).json()
    assert detail["success"] is True
    assert detail["data"]["enabled"] is False

    listed = client.get(
        KEYWORDS_URL, headers=_auth_header(token), params={"shop_pk": 7}
    ).json()
    assert listed["data"]["total"] == 1


def test_keyword_access_denied_for_unauthorized(client, keyword_users):
    """无权限访问被拒：未授权用户调用返回「无访问权限」（需求 2.4）。"""
    token = _login_token(
        client,
        keyword_users["kw_guest"]["username"],
        keyword_users["kw_guest"]["password"],
    )
    resp = client.get(KEYWORDS_URL, headers=_auth_header(token))
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"] is False
    assert body["code"] == CODE_FORBIDDEN
    assert body["message"] == MSG_FORBIDDEN


# ----------------------------------------------------------------------
# 服务层测试（不经 HTTP，直接验证业务逻辑）
# ----------------------------------------------------------------------
def test_service_set_status_then_disabled_excluded_from_enabled_filter(
    db_session, keyword_users
):
    """服务层：停用后按 enabled=True 筛选不返回停用规则（需求 6.7）。"""
    operator_id = keyword_users["kw_user"]["id"]
    created = keyword_service.create_keyword_rule(
        db_session,
        shop_pk=1,
        keyword="发票",
        match_type="contains",
        reply_content="支持开具电子发票",
        operator_id=operator_id,
    )
    rule_id = created.data["id"]

    keyword_service.set_keyword_rule_status(
        db_session, rule_id, enabled=False, operator_id=operator_id
    )

    enabled_list = keyword_service.list_keyword_rules(
        db_session, shop_pk=1, enabled=True, operator_id=operator_id
    )
    assert enabled_list.data["total"] == 0

    disabled_list = keyword_service.list_keyword_rules(
        db_session, shop_pk=1, enabled=False, operator_id=operator_id
    )
    assert disabled_list.data["total"] == 1


def test_service_update_priority(db_session, keyword_users):
    """服务层：修改优先级生效（需求 6.1 配套）。"""
    operator_id = keyword_users["kw_user"]["id"]
    created = keyword_service.create_keyword_rule(
        db_session,
        shop_pk=2,
        keyword="物流",
        match_type="contains",
        reply_content="顺丰发货",
        priority=1,
        operator_id=operator_id,
    )
    rule_id = created.data["id"]

    updated = keyword_service.update_keyword_rule(
        db_session, rule_id, priority=99, operator_id=operator_id
    )
    assert updated.success is True
    assert updated.data["priority"] == 99

    # 直接核对数据库记录。
    rule = db_session.get(KeywordRule, rule_id)
    assert rule.priority == 99

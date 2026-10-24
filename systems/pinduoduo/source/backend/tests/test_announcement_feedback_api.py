# -*- coding: utf-8 -*-
"""
backend.tests.test_announcement_feedback_api —— 公告与意见反馈接口单元测试
=========================================================================
本文件用途：对 backend 公告（任务 8.4，需求 21.3）与意见反馈（需求 21.5）接口
进行单元测试，覆盖核心验收场景：

公告（需求 21.3）：
- 管理员新增公告并在用户端展示；
- 编辑公告字段生效；
- 启停用：停用公告不在用户端展示，记录保留；
- 逻辑删除：deleted_flag=True，记录保留、不在管理端默认列表与用户端展示；
- 非管理员访问管理端接口被拒（需求 21.17），但可访问用户端展示接口。

意见反馈（需求 21.5）：
- 所有登录用户提交反馈落库；
- 用户仅能查看本人反馈（数据范围隔离）；
- 管理员查看全部反馈列表并处理回复；
- 非管理员访问管理端反馈接口被拒。

测试方案：pytest + FastAPI TestClient + SQLite 内存库（夹具见 conftest.py），
所有接口 HTTP 恒返回 200，业务成败由统一响应体 {code, success, message, data}
表达。
"""
from __future__ import annotations

from app.core.business_codes import CODE_FORBIDDEN

# 业务路由统一前缀（与 _bootstrap.API_PREFIX 默认值一致）。
API_PREFIX = "/api/v1"
LOGIN_URL = f"{API_PREFIX}/login"
ANNOUNCEMENTS_URL = f"{API_PREFIX}/announcements"
ANNOUNCEMENTS_VISIBLE_URL = f"{API_PREFIX}/announcements/visible"
FEEDBACKS_URL = f"{API_PREFIX}/feedbacks"
FEEDBACKS_MINE_URL = f"{API_PREFIX}/feedbacks/mine"


def _login(client, username: str, password: str) -> str:
    """登录并返回访问令牌字符串。"""
    resp = client.post(LOGIN_URL, json={"username": username, "password": password})
    return resp.json()["data"]["token"]


def _auth(token: str) -> dict:
    """构造带 Bearer 令牌的鉴权请求头。"""
    return {"Authorization": f"Bearer {token}"}


# ----------------------------------------------------------------------
# 公告（需求 21.3）
# ----------------------------------------------------------------------
def test_admin_create_announcement_visible_to_user(client, test_users):
    """管理员新增启用公告后，用户端展示接口可见（需求 21.3）。"""
    admin_token = _login(client, test_users["admin"]["username"], test_users["admin"]["password"])
    guest_token = _login(client, test_users["guest"]["username"], test_users["guest"]["password"])

    create = client.post(
        ANNOUNCEMENTS_URL,
        headers=_auth(admin_token),
        json={"title": "系统升级公告", "content": "今晚维护", "enabled": True},
    ).json()
    assert create["success"] is True
    ann_id = create["data"]["id"]

    # 用户端展示可见该启用公告。
    visible = client.get(ANNOUNCEMENTS_VISIBLE_URL, headers=_auth(guest_token)).json()
    assert visible["success"] is True
    titles = [a["title"] for a in visible["data"]["list"]]
    assert "系统升级公告" in titles
    assert any(a["id"] == ann_id for a in visible["data"]["list"])


def test_update_announcement_persists(client, test_users):
    """编辑公告字段生效（需求 21.3 配套）。"""
    admin_token = _login(client, test_users["admin"]["username"], test_users["admin"]["password"])
    ann_id = client.post(
        ANNOUNCEMENTS_URL,
        headers=_auth(admin_token),
        json={"title": "旧标题", "content": "旧内容"},
    ).json()["data"]["id"]

    upd = client.put(
        f"{ANNOUNCEMENTS_URL}/{ann_id}",
        headers=_auth(admin_token),
        json={"title": "新标题"},
    ).json()
    assert upd["success"] is True
    assert upd["data"]["title"] == "新标题"


def test_disabled_announcement_hidden_from_user(client, test_users):
    """停用公告不在用户端展示，记录保留（需求 21.3）。"""
    admin_token = _login(client, test_users["admin"]["username"], test_users["admin"]["password"])
    guest_token = _login(client, test_users["guest"]["username"], test_users["guest"]["password"])
    ann_id = client.post(
        ANNOUNCEMENTS_URL,
        headers=_auth(admin_token),
        json={"title": "临时公告", "content": "内容", "enabled": True},
    ).json()["data"]["id"]

    # 停用。
    status_resp = client.put(
        f"{ANNOUNCEMENTS_URL}/{ann_id}/status",
        headers=_auth(admin_token),
        json={"enabled": False},
    ).json()
    assert status_resp["success"] is True

    # 用户端不可见。
    visible = client.get(ANNOUNCEMENTS_VISIBLE_URL, headers=_auth(guest_token)).json()
    assert all(a["id"] != ann_id for a in visible["data"]["list"])

    # 但管理端记录仍保留（详情可查）。
    detail = client.get(f"{ANNOUNCEMENTS_URL}/{ann_id}", headers=_auth(admin_token)).json()
    assert detail["success"] is True


def test_soft_delete_announcement_keeps_record(client, test_users):
    """逻辑删除公告：从默认列表与用户端展示移除，记录保留（需求 24.6）。"""
    admin_token = _login(client, test_users["admin"]["username"], test_users["admin"]["password"])
    guest_token = _login(client, test_users["guest"]["username"], test_users["guest"]["password"])
    ann_id = client.post(
        ANNOUNCEMENTS_URL,
        headers=_auth(admin_token),
        json={"title": "待删除公告", "content": "内容", "enabled": True},
    ).json()["data"]["id"]

    deleted = client.delete(f"{ANNOUNCEMENTS_URL}/{ann_id}", headers=_auth(admin_token)).json()
    assert deleted["success"] is True

    # 管理端默认列表不含已删除公告。
    listing = client.get(ANNOUNCEMENTS_URL, headers=_auth(admin_token)).json()
    assert all(a["id"] != ann_id for a in listing["data"]["list"])

    # 用户端展示不含已删除公告。
    visible = client.get(ANNOUNCEMENTS_VISIBLE_URL, headers=_auth(guest_token)).json()
    assert all(a["id"] != ann_id for a in visible["data"]["list"])

    # 详情视为不存在（逻辑删除）。
    detail = client.get(f"{ANNOUNCEMENTS_URL}/{ann_id}", headers=_auth(admin_token)).json()
    assert detail["success"] is False


def test_non_admin_cannot_manage_announcement(client, test_users):
    """非管理员访问公告管理端接口被拒（需求 21.17）。"""
    guest_token = _login(client, test_users["guest"]["username"], test_users["guest"]["password"])

    create = client.post(
        ANNOUNCEMENTS_URL,
        headers=_auth(guest_token),
        json={"title": "x", "content": "y"},
    ).json()
    assert create["success"] is False
    assert create["code"] == CODE_FORBIDDEN

    listing = client.get(ANNOUNCEMENTS_URL, headers=_auth(guest_token)).json()
    assert listing["success"] is False
    assert listing["code"] == CODE_FORBIDDEN


# ----------------------------------------------------------------------
# 意见反馈（需求 21.5）
# ----------------------------------------------------------------------
def test_user_submit_feedback_and_view_own(client, test_users):
    """用户提交反馈落库并可查看本人反馈（需求 21.5）。"""
    guest_token = _login(client, test_users["guest"]["username"], test_users["guest"]["password"])

    submit = client.post(
        FEEDBACKS_URL,
        headers=_auth(guest_token),
        json={"content": "希望增加导出功能", "contact": "wx123"},
    ).json()
    assert submit["success"] is True
    assert submit["data"]["status"] == "pending"

    mine = client.get(FEEDBACKS_MINE_URL, headers=_auth(guest_token)).json()
    assert mine["success"] is True
    contents = [f["content"] for f in mine["data"]["list"]]
    assert "希望增加导出功能" in contents


def test_feedback_empty_content_rejected(client, test_users):
    """反馈内容为空返回 success=false（参数校验）。"""
    guest_token = _login(client, test_users["guest"]["username"], test_users["guest"]["password"])
    resp = client.post(
        FEEDBACKS_URL, headers=_auth(guest_token), json={"content": "   "}
    ).json()
    assert resp["success"] is False


def test_user_feedback_isolated_per_user(client, test_users):
    """本人反馈列表仅含自己提交的反馈（数据范围隔离，需求 21.5 配套）。"""
    guest_token = _login(client, test_users["guest"]["username"], test_users["guest"]["password"])
    manager_token = _login(client, test_users["manager"]["username"], test_users["manager"]["password"])

    client.post(FEEDBACKS_URL, headers=_auth(guest_token), json={"content": "访客反馈"})
    client.post(FEEDBACKS_URL, headers=_auth(manager_token), json={"content": "管理者反馈"})

    guest_mine = client.get(FEEDBACKS_MINE_URL, headers=_auth(guest_token)).json()
    guest_contents = [f["content"] for f in guest_mine["data"]["list"]]
    assert "访客反馈" in guest_contents
    assert "管理者反馈" not in guest_contents


def test_admin_list_and_reply_feedback(client, test_users):
    """管理员查看全部反馈并处理回复（需求 21.5）。"""
    admin_token = _login(client, test_users["admin"]["username"], test_users["admin"]["password"])
    guest_token = _login(client, test_users["guest"]["username"], test_users["guest"]["password"])

    fb_id = client.post(
        FEEDBACKS_URL, headers=_auth(guest_token), json={"content": "登录页太慢"}
    ).json()["data"]["id"]

    # 管理员可见全部反馈。
    listing = client.get(FEEDBACKS_URL, headers=_auth(admin_token)).json()
    assert listing["success"] is True
    assert any(f["id"] == fb_id for f in listing["data"]["list"])

    # 管理员处理回复，状态自动置为已处理。
    reply = client.put(
        f"{FEEDBACKS_URL}/{fb_id}/reply",
        headers=_auth(admin_token),
        json={"reply": "已优化加载速度"},
    ).json()
    assert reply["success"] is True
    assert reply["data"]["reply"] == "已优化加载速度"
    assert reply["data"]["status"] == "done"


def test_non_admin_cannot_view_all_feedbacks(client, test_users):
    """非管理员访问反馈管理端列表被拒（需求 21.17）。"""
    guest_token = _login(client, test_users["guest"]["username"], test_users["guest"]["password"])
    listing = client.get(FEEDBACKS_URL, headers=_auth(guest_token)).json()
    assert listing["success"] is False
    assert listing["code"] == CODE_FORBIDDEN

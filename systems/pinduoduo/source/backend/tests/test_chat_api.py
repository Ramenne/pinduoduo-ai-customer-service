# -*- coding: utf-8 -*-
"""
backend.tests.test_chat_api —— 在线聊天接口单元测试
===================================================
本文件用途：对 backend「在线聊天」接口（app.api.routes.chat）进行单元测试，
覆盖需求 14 的核心验收场景：

- 会话列表后端分页 + 数据范围隔离（需求 14.1 / 3.7）：仅返回本人 / 被授权店铺会话，
  返回 {list,total,page,page_size}。
- 历史消息（需求 14.2）：按消息时间正序返回该会话聊天记录（北京时间）。
- 手动发送消息成功（需求 14.3）：经 websocket 下发并记消息日志、追加发出消息、
  刷新会话最近消息时间。
- 手动发送消息失败（需求 14.3）：websocket 不可用时返回失败，并记失败消息日志。
- 新消息提示（需求 14.4）：返回未读会话汇总与总未读数。
- 数据范围隔离：非管理员不可访问他人店铺会话（需求 3.7）。
- 无权限访问被拒（需求 2.4）。

测试方案：pytest + FastAPI TestClient + 内存 SQLite（夹具见 conftest.py）。
对 websocket 发送接口（chat_send_client.send_manual_message）做 monkeypatch，避免
真实 HTTP 调用。所有接口 HTTP 恒返回 200。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import chat_send_client
from common.models.log_models import ChatMessage, Conversation, MessageLog
from common.models.shop_models import Shop
from common.models.user_models import SysPermission, SysRolePermission

API_PREFIX = "/api/v1"
LOGIN_URL = f"{API_PREFIX}/login"
CONV_URL = f"{API_PREFIX}/chat/conversations"
HINTS_URL = f"{API_PREFIX}/chat/hints"


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
def chat_env(db_session, seed_permissions, test_users):
    """预置 chat 资源权限点、授权用户、店铺、会话与聊天消息。"""
    manager_id = test_users["manager"]["id"]
    role_manager_id = seed_permissions["role_manager_id"]

    # 追加 chat 资源权限点并授予 user_manager 角色（view/send）。
    for action in ("view", "send"):
        perm = SysPermission(resource_key="chat", action=action)
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

    # 会话：conv_a 属于 shop_a（manager 可见），conv_b 属于 shop_b（他人）。
    conv_a = Conversation(
        shop_pk=shop_a.id,
        customer_uid="C1",
        nickname="客户一",
        last_msg_at=datetime(2024, 1, 1, 10, 0, 0),
        unread_count=2,
    )
    conv_b = Conversation(
        shop_pk=shop_b.id,
        customer_uid="C2",
        nickname="客户二",
        last_msg_at=datetime(2024, 1, 1, 11, 0, 0),
        unread_count=5,
    )
    db_session.add_all([conv_a, conv_b])
    db_session.flush()

    # conv_a 的两条历史消息（乱序插入，验证按时间正序返回）。
    msg2 = ChatMessage(
        shop_pk=shop_a.id,
        customer_uid="C1",
        direction="in",
        msg_type="text",
        content="第二条",
        msg_time=datetime(2024, 1, 1, 10, 5, 0),
    )
    msg1 = ChatMessage(
        shop_pk=shop_a.id,
        customer_uid="C1",
        direction="in",
        msg_type="text",
        content="第一条",
        msg_time=datetime(2024, 1, 1, 10, 0, 0),
    )
    db_session.add_all([msg2, msg1])
    db_session.flush()
    db_session.commit()

    return {
        "shop_a_pk": shop_a.id,
        "shop_b_pk": shop_b.id,
        "conv_a_id": conv_a.id,
        "conv_b_id": conv_b.id,
        "manager_id": manager_id,
    }


def test_list_conversations_scope_and_pagination(client, test_users, chat_env):
    """会话列表：数据范围隔离 + 后端分页（需求 14.1 / 3.7）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])

    resp = client.get(
        CONV_URL,
        headers=_auth_header(token),
        params={"page": 1, "page_size": 10},
    ).json()
    data = resp["data"]
    assert resp["success"] is True
    # 仅可见本人店铺的 1 个会话（conv_a），不含他人的 conv_b。
    assert data["total"] == 1
    assert data["page"] == 1
    assert data["page_size"] == 10
    assert data["list"][0]["customer_uid"] == "C1"


@pytest.mark.parametrize(
    "message,expected_code",
    [("会话已过期", 42603), ("会话同步服务暂不可用，请稍后重试", 42602)],
)
def test_sync_conversations_distinguishes_shop_auth(
    client, test_users, chat_env, monkeypatch, message, expected_code
):
    """平台过期不应触发后台退出；网络故障也不能被当作授权失效。"""
    from app.services import chat_sync_client

    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    monkeypatch.setattr(
        chat_sync_client, "fetch_conversations",
        lambda **kwargs: chat_sync_client.SyncResult(ok=False, message=message),
    )
    body = client.get(
        f"{API_PREFIX}/chat/shops/{chat_env['shop_a_pk']}/sync-conversations",
        headers=_auth_header(token),
    ).json()
    assert body["success"] is False
    assert body["code"] == expected_code
    assert body["code"] != 40100


def test_sync_empty_success_and_other_owner_denied(
    client, test_users, chat_env, monkeypatch
):
    """真正空列表返回成功；他人店铺必须在触发外部查询前拒绝。"""
    from app.services import chat_sync_client

    calls = []
    def fetch(**kwargs):
        calls.append(kwargs)
        return chat_sync_client.SyncResult(ok=True, items=[])

    monkeypatch.setattr(chat_sync_client, "fetch_conversations", fetch)
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    for shop_key, success in [("shop_a_pk", True), ("shop_b_pk", False)]:
        body = client.get(
            f"{API_PREFIX}/chat/shops/{chat_env[shop_key]}/sync-conversations",
            headers=_auth_header(token),
        ).json()
        assert body["success"] is success
        if success:
            assert body["data"]["conversations"] == []
    assert len(calls) == 1


def test_list_messages_order(client, test_users, chat_env):
    """历史消息：按消息时间正序返回（需求 14.2）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    conv_a_id = chat_env["conv_a_id"]

    resp = client.get(
        f"{CONV_URL}/{conv_a_id}/messages",
        headers=_auth_header(token),
    ).json()
    data = resp["data"]
    assert resp["success"] is True
    assert data["total"] == 2
    assert [m["content"] for m in data["list"]] == ["第一条", "第二条"]


def test_send_manual_message_success(client, test_users, chat_env, monkeypatch, db_session):
    """手动发送成功：经 websocket 下发、记消息日志、追加发出消息（需求 14.3）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    conv_a_id = chat_env["conv_a_id"]

    def fake_send(shop_pk, shop_id, owner_user_id, recipient_uid, content, operation_key=None, control_revision=None):
        return chat_send_client.ManualSendResult(ok=True)

    monkeypatch.setattr(chat_send_client, "send_manual_message", fake_send)

    resp = client.post(
        f"{CONV_URL}/{conv_a_id}/send",
        headers=_auth_header(token),
        json={"content": "您好，有什么可以帮您"},
    ).json()
    assert resp["success"] is True
    assert resp["data"]["sent"] is True

    # 记录了消息日志（成功）。
    logs = db_session.query(MessageLog).all()
    assert any(log.process_result == "manual_sent" for log in logs)
    # 追加了一条「发」方向聊天消息。
    out_msgs = (
        db_session.query(ChatMessage).filter_by(direction="out").all()
    )
    assert any(m.content == "您好，有什么可以帮您" for m in out_msgs)


def test_send_manual_message_failure_records_log(
    client, test_users, chat_env, monkeypatch, db_session
):
    """手动发送失败：返回失败并记失败消息日志（需求 14.3）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    conv_a_id = chat_env["conv_a_id"]

    def fake_send(shop_pk, shop_id, owner_user_id, recipient_uid, content, operation_key=None, control_revision=None):
        return chat_send_client.ManualSendResult(ok=False, message="连接不可用")

    monkeypatch.setattr(chat_send_client, "send_manual_message", fake_send)

    resp = client.post(
        f"{CONV_URL}/{conv_a_id}/send",
        headers=_auth_header(token),
        json={"content": "测试失败"},
    ).json()
    assert resp["success"] is False

    logs = db_session.query(MessageLog).all()
    assert any(log.process_result == "manual_send_failed" for log in logs)


def test_new_message_hints(client, test_users, chat_env):
    """新消息提示：返回未读会话汇总与总未读数（需求 14.4）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])

    resp = client.get(HINTS_URL, headers=_auth_header(token)).json()
    data = resp["data"]
    assert resp["success"] is True
    # 仅本人店铺会话 conv_a 的未读（2 条），不含他人 conv_b。
    assert data["total_unread"] == 2
    assert len(data["conversations"]) == 1
    assert data["conversations"][0]["customer_uid"] == "C1"


def test_messages_data_scope_isolation(client, test_users, chat_env):
    """数据范围隔离：非管理员不可访问他人店铺会话消息（需求 3.7）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    conv_b_id = chat_env["conv_b_id"]  # 归属他人

    resp = client.get(
        f"{CONV_URL}/{conv_b_id}/messages",
        headers=_auth_header(token),
    ).json()
    assert resp["success"] is False
    # 越权统一返回「会话不存在」，不泄露存在性。
    assert resp["message"] == "会话不存在"


def test_chat_denied_for_unauthorized_user(client, test_users, chat_env):
    """无权限访问被拒：访客角色调用会话列表返回「无访问权限」（需求 2.4）。"""
    guest = test_users["guest"]
    token = _login_token(client, guest["username"], guest["password"])

    resp = client.get(CONV_URL, headers=_auth_header(token)).json()
    assert resp["success"] is False
    assert resp["code"] == CODE_FORBIDDEN
    assert resp["message"] == MSG_FORBIDDEN

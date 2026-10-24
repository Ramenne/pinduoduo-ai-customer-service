# -*- coding: utf-8 -*-
"""
backend.tests.test_chat_context_api —— 会话订单上下文接口单元测试
================================================================
本文件用途：对 backend「会话订单上下文」接口（app.api.routes.chat_context）进行
单元测试，覆盖需求 17 的核心验收场景：

- 记录订单/商品上下文随会话消息入消息日志（需求 17.1）：写入 chat_message、
  message_log 与 conversation，订单 / 商品上下文以 JSON 文本随消息存储。
- 展示会话已记录的订单与商品上下文（需求 17.3）：返回最近订单 / 商品上下文与
  携带上下文的消息列表。
- 售后状态/类型枚举入字典并展示中文文案（需求 17.4）：展示时附 *_label 中文。
- 时间以北京时间记录与展示（需求 17.5）：msg_time / last_msg_at 为北京时间。
- 数据范围隔离：非管理员不可记录 / 查看他人店铺会话（需求 3.7）。
- 无权限访问被拒（需求 2.4）。

测试方案：pytest + FastAPI TestClient + 内存 SQLite（夹具见 conftest.py）。
所有接口 HTTP 恒返回 200。
"""
from __future__ import annotations

import pytest

from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from common.models.log_models import ChatMessage, Conversation, MessageLog
from common.models.shop_models import Shop
from common.models.user_models import SysPermission, SysRolePermission
from common.services.dict_service import register_dict_initial_data

API_PREFIX = "/api/v1"
LOGIN_URL = f"{API_PREFIX}/login"
CONTEXT_URL = f"{API_PREFIX}/chat/context"


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
    """预置 chat 资源权限点、授权用户、店铺与售后枚举字典。"""
    manager_id = test_users["manager"]["id"]
    role_manager_id = seed_permissions["role_manager_id"]

    # 追加 chat 资源权限点并授予 user_manager 角色。
    for action in ("view", "create"):
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

    # 登记售后状态 / 类型等字典初始数据（需求 17.4）。
    register_dict_initial_data(db_session)
    db_session.commit()

    return {
        "shop_a_pk": shop_a.id,
        "shop_b_pk": shop_b.id,
        "manager_id": manager_id,
    }


def _record_payload(shop_pk: int) -> dict:
    """构造一条携带订单/商品上下文的记录请求体。"""
    return {
        "shop_pk": shop_pk,
        "customer_uid": "cust_1",
        "direction": "in",
        "msg_type": "order",
        "content": "我的订单什么时候发货？",
        "order_context": {
            "order_id": "O20240101",
            "goods_name": "纯棉T恤",
            "goods_id": "G1",
            "spec": "L码/白色",
            "aftersale_status": "applying",
            "aftersale_type": "refund_only",
        },
        "goods_context": {
            "goods_id": "G1",
            "goods_name": "纯棉T恤",
            "price": 59.9,
            "thumb_url": "http://img/g1.jpg",
        },
        "nickname": "小明",
        "msg_time": "2024-01-01 10:00:00",
        "process_result": "auto_reply",
    }


def test_record_context_persists_message_log(client, test_users, chat_env, db_session):
    """记录订单/商品上下文随会话消息入消息日志（需求 17.1/17.5）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    shop_pk = chat_env["shop_a_pk"]

    resp = client.post(
        CONTEXT_URL, headers=_auth_header(token), json=_record_payload(shop_pk)
    ).json()
    assert resp["success"] is True, resp
    assert resp["data"]["message_id"] is not None
    assert resp["data"]["log_id"] is not None

    # 聊天消息：订单 / 商品上下文随消息存储。
    messages = db_session.query(ChatMessage).filter_by(shop_pk=shop_pk).all()
    assert len(messages) == 1
    assert messages[0].order_context is not None
    assert messages[0].goods_context is not None
    # 时间为北京时间口径（去时区，与入参一致）。
    assert messages[0].msg_time.strftime("%Y-%m-%d %H:%M:%S") == "2024-01-01 10:00:00"

    # 消息日志：原始消息与处理结果落库（需求 17.1 / 19.5 禁止物理删除）。
    logs = db_session.query(MessageLog).filter_by(shop_pk=shop_pk).all()
    assert len(logs) == 1
    assert logs[0].process_result == "auto_reply"

    # 会话：按 (shop_pk, customer_uid) upsert，最近消息时间刷新。
    convs = db_session.query(Conversation).filter_by(shop_pk=shop_pk).all()
    assert len(convs) == 1
    assert convs[0].nickname == "小明"
    assert convs[0].last_msg_at is not None


def test_get_conversation_context_with_dict_labels(client, test_users, chat_env, db_session):
    """展示会话已记录上下文并附售后中文文案（需求 17.3/17.4）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    shop_pk = chat_env["shop_a_pk"]

    client.post(CONTEXT_URL, headers=_auth_header(token), json=_record_payload(shop_pk))
    conversation = db_session.query(Conversation).filter_by(shop_pk=shop_pk).first()

    resp = client.get(
        f"{API_PREFIX}/chat/conversations/{conversation.id}/context",
        headers=_auth_header(token),
    ).json()
    assert resp["success"] is True, resp
    data = resp["data"]

    # 展示最近订单上下文，且售后状态 / 类型转为中文文案（需求 17.4）。
    order = data["latest_order_context"]
    assert order["order_id"] == "O20240101"
    assert order["aftersale_status_label"] == "申请中"
    assert order["aftersale_type_label"] == "仅退款"

    # 展示最近商品上下文。
    goods = data["latest_goods_context"]
    assert goods["goods_id"] == "G1"
    assert goods["price"] == 59.9

    # 携带上下文的消息列表非空，时间为北京时间字符串（需求 17.5）。
    assert len(data["context_messages"]) == 1
    assert data["context_messages"][0]["msg_time"].startswith("2024-01-01T10:00:00")


def test_record_context_data_scope_isolation(client, test_users, chat_env):
    """数据范围隔离：非管理员不可记录他人店铺会话上下文（需求 3.7）。"""
    manager = test_users["manager"]
    token = _login_token(client, manager["username"], manager["password"])
    shop_b_pk = chat_env["shop_b_pk"]  # 归属他人

    resp = client.post(
        CONTEXT_URL, headers=_auth_header(token), json=_record_payload(shop_b_pk)
    ).json()
    assert resp["success"] is False
    assert resp["code"] == CODE_FORBIDDEN
    assert resp["message"] == MSG_FORBIDDEN


def test_chat_context_denied_for_unauthorized_user(client, test_users, chat_env):
    """无权限访问被拒：访客角色记录上下文返回「无访问权限」（需求 2.4）。"""
    guest = test_users["guest"]
    token = _login_token(client, guest["username"], guest["password"])

    resp = client.post(
        CONTEXT_URL,
        headers=_auth_header(token),
        json=_record_payload(chat_env["shop_a_pk"]),
    ).json()
    assert resp["success"] is False
    assert resp["code"] == CODE_FORBIDDEN

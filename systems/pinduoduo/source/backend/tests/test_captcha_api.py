# -*- coding: utf-8 -*-
"""
tests.test_captcha_api —— 登录滑块验证码接口测试
================================================
覆盖：
- GET  /captcha/status          开关查询（默认关闭）；
- POST /captcha/slider/generate 生成挑战；
- POST /captcha/slider/verify   正确/错误位移校验；
- 开启验证码后登录需携带有效票据（端到端）。
"""
from __future__ import annotations

from app.services import captcha_service, setting_service


def test_captcha_status_default_disabled(client):
    """默认未配置时登录验证码开关为关闭。"""
    resp = client.get("/api/v1/captcha/status")
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["enabled"] is False


def test_slider_generate_and_verify_success(client):
    """生成挑战后，按正确位移校验应通过并返回票据。"""
    gen = client.post("/api/v1/captcha/slider/generate").json()
    assert gen["success"] is True
    challenge_id = gen["data"]["challenge_id"]
    # 取后端记录的正确答案，模拟用户对齐
    answer = captcha_service._challenge_store[challenge_id]["answer"]
    verify = client.post(
        "/api/v1/captcha/slider/verify",
        json={"challenge_id": challenge_id, "distance": answer},
    ).json()
    assert verify["success"] is True
    assert verify["data"]["ticket"]


def test_slider_verify_wrong_distance_failed(client):
    """位移偏差超过容差应校验失败。"""
    gen = client.post("/api/v1/captcha/slider/generate").json()
    challenge_id = gen["data"]["challenge_id"]
    answer = captcha_service._challenge_store[challenge_id]["answer"]
    verify = client.post(
        "/api/v1/captcha/slider/verify",
        json={"challenge_id": challenge_id, "distance": answer + 50},
    ).json()
    assert verify["success"] is False


def test_login_requires_ticket_when_captcha_enabled(client, db_session, test_users):
    """开启登录验证码后：无票据登录被拒，携带有效票据可正常登录。"""
    admin = test_users["admin"]
    # 开启登录验证码开关
    setting_service.update_basic(db_session, enable_captcha=True)
    db_session.commit()

    # 无票据登录：被拒（提示先完成滑块验证）
    no_ticket = client.post(
        "/api/v1/login",
        json={"username": admin["username"], "password": admin["password"]},
    ).json()
    assert no_ticket["success"] is False

    # 走完滑块拿到票据
    gen = client.post("/api/v1/captcha/slider/generate").json()
    cid = gen["data"]["challenge_id"]
    answer = captcha_service._challenge_store[cid]["answer"]
    ticket = client.post(
        "/api/v1/captcha/slider/verify",
        json={"challenge_id": cid, "distance": answer},
    ).json()["data"]["ticket"]

    # 携带票据登录：成功（凭据正确时）
    ok = client.post(
        "/api/v1/login",
        json={
            "username": admin["username"],
            "password": admin["password"],
            "captcha_ticket": ticket,
        },
    ).json()
    assert ok["success"] is True
    assert ok["data"]["token"]

    # 恢复开关，避免影响其它用例
    setting_service.update_basic(db_session, enable_captcha=False)
    db_session.commit()

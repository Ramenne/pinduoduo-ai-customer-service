# -*- coding: utf-8 -*-
"""
backend 扫码登录接口测试（本轮新增）
==================================
覆盖范围（全部使用测试替身，**不接触任何真实账号、不触网**）：

- 权限：无 ``shop:create`` 权限的用户不得创建 / 绑定扫码会话；
- 归属：重新授权的目标店铺必须属于当前用户；
- 状态可区分：需人工验证 / 已过期 / 失败 必须返回**不同**业务码，不得合并成超时；
- 凭据不外泄：任何对外响应都不含 Cookie；
- 身份一致性：扫码店铺与目标店铺不一致时拒绝保存且原凭据保持不变；
- 幂等：凭据已被领取后重复绑定返回幂等成功，不重复 upsert。

websocket 侧的真实隔离与资源释放由 ``websocket/tests/test_qr_login.py`` 覆盖。
"""
from __future__ import annotations

import copy

import pytest
from common.models.shop_models import Account, Shop
from common.models.user_models import SysPermission, SysRolePermission
from app.services import qr_login_client
from app.services.qr_login_client import QrCallResult
from common.utils.crypto import encrypt_text

API_PREFIX = "/api/v1"
LOGIN_URL = f"{API_PREFIX}/login"
SESSIONS_URL = f"{API_PREFIX}/qr-login/sessions"

# 测试替身返回的扫码结果（店铺标识与凭据均为假值）。
FAKE_COOKIES = {"PASS_ID": "fake-pass-id", "webp": "false"}


def _login_token(client, username: str, password: str) -> str:
    """登录并取回访问令牌。"""
    body = client.post(LOGIN_URL, json={"username": username, "password": password}).json()
    assert body["success"] is True, body
    return body["data"]["token"]


def _auth(token: str) -> dict:
    """构造 Bearer 鉴权头。"""
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def qr_env(db_session, seed_permissions, test_users):
    """追加 shop 资源权限，并预置「归属 manager」与「归属他人」两家店铺。"""
    manager_id = test_users["manager"]["id"]
    role_manager_id = seed_permissions["role_manager_id"]

    for action in ("view", "create", "update", "disable"):
        perm = SysPermission(resource_key="shop", action=action)
        db_session.add(perm)
        db_session.flush()
        db_session.add(SysRolePermission(role_id=role_manager_id, permission_id=perm.id))

    mine = Shop(shop_id="pdd_mine", shop_name="我的店铺", owner_user_id=manager_id, status=1)
    theirs = Shop(shop_id="pdd_theirs", shop_name="别人的店铺", owner_user_id=999999, status=1)
    db_session.add_all([mine, theirs])
    db_session.flush()
    db_session.commit()

    return {
        "manager_id": manager_id,
        "mine_shop_id": mine.shop_id,
        "mine_pk": mine.id,
        "theirs_shop_id": theirs.shop_id,
        "users": test_users,
    }


class FakeQrClient:
    """qr_login_client 的测试替身：记录调用入参，按脚本返回预设结果。"""

    def __init__(self):
        self.calls = []
        self.create_result = (True, {"session_id": "s-1", "state": "waiting",
                                     "qr_image_base64": "AAEC", "owner_user_id": None})
        self.get_result = (True, {"session_id": "s-1", "state": "waiting",
                                  "owner_user_id": None, "target_shop_id": None})
        self.claim_result = (True, {"cookies": copy.deepcopy(FAKE_COOKIES),
                                    "shop_id": "pdd_new", "shop_name": "新扫到的店铺"})
        self.cancel_result = (True, "")
        self.get_sequence = []
        self.get_sequence = []

    def create_session(self, owner_user_id, target_shop_id):
        self.calls.append(("create", owner_user_id, target_shop_id))
        ok, data = self.create_result
        return QrCallResult(ok=ok, data=data, message="" if ok else "扫码登录服务繁忙")

    def get_session(self, session_id, owner_user_id):
        self.calls.append(("get", session_id, owner_user_id))
        # get_sequence 非空时按次序返回，用于模拟"两次查询之间状态被别的请求改掉"。
        if self.get_sequence:
            ok, data = self.get_sequence.pop(0)
        else:
            ok, data = self.get_result
        data = dict(data)
        data.setdefault("session_id", session_id)
        return QrCallResult(ok=ok, data=data, message="" if ok else "会话不存在或已结束")

    def claim_session(self, session_id, owner_user_id):
        self.calls.append(("claim", session_id, owner_user_id))
        ok, data = self.claim_result
        return QrCallResult(ok=ok, data=data, message="" if ok else "该扫码会话的凭据已被领取，请重新发起扫码")

    def cancel_session(self, session_id, owner_user_id):
        self.calls.append(("cancel", session_id, owner_user_id))
        ok, message = self.cancel_result
        return QrCallResult(ok=ok, data={}, message=message or ("" if ok else "会话已结束"))


@pytest.fixture()
def fake_client(monkeypatch, qr_env):
    """把 qr_login_client 的四个函数换成替身（参照 test_products_api 的 stub 口径）。"""
    fake = FakeQrClient()
    monkeypatch.setattr(qr_login_client, "create_session", fake.create_session)
    monkeypatch.setattr(qr_login_client, "get_session", fake.get_session)
    monkeypatch.setattr(qr_login_client, "claim_session", fake.claim_session)
    monkeypatch.setattr(qr_login_client, "cancel_session", fake.cancel_session)
    fake.users = qr_env["users"]
    fake.env = qr_env
    return fake


def test_create_requires_permission(client, db_session, qr_env):
    """无 shop:create 权限的用户不得创建扫码会话（需求 2.4）。"""
    token = _login_token(client, qr_env["users"]["guest"]["username"],
                         qr_env["users"]["guest"]["password"])
    body = client.post(SESSIONS_URL, json={}, headers=_auth(token)).json()
    assert body["success"] is False
    assert body["code"] == 40300


def test_create_binds_owner_and_never_returns_cookies(client, fake_client):
    """创建时绑定发起用户，且响应体不含任何 Cookie 字段。"""
    token = _login_token(client, fake_client.users["manager"]["username"],
                         fake_client.users["manager"]["password"])
    body = client.post(SESSIONS_URL, json={}, headers=_auth(token)).json()
    assert body["success"] is True, body
    # 归属参数确实传给了 websocket 侧。
    assert ("create", fake_client.env["manager_id"], None) in fake_client.calls
    assert "cookie" not in body["data"]
    assert "cookies" not in body["data"]


def test_reauth_target_shop_must_be_own(client, fake_client):
    """重新授权时目标店铺不属于当前用户 → 按「不存在」拒绝，且不发起扫码。"""
    token = _login_token(client, fake_client.users["manager"]["username"],
                         fake_client.users["manager"]["password"])
    body = client.post(
        SESSIONS_URL,
        json={"target_shop_id": fake_client.env["theirs_shop_id"]},
        headers=_auth(token),
    ).json()
    assert body["success"] is False
    assert body["code"] == 40400
    assert not any(c[0] == "create" for c in fake_client.calls)


def test_get_never_errors_on_states_so_frontend_keeps_polling(client, fake_client):
    """状态查询一律 success，由 terminal 决定是否停表。

    关键行为：verification_required 期间后端仍在等用户本人完成验证，若这里返回
    业务错误，前端会停表、用户就失去继续的机会。
    """
    token = _login_token(client, fake_client.users["manager"]["username"],
                         fake_client.users["manager"]["password"])

    fake_client.get_result = (True, {"state": "verification_required", "terminal": False,
                                     "message": "请在服务器可见浏览器窗口完成验证"})
    body = client.get(f"{SESSIONS_URL}/s-1", headers=_auth(token)).json()
    assert body["success"] is True, body
    assert body["data"]["state"] == "verification_required"
    assert body["data"]["terminal"] is False
    assert "cookie" not in body["data"] and "cookies" not in body["data"]

    fake_client.get_result = (True, {"state": "expired", "terminal": True,
                                     "message": "登录二维码已失效"})
    body = client.get(f"{SESSIONS_URL}/s-1", headers=_auth(token)).json()
    assert body["success"] is True
    assert body["data"]["terminal"] is True


def test_bind_maps_terminal_states_to_distinct_codes(client, fake_client):
    """绑定时的失败类终态必须映射成互不相同的业务码。"""
    token = _login_token(client, fake_client.users["manager"]["username"],
                         fake_client.users["manager"]["password"])

    seen = set()
    for state, expected in (("verification_required", 42604), ("expired", 42605),
                            ("failed", 42602)):
        fake_client.get_result = (True, {"state": state, "terminal": True})
        body = client.post(f"{SESSIONS_URL}/s-1/bind", headers=_auth(token)).json()
        assert body["success"] is False
        assert body["code"] == expected, (state, body)
        seen.add(expected)
    # 三者不得合并成同一个码。
    assert len(seen) == 3


def test_get_passes_current_user_as_owner(client, fake_client):
    """查询必须把当前用户作为归属传给 websocket，由其做严格比对。"""
    token = _login_token(client, fake_client.users["manager"]["username"],
                         fake_client.users["manager"]["password"])
    client.get(f"{SESSIONS_URL}/s-1", headers=_auth(token))
    assert ("get", "s-1", fake_client.env["manager_id"]) in fake_client.calls


def test_get_rejects_foreign_owner_in_payload(client, fake_client):
    """上游若返回他人归属，backend 必须二次拦截为「不存在」。"""
    token = _login_token(client, fake_client.users["manager"]["username"],
                         fake_client.users["manager"]["password"])
    fake_client.get_result = (True, {"state": "waiting", "terminal": False,
                                     "owner_user_id": 555555})
    body = client.get(f"{SESSIONS_URL}/s-1", headers=_auth(token)).json()
    assert body["success"] is False
    assert body["code"] == 40400


def test_bind_requires_confirmed_state(client, fake_client):
    """未完成确认时不得绑定（防止把只读轮询变成写操作）。"""
    token = _login_token(client, fake_client.users["manager"]["username"],
                         fake_client.users["manager"]["password"])
    fake_client.get_result = (True, {"state": "waiting"})
    body = client.post(f"{SESSIONS_URL}/s-1/bind", headers=_auth(token)).json()
    assert body["success"] is False
    assert not any(c[0] == "claim" for c in fake_client.calls)


def test_bind_creates_shop_for_new_authorization(client, fake_client, db_session):
    """新授权：扫码到未绑定过的店铺时按既有 upsert 口径落库并加密存 Cookie。"""
    token = _login_token(client, fake_client.users["manager"]["username"],
                         fake_client.users["manager"]["password"])
    fake_client.get_result = (True, {"state": "confirmed", "target_shop_id": None})
    body = client.post(f"{SESSIONS_URL}/s-1/bind", headers=_auth(token)).json()
    assert body["success"] is True, body
    shop = db_session.query(Shop).filter_by(shop_id="pdd_new").first()
    assert shop is not None
    assert shop.owner_user_id == fake_client.env["manager_id"]
    account = db_session.query(Account).filter_by(shop_pk=shop.id).first()
    assert account is not None
    # 凭据必须是密文，不得等于明文假值。
    assert account.cookies_enc and FAKE_COOKIES["PASS_ID"] not in account.cookies_enc


def test_bind_rejects_wrong_shop_and_keeps_original_credential(client, fake_client, db_session):
    """扫码店铺与目标店铺不一致 → 42606，且原凭据一个字节都不许变。"""
    manager_id = fake_client.env["manager_id"]
    existing = Account(
        shop_pk=fake_client.env["mine_pk"],
        user_id=manager_id,
        username="old-user",
        cookies_enc=encrypt_text('{"PASS_ID": "old-cookie"}'),
    )
    db_session.add(existing)
    db_session.flush()
    db_session.commit()
    before = existing.cookies_enc

    fake_client.get_result = (True, {"state": "confirmed",
                                     "target_shop_id": fake_client.env["mine_shop_id"]})
    fake_client.claim_result = (True, {"cookies": copy.deepcopy(FAKE_COOKIES),
                                       "shop_id": "pdd_someone_elses_shop"})

    token = _login_token(client, fake_client.users["manager"]["username"],
                         fake_client.users["manager"]["password"])
    body = client.post(f"{SESSIONS_URL}/s-1/bind", headers=_auth(token)).json()
    assert body["success"] is False
    assert body["code"] == 42606

    db_session.expire_all()
    unchanged = db_session.query(Account).filter_by(shop_pk=fake_client.env["mine_pk"]).first()
    assert unchanged is not None and unchanged.cookies_enc == before


def test_bind_is_idempotent_after_claim(client, fake_client, db_session):
    """凭据已领取过且店铺已在名下 → 返回幂等成功，不重复 upsert、不重复领取。"""
    mine_shop_id = fake_client.env["mine_shop_id"]
    original_cookies_enc = encrypt_text('{"PASS_ID": "old-cookie"}')
    db_session.add(Account(
        shop_pk=fake_client.env["mine_pk"],
        user_id=fake_client.env["manager_id"],
        username="old-user",
        cookies_enc=original_cookies_enc,
    ))
    db_session.flush()
    db_session.commit()

    fake_client.get_result = (True, {"state": "claimed", "target_shop_id": mine_shop_id})
    fake_client.claim_result = (False, None)

    token = _login_token(client, fake_client.users["manager"]["username"],
                         fake_client.users["manager"]["password"])
    body = client.post(f"{SESSIONS_URL}/s-1/bind", headers=_auth(token)).json()
    assert body["success"] is True, body
    assert "未重复更改" in body["message"]
    # 原凭据保持密文原样，未被改写。
    account = db_session.query(Account).filter_by(shop_pk=fake_client.env["mine_pk"]).first()
    assert account is not None and account.cookies_enc == original_cookies_enc
    # 状态为 claimed 时不应再走领取，避免重复消费凭据。
    assert not any(c[0] == "claim" for c in fake_client.calls)


def test_bind_idempotent_uses_scanned_shop_id_when_no_target(client, fake_client, db_session):
    """新建绑定（无 target_shop_id）重复调用时，用会话上的扫码结果兜底定位店铺。"""
    db_session.add(Shop(shop_id="pdd_new", shop_name="新扫到的店铺",
                        owner_user_id=fake_client.env["manager_id"], status=1))
    db_session.flush()
    db_session.commit()

    fake_client.get_result = (True, {"state": "claimed", "target_shop_id": None,
                                     "shop_id": "pdd_new", "terminal": True})
    token = _login_token(client, fake_client.users["manager"]["username"],
                         fake_client.users["manager"]["password"])
    body = client.post(f"{SESSIONS_URL}/s-1/bind", headers=_auth(token)).json()
    assert body["success"] is True, body
    assert "未重复更改" in body["message"]


def test_bind_without_credentials_does_not_touch_store(client, fake_client, db_session):
    """领取成功但缺店铺标识时不得写库（防止用空凭据覆盖已有授权）。"""
    token = _login_token(client, fake_client.users["manager"]["username"],
                         fake_client.users["manager"]["password"])
    fake_client.get_result = (True, {"state": "confirmed", "target_shop_id": None})
    fake_client.claim_result = (True, {"cookies": {}, "shop_id": ""})
    body = client.post(f"{SESSIONS_URL}/s-1/bind", headers=_auth(token)).json()
    assert body["success"] is False
    assert db_session.query(Shop).filter_by(shop_id="").first() is None


def test_cancel_passes_owner_to_websocket(client, fake_client):
    """取消请求把当前用户作为归属传给 websocket，由其做严格比对。"""
    token = _login_token(client, fake_client.users["manager"]["username"],
                         fake_client.users["manager"]["password"])
    body = client.delete(f"{SESSIONS_URL}/s-1", headers=_auth(token)).json()
    assert body["success"] is True, body
    assert ("cancel", "s-1", fake_client.env["manager_id"]) in fake_client.calls


def test_concurrent_second_bind_is_idempotent(client, fake_client, db_session):
    """两个 bind 并发时，第二个在 claim 失败后应复查状态并给幂等成功，不重复落库。

    真实竞态里第一个请求已经把店铺建好了，所以这里预置 pdd_race 来还原那个时刻。
    """
    race_shop = Shop(shop_id="pdd_race", shop_name="竞态店铺",
                     owner_user_id=fake_client.env["manager_id"], status=1)
    db_session.add(race_shop)
    db_session.flush()
    db_session.commit()

    token = _login_token(client, fake_client.users["manager"]["username"],
                         fake_client.users["manager"]["password"])
    # 第一次 get 看到 confirmed，claim 失败（被另一个请求抢先领取），复查 get 看到 claimed。
    fake_client.get_sequence = [
        (True, {"state": "confirmed", "terminal": False, "target_shop_id": None}),
        (True, {"state": "claimed", "terminal": True, "shop_id": "pdd_race",
                "target_shop_id": None}),
    ]
    fake_client.claim_result = (False, None)

    body = client.post(f"{SESSIONS_URL}/s-1/bind", headers=_auth(token)).json()
    assert body["success"] is True, body
    assert "未重复更改" in body["message"]
    # 关键：没有第二次落库，店铺记录数仍为 1，且没有新建 account 凭据。
    assert db_session.query(Shop).filter_by(shop_id="pdd_race").count() == 1
    assert db_session.query(Account).filter_by(shop_pk=race_shop.id).count() == 0


def test_bind_after_cancel_does_not_write(client, fake_client, db_session):
    """会话已被取消后不得绑定成功，也不得写库。"""
    token = _login_token(client, fake_client.users["manager"]["username"],
                         fake_client.users["manager"]["password"])
    fake_client.get_result = (True, {"state": "cancelled", "terminal": True})
    body = client.post(f"{SESSIONS_URL}/s-1/bind", headers=_auth(token)).json()
    assert body["success"] is False
    assert body["code"] == 42605
    assert not any(c[0] == "claim" for c in fake_client.calls)


def test_verification_window_uses_authenticated_owner(client, test_users, qr_env, monkeypatch):
    calls=[]
    def invoke(method,path,**kwargs):
        calls.append((method,path,kwargs))
        return QrCallResult(ok=True,data={'image':'fake-image','width':1440,'height':900})
    monkeypatch.setattr(qr_login_client,'_invoke_json',invoke)
    user=test_users['manager']
    token=_login_token(client,user['username'],user['password'])
    response=client.get(SESSIONS_URL+'/sample/frame',headers=_auth(token))
    assert response.json()['success']
    assert response.headers['cache-control']=='no-store'
    assert calls[0][2]['params']['owner_user_id']==user['id']
    body=client.post(SESSIONS_URL+'/sample/input',headers=_auth(token),json={'kind':'up','x':0.2,'y':0.3,'owner_user_id':999}).json()
    assert body['success']
    assert calls[-1][2]['payload']['owner_user_id']==user['id']
    wheel=client.post(SESSIONS_URL+'/sample/input',headers=_auth(token),json={
        'kind':'wheel','x':0.2,'y':0.3,'delta_x':10,'delta_y':200,'owner_user_id':999})
    assert wheel.json()['success']
    assert calls[-1][2]['payload']['delta_y']==200
    assert calls[-1][2]['payload']['delta_x']==10
    assert calls[-1][2]['payload']['owner_user_id']==user['id']
    calls.clear()
    body=client.get(SESSIONS_URL+'/sample/frame').json()
    assert not body['success'] and not calls


def test_admin_can_reauthorize_visible_member_shop_without_duplicate(client, fake_client, db_session, seed_permissions):
    # 管理员的数据范围不限，但接口操作权限仍显式授予。
    for perm in db_session.query(SysPermission).filter(SysPermission.resource_key == "shop").all():
        db_session.add(SysRolePermission(role_id=seed_permissions["role_admin_id"], permission_id=perm.id))
    db_session.commit()
    admin = fake_client.users["admin"]
    token = _login_token(client, admin["username"], admin["password"])
    target = fake_client.env["mine_shop_id"]
    body = client.post(SESSIONS_URL, json={"target_shop_id": target}, headers=_auth(token)).json()
    assert body["success"] is True, body
    fake_client.get_result = (True, {"state": "confirmed", "owner_user_id": admin["id"], "target_shop_id": target})
    fake_client.claim_result = (True, {"shop_id": target, "cookies": copy.deepcopy(FAKE_COOKIES)})
    body = client.post(SESSIONS_URL + "/s-1/bind", headers=_auth(token)).json()
    assert body["success"] is True, body
    assert body["data"]["id"] == fake_client.env["mine_pk"]
    assert body["data"]["owner_user_id"] == fake_client.env["manager_id"]
    assert len(db_session.query(Shop).filter(Shop.shop_id == target).all()) == 1


def test_password_create_is_short_owned_request_and_never_echoes_password(client, fake_client, monkeypatch):
    calls=[]
    def create(owner, target, username, password, remark):
        calls.append((owner,target,username,password,remark))
        return QrCallResult(ok=True,data={'session_id':'pw-1','login_mode':'password','state':'waiting',
             'password':'must-not-leak','cookies':{'PASS_ID':'must-not-leak'}})
    monkeypatch.setattr(qr_login_client,'create_password_session',create)
    token=_login_token(client,fake_client.users['manager']['username'],fake_client.users['manager']['password'])
    body=client.post(SESSIONS_URL,json={'login_mode':'password','username':'fixture-user',
                       'password':'fixture-secret','remark':'fixture'},headers=_auth(token)).json()
    assert body['success'],body
    assert calls==[(fake_client.env['manager_id'],None,'fixture-user','fixture-secret','fixture')]
    assert body['data']['login_mode']=='password'
    assert 'password' not in body['data'] and 'cookies' not in body['data']
    assert 'fixture-secret' not in str(body) and 'must-not-leak' not in str(body)


def test_password_bind_encrypts_password_and_preserves_public_response(client, db_session, fake_client, monkeypatch):
    from common.utils.crypto import decrypt_text
    fake_client.get_result=(True, {'session_id':'pw-1','state':'confirmed','login_mode':'password',
            'owner_user_id':fake_client.env['manager_id']})
    fake_client.claim_result=(True,{'shop_id':'password_new','shop_name':'Fixture',
          'username':'fixture-user','password':'fixture-secret','remark':'fixture',
          'cookies':copy.deepcopy(FAKE_COOKIES)})
    # 落库后禁止测试触发真实连接。
    from app.services import account_service
    monkeypatch.setattr(account_service,'notify_connect',lambda *args,**kwargs:None)
    token=_login_token(client,fake_client.users['manager']['username'],fake_client.users['manager']['password'])
    body=client.post(f'{SESSIONS_URL}/pw-1/bind',headers=_auth(token)).json()
    assert body['success'],body
    account=db_session.query(Account).filter(Account.username=='fixture-user').one()
    assert decrypt_text(account.password_enc)=='fixture-secret'
    assert account.password_enc!='fixture-secret'
    assert 'fixture-secret' not in str(body) and 'fake-pass-id' not in str(body)


def test_sms_mapping_uses_current_owner_and_does_not_echo_code(client,fake_client,monkeypatch):
    calls=[]
    def invoke(method,path,**kw):
        calls.append((method,path,kw))
        return QrCallResult(ok=True,data={})
    monkeypatch.setattr(qr_login_client,'_invoke_json',invoke)
    token=_login_token(client,fake_client.users['manager']['username'],fake_client.users['manager']['password'])
    body=client.post(f'{SESSIONS_URL}/pw-1/verification',json={'action':'submit_sms','code':'123456','owner_user_id':999},headers=_auth(token)).json()
    assert body['success'] and '123456' not in str(body)
    payload=calls[0][2]['payload']
    assert payload=={'owner_user_id':fake_client.env['manager_id'],'action':'submit_sms','code':'123456'}


def test_sms_metadata_is_public_but_credentials_are_not(client,fake_client):
    fake_client.get_result=(True,{'session_id':'pw-1','state':'verification_required','login_mode':'password',
        'owner_user_id':fake_client.env['manager_id'],'verification':{'type':'sms','masked_mobile':'195****8667','can_resend':False,'cooldown_seconds':19},
        'password':'never-return-fixture','code':'never-return-fixture'})
    token=_login_token(client,fake_client.users['manager']['username'],fake_client.users['manager']['password'])
    body=client.get(f'{SESSIONS_URL}/pw-1',headers=_auth(token)).json()
    assert body['success'] and body['data']['verification']['masked_mobile']=='195****8667'
    assert 'never-return-fixture' not in str(body)

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from conftest import (
    account,
    controller_headers,
    csrf_from_html,
    device_payload,
    enroll,
    public_key_text,
    sign,
)
from oin_control_plane.security import enrollment_message
from oin_control_plane.protocol import normalize_device_payload


def test_registered_account_survives_server_reopen_and_can_login(app):
    from oin_control_plane.app import create_app
    password = "registration-persistence-test-password"
    with TestClient(app, base_url="https://testserver") as first:
        for route in ['/login', '/register']:
            html = first.get(route).text
            assert '已有账号，登录' in html and '没有账号，注册' in html
        created = first.post('/api/v1/accounts', json={
            'username':'signup-persist', 'email':'signup@example.test', 'password':password})
        assert created.status_code == 201
        ident = created.json()['account']['id']
        assert password not in created.text and 'password_hash' not in created.text
        duplicate = first.post('/api/v1/accounts', json={'username':'signup-persist','password':password})
        assert duplicate.status_code == 409
    # A fresh application/store reads the same durable database, not an in-memory account list.
    with TestClient(create_app(app.state.settings), base_url="https://testserver") as reopened:
        login = reopened.post('/api/v1/mobile/sessions', json={'identifier':'signup@example.test','password':password})
        assert login.status_code == 200 and login.json()['account']['id'] == ident
        token = login.json()['access_token']
        me = reopened.get('/api/v1/accounts/me', headers={'Authorization':'Bearer '+token})
        assert me.status_code == 200 and me.json()['account']['id'] == ident
    with app.state.database.connect() as connection:
        row = connection.execute('SELECT password_hash FROM accounts WHERE id=?',(ident,)).fetchone()
        assert row['password_hash'] != password


def test_web_register_login_logout_and_download(client: TestClient, app, monkeypatch) -> None:
    page = client.get("/register")
    assert page.status_code == 200
    csrf = csrf_from_html(page.text)
    registered = client.post(
        "/register",
        data={
            "csrf": csrf,
            "username": "web-user",
            "email": "web-user@example.test",
            "password": "correct horse battery",
        },
        follow_redirects=False,
    )
    assert registered.status_code == 303
    assert registered.headers["location"] == "/devices"
    devices = client.get("/devices")
    assert devices.status_code == 200
    assert "尚无已绑定设备" in devices.text

    download = client.get("/download")
    assert download.status_code == 200
    assert "0.1.0-test" in download.text
    assert "直接下载 APK" in download.text
    assert "扫码后直接下载同一 APK，不经过中间页面" in download.text
    assert "复制下载链接" not in download.text
    assert "apk-download-url" not in download.text
    assert "/download/apk" in download.text
    apk = client.get("/download/apk")
    assert apk.status_code == 200
    assert apk.content == b"test-apk-not-installable"

    qr_target: dict[str, str] = {}

    class FakeQr:
        def save(self, stream) -> None:
            stream.write(b"<svg></svg>")

    def fake_qr_make(value: str, **_kwargs):
        qr_target["value"] = value
        return FakeQr()

    monkeypatch.setattr("oin_control_plane.app.qrcode.make", fake_qr_make)
    qr = client.get("/download/qr.svg")
    assert qr.status_code == 200
    assert b"<svg" in qr.content
    assert qr_target["value"] == "https://testserver/download/apk"

    token = client.cookies.get("oin_session")
    session = app.state.store.get_web_session(token)
    assert session is not None
    logged_out = client.post(
        "/logout", data={"csrf": session.csrf_token}, follow_redirects=False
    )
    assert logged_out.status_code == 303
    assert app.state.store.get_web_session(token) is None

    login_page = client.get("/login")
    login_csrf = csrf_from_html(login_page.text)
    wrong = client.post(
        "/login",
        data={"csrf": login_csrf, "identifier": "web-user", "password": "wrong-password-value"},
    )
    assert wrong.status_code == 401
    assert "密码错误" in wrong.text


def test_device_signature_repetition_and_owner_isolation(client: TestClient, app) -> None:
    owner = account(client, "owner")
    stranger = account(client, "stranger")
    private_key = ec.generate_private_key(ec.SECP256R1())
    first = enroll(client, owner["token"], private_key=private_key)
    device_id = first["device"]["id"]
    assert first["registration"] == "created"

    repeated = enroll(client, owner["token"], private_key=private_key, payload=device_payload("Renamed"))
    assert repeated["registration"] == "existing"
    assert repeated["device"]["id"] == device_id
    assert repeated["device"]["display_name"] == "Renamed"

    unauthenticated = client.get("/api/v1/devices")
    assert unauthenticated.status_code == 401
    mobile_list = client.get(
        "/api/v1/devices", headers={"Authorization": f"Bearer {owner['token']}"}
    )
    assert mobile_list.status_code == 401
    owner_list = client.get("/api/v1/devices", headers=controller_headers(app, owner))
    assert owner_list.status_code == 200
    assert [item["id"] for item in owner_list.json()["devices"]] == [device_id]
    cross_account = client.get(
        f"/api/v1/devices/{device_id}",
        headers=controller_headers(app, stranger),
    )
    assert cross_account.status_code == 404

    stranger_headers = {"Authorization": f"Bearer {stranger['token']}"}
    challenge_response = client.post(
        "/api/v1/device-enrollment-challenges",
        json={"public_key": public_key_text(private_key)},
        headers=stranger_headers,
    )
    challenge = challenge_response.json()
    payload = device_payload("Stolen")
    conflict = client.post(
        "/api/v1/devices",
        json={
            "challenge_id": challenge["challenge_id"],
            "payload": payload,
            "signature": sign(
                private_key,
                enrollment_message(challenge["challenge_id"], challenge["nonce"], payload),
            ),
        },
        headers=stranger_headers,
    )
    assert conflict.status_code == 409


def test_device_page_labels_stale_snapshots_and_front_app_read_only_boundary(
    client: TestClient, app
) -> None:
    owner = account(client, "truthful-device-page")
    payload = device_payload("Redmi test phone")
    payload["manufacturer"] = "Xiaomi"
    payload["model"] = "2312DRA50C"
    enrolled = enroll(client, owner["token"], payload=payload)
    device_id = enrolled["device"]["id"]
    page = client.get(
        f"/devices/{device_id}", headers=controller_headers(app, owner)
    )
    assert page.status_code == 200
    assert "最后成功手机快照" in page.text
    assert "WS/心跳中断时，手机后续修改不会显示" in page.text
    assert "snapshot 本身只读" in page.text
    assert "不允许任意应用或页面内业务点击" in page.text
    assert "闲鱼前台节点读取的唯一必需权限是无障碍" in page.text
    assert "后台存活稳定性建议" in page.text
    assert "不能写成内容读取必要授权" in page.text
    assert "必须人工开启自启动并锁定最近任务" not in page.text
    assert "设备当前离线" in page.text
    assert "运行 health" not in page.text


def test_invalid_signature_is_rejected(client: TestClient, app) -> None:
    owner = account(client, "signature-owner")
    private_key = ec.generate_private_key(ec.SECP256R1())
    other_key = ec.generate_private_key(ec.SECP256R1())
    headers = {"Authorization": f"Bearer {owner['token']}"}
    challenge = client.post(
        "/api/v1/device-enrollment-challenges",
        json={"public_key": public_key_text(private_key)},
        headers=headers,
    ).json()
    payload = device_payload()
    response = client.post(
        "/api/v1/devices",
        json={
            "challenge_id": challenge["challenge_id"],
            "payload": payload,
            "signature": sign(
                other_key,
                enrollment_message(challenge["challenge_id"], challenge["nonce"], payload),
            ),
        },
        headers=headers,
    )
    assert response.status_code == 401
    assert client.get(
        "/api/v1/devices", headers=controller_headers(app, owner)
    ).json()["devices"] == []
    with app.state.database.connect() as connection:
        audit = connection.execute(
            "SELECT action, success, detail_json FROM audit_logs "
            "WHERE account_id = ? ORDER BY id DESC LIMIT 1",
            (owner["account"]["id"],),
        ).fetchone()
    assert audit["action"] == "device_enrollment_rejected"
    assert audit["success"] == 0
    assert "invalid_device_signature" in audit["detail_json"]

    correct_retry = client.post(
        "/api/v1/devices",
        json={
            "challenge_id": challenge["challenge_id"],
            "payload": payload,
            "signature": sign(
                private_key,
                enrollment_message(challenge["challenge_id"], challenge["nonce"], payload),
            ),
        },
        headers=headers,
    )
    assert correct_retry.status_code == 401


def test_enrollment_payload_must_be_normalized_and_challenge_is_fresh_single_use(
    client: TestClient, app
) -> None:
    owner = account(client, "challenge-owner")
    private_key = ec.generate_private_key(ec.SECP256R1())
    headers = {"Authorization": f"Bearer {owner['token']}"}
    raw = device_payload("  ＯＩＮ / phone  ")
    raw["timezone"] = " Asia/Shanghai "
    raw["capabilities"]["cloud_task_types"] = [
        "xianyu_snapshot",
        "health",
        "health",
        "observe",
    ]
    normalized = normalize_device_payload(raw)

    challenge = client.post(
        "/api/v1/device-enrollment-challenges",
        json={"public_key": public_key_text(private_key)},
        headers=headers,
    ).json()
    raw_signature = sign(
        private_key,
        enrollment_message(challenge["challenge_id"], challenge["nonce"], normalized),
    )
    rejected_raw = client.post(
        "/api/v1/devices",
        json={
            "challenge_id": challenge["challenge_id"],
            "payload": raw,
            "signature": raw_signature,
        },
        headers=headers,
    )
    assert rejected_raw.status_code == 422

    request = {
        "challenge_id": challenge["challenge_id"],
        "payload": normalized,
        "signature": raw_signature,
    }
    created = client.post("/api/v1/devices", json=request, headers=headers)
    assert created.status_code == 201
    reused = client.post("/api/v1/devices", json=request, headers=headers)
    assert reused.status_code == 401

    expired = client.post(
        "/api/v1/device-enrollment-challenges",
        json={"public_key": public_key_text(private_key)},
        headers=headers,
    ).json()
    with app.state.database.connect() as connection:
        connection.execute(
            "UPDATE enrollment_challenges SET expires_at = 1 WHERE id = ?",
            (expired["challenge_id"],),
        )
    expired_request = {
        "challenge_id": expired["challenge_id"],
        "payload": normalized,
        "signature": sign(
            private_key,
            enrollment_message(expired["challenge_id"], expired["nonce"], normalized),
        ),
    }
    assert client.post("/api/v1/devices", json=expired_request, headers=headers).status_code == 401

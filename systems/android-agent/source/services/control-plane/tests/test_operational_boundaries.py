from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import account, enroll
from oin_control_plane.app import create_app
from oin_control_plane.config import Settings
from oin_control_plane.database import Database


PRODUCTION_TEST_DATABASE_URL = (
    "postgresql://oin_v01_app:test@127.0.0.1:5432/oin_v01"
)


def _create_production_app_with_sqlite_test_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, settings: Settings
):
    database = Database(tmp_path / "production-boundary-test.db")
    database.migrate()
    monkeypatch.setattr(
        "oin_control_plane.app.Database", lambda *_, **_kwargs: database
    )
    return create_app(settings)


def test_production_trusted_host_accepts_only_approved_host_with_public_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        database_path=tmp_path / "production-host.db",
        database_url=PRODUCTION_TEST_DATABASE_URL,
        database_auto_migrate=False,
        public_base_url="https://approved.example/oin",
        apk_path=None,
        apk_version="unpublished",
        environment="production",
        password_iterations=100_000,
    )
    production_app = _create_production_app_with_sqlite_test_backend(
        monkeypatch, tmp_path, settings
    )
    with TestClient(production_app, base_url="http://127.0.0.1") as production_client:
        assert production_client.get("/healthz").status_code == 400
        accepted = production_client.get(
            "/healthz", headers={"Host": "approved.example"}
        )
        assert accepted.status_code == 200
        assert accepted.json()["ok"] is True
    assert settings.browser_path("/healthz") == "/oin/healthz"


def test_production_internal_http_requires_exact_explicit_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = {
        "database_path": tmp_path / "internal-http.db",
        "database_url": PRODUCTION_TEST_DATABASE_URL,
        "database_auto_migrate": False,
        "public_base_url": "http://dominolif.com/oin",
        "apk_path": None,
        "apk_version": "unpublished",
        "environment": "production",
        "password_iterations": 100_000,
    }
    with pytest.raises(ValueError, match="explicit restricted internal HTTP opt-in"):
        Settings(**values)
    with pytest.raises(ValueError, match="restricted to"):
        Settings(
            **{
                **values,
                "public_base_url": "http://other.example/oin",
                "internal_http_enabled": True,
            }
        )

    settings = Settings(**values, internal_http_enabled=True)
    assert settings.transport_mode == "internal_http"
    internal_app = _create_production_app_with_sqlite_test_backend(
        monkeypatch, tmp_path, settings
    )
    with TestClient(internal_app, base_url="http://dominolif.com") as internal_client:
        health = internal_client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["public_base_url"] == "http://dominolif.com/oin"
        assert health.json()["transport_mode"] == "internal_http"
        assert "strict-transport-security" not in health.headers


def test_dispatch_freshness_must_precede_heartbeat_timeout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="lower than the heartbeat timeout"):
        Settings(
            database_path=tmp_path / "freshness.db",
            public_base_url="https://testserver",
            apk_path=None,
            apk_version="unpublished",
            heartbeat_timeout_seconds=50,
            dispatch_freshness_seconds=50,
        )


def test_http_json_rejects_duplicate_fields_floats_and_out_of_range_integers(
    client: TestClient,
) -> None:
    headers = {"Content-Type": "application/json"}
    duplicate = client.post(
        "/api/v1/mobile/sessions",
        content='{"identifier":"first","identifier":"second","password":"long-enough-password"}',
        headers=headers,
    )
    floating = client.post(
        "/api/v1/mobile/sessions",
        content='{"identifier":1.5,"password":"long-enough-password"}',
        headers=headers,
    )
    oversized = client.post(
        "/api/v1/mobile/sessions",
        content='{"identifier":9223372036854775808,"password":"long-enough-password"}',
        headers=headers,
    )
    assert duplicate.status_code == 422
    assert floating.status_code == 422
    assert oversized.status_code == 422


def test_http_middleware_periodically_cleans_all_expired_session_and_challenge_rows(
    client: TestClient, app
) -> None:
    owner = account(client, "cleanup-owner")
    enrolled = enroll(client, owner["token"])
    device_id = enrolled["device"]["id"]
    account_id = owner["account"]["id"]
    with app.state.database.connect() as connection:
        credential_id = connection.execute(
            "SELECT id FROM device_credentials WHERE device_id = ? AND revoked_at IS NULL",
            (device_id,),
        ).fetchone()["id"]
        connection.execute(
            "INSERT INTO preauth_sessions(token_hash, csrf_token, expires_at) VALUES (?, ?, ?)",
            ("expired-preauth", "expired-csrf", 1),
        )
        connection.execute(
            "INSERT INTO web_sessions(token_hash, account_id, csrf_token, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("expired-web", account_id, "expired-csrf", 0, 1),
        )
        connection.execute(
            "INSERT INTO mobile_sessions(token_hash, account_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            ("expired-mobile", account_id, 0, 1),
        )
        connection.execute(
            "INSERT INTO enrollment_challenges(id, account_id, public_key_der, public_key_sha256, "
            "nonce, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("expired-enrollment", account_id, b"x", "expired-key", "expired-nonce", 0, 1),
        )
        connection.execute(
            "INSERT INTO device_revocation_challenges(id, account_id, device_id, credential_id, "
            "nonce, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "expired-revocation",
                account_id,
                device_id,
                credential_id,
                "expired-nonce",
                0,
                1,
            ),
        )

    assert client.get("/healthz").status_code == 200
    checks = {
        "preauth_sessions": ("token_hash", "expired-preauth"),
        "web_sessions": ("token_hash", "expired-web"),
        "mobile_sessions": ("token_hash", "expired-mobile"),
        "enrollment_challenges": ("id", "expired-enrollment"),
        "device_revocation_challenges": ("id", "expired-revocation"),
    }
    deadline = time.monotonic() + 1.0
    while True:
        remaining = {}
        with app.state.database.connect() as connection:
            for table, (column, value) in checks.items():
                remaining[table] = connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (value,)
                ).fetchone()[0]
        if not any(remaining.values()) or time.monotonic() >= deadline:
            break
        time.sleep(0.01)
    assert remaining == {table: 0 for table in checks}

"""永久登录、有限期兼容及退出记录跨重启回归测试，可用 unittest 单独运行。"""
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from app.core.token_blacklist import TokenBlacklist
from common.core.config import Settings
from common.utils.security import create_access_token, decode_access_token
from common.utils.time_utils import now_beijing, now_beijing_naive


class PermanentLoginTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(_env_file=None, jwt_secret_key="permanent-login-test-signing-key-123456", access_token_expire_minutes=0)

    def test_permanent_login_stays_valid_years_after_issue(self):
        with patch("common.utils.security.now_beijing", return_value=now_beijing() - timedelta(days=3650)):
            token = create_access_token(1, settings=self.settings)
        payload = decode_access_token(token, settings=self.settings)
        self.assertEqual(payload["sub"], "1")
        self.assertNotIn("exp", payload)
        self.assertTrue(payload["jti"])

    def test_extra_claims_cannot_set_permanent_token_expiry(self):
        token = create_access_token(1, settings=self.settings, extra_claims={"exp": 1})
        self.assertIsNotNone(decode_access_token(token, settings=self.settings))

    def test_explicit_expired_token_still_rejected(self):
        token = create_access_token(1, settings=self.settings, expires_delta=timedelta(seconds=-1))
        self.assertIsNone(decode_access_token(token, settings=self.settings))

    def test_positive_configuration_preserves_expiration(self):
        self.settings.access_token_expire_minutes = 30
        payload = decode_access_token(create_access_token(1, settings=self.settings), settings=self.settings)
        self.assertEqual(payload["exp"] - payload["iat"], 1800)

    def test_bad_signature_still_rejected(self):
        token = create_access_token(1, settings=self.settings)
        self.settings.jwt_secret_key = "different-test-signing-key-123456789"
        self.assertIsNone(decode_access_token(token, settings=self.settings))

    def test_logout_survives_new_instance_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "revoked.sqlite3"
            first = TokenBlacklist(path)
            first.revoke("permanent")
            first.revoke("expired", now_beijing_naive() - timedelta(days=1))
            second = TokenBlacklist(path)
            self.assertTrue(second.is_revoked("permanent"))
            self.assertFalse(second.is_revoked("expired"))
            self.assertFalse(second.is_revoked("another-login"))
            second.clear()
            self.assertFalse(first.is_revoked("permanent"))


if __name__ == "__main__":
    unittest.main()

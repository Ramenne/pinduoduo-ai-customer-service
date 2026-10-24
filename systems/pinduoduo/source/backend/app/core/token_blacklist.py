"""持久保存主动退出的令牌 ID；永久令牌的失效记录不会自动清理。"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator
import sqlite3
from pathlib import Path
from datetime import datetime

from common.utils.time_utils import now_beijing_naive


class TokenBlacklist:
    """SQLite 失效登记，跨进程和服务重启生效，不保存令牌原文。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.environ.get(
            "TOKEN_BLACKLIST_PATH",
            str(Path(__file__).resolve().parents[2] / ".runtime" / "token-revocations.sqlite3"),
        ))

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = sqlite3.connect(str(self.path), timeout=10)
        try:
            with connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS revoked_tokens (jti TEXT PRIMARY KEY, expires_at TEXT)"
                )
                yield connection
        finally:
            connection.close()

    def revoke(self, jti: str, expire_at: datetime | None = None) -> None:
        if not jti:
            return
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO revoked_tokens (jti, expires_at) VALUES (?, ?)",
                (jti, expire_at.isoformat() if expire_at is not None else None),
            )
            connection.execute(
                "DELETE FROM revoked_tokens WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now_beijing_naive().isoformat(),),
            )

    def is_revoked(self, jti: str) -> bool:
        if not jti:
            return False
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM revoked_tokens WHERE jti = ?", (jti,)
            ).fetchone() is not None

    def clear(self) -> None:
        """清空记录，仅供测试使用。"""
        with self._connect() as connection:
            connection.execute("DELETE FROM revoked_tokens")


_token_blacklist = TokenBlacklist()


def get_token_blacklist() -> TokenBlacklist:
    return _token_blacklist


__all__ = ["TokenBlacklist", "get_token_blacklist"]

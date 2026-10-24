from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit


INTERNAL_HTTP_BASE_URL = "http://dominolif.com/oin"
PRODUCTION_DATABASE_URL_REQUIRED_ERROR = (
    "OIN_DATABASE_URL is required when OIN_ENVIRONMENT=production; "
    "SQLite is not permitted"
)


def _environment_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    public_base_url: str
    apk_path: Path | None
    apk_version: str
    database_url: str | None = field(default=None, repr=False)
    database_auto_migrate: bool = True
    internal_http_enabled: bool = False
    environment: str = "development"
    bind_host: str = "127.0.0.1"
    bind_port: int = 18080
    web_session_ttl_seconds: int = 12 * 60 * 60
    mobile_session_ttl_seconds: int = 15 * 60
    preauth_ttl_seconds: int = 15 * 60
    enrollment_ttl_seconds: int = 5 * 60
    route_activation_ttl_seconds: int = 5 * 60
    websocket_auth_timeout_seconds: int = 10
    heartbeat_timeout_seconds: int = 90
    dispatch_freshness_seconds: int = 50
    task_ack_deadline_seconds: int = 8
    websocket_send_timeout_seconds: int = 3
    websocket_close_timeout_seconds: int = 2
    max_active_tasks_per_device: int = 8
    password_iterations: int = 600_000
    max_result_bytes: int = 1_048_576
    cleanup_interval_seconds: int = 60
    database_connect_timeout_seconds: int = 3
    database_statement_timeout_ms: int = 3_000
    database_lock_timeout_ms: int = 1_500

    def __post_init__(self) -> None:
        normalized = self.public_base_url.rstrip("/")
        object.__setattr__(self, "public_base_url", normalized)
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("OIN_PUBLIC_BASE_URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("OIN_PUBLIC_BASE_URL must not contain credentials, query, or fragment")
        if self.internal_http_enabled and normalized != INTERNAL_HTTP_BASE_URL:
            raise ValueError(
                f"OIN_INTERNAL_HTTP_ENABLED is restricted to {INTERNAL_HTTP_BASE_URL}"
            )
        if self.environment == "production" and parsed.scheme != "https":
            if not self.internal_http_enabled:
                raise ValueError(
                    "Production HTTP requires the explicit restricted internal HTTP opt-in"
                )
        if self.environment == "production" and not self.database_url:
            raise ValueError(PRODUCTION_DATABASE_URL_REQUIRED_ERROR)
        if self.database_url:
            database = urlsplit(self.database_url)
            if database.scheme not in {"postgres", "postgresql"}:
                raise ValueError("OIN_DATABASE_URL must be a PostgreSQL URL")
            if not database.hostname or not database.path.strip("/"):
                raise ValueError("OIN_DATABASE_URL must include a host and database name")
            if database.query or database.fragment:
                raise ValueError("OIN_DATABASE_URL must not contain query parameters or a fragment")
            if self.environment == "production":
                database_name = unquote(database.path.strip("/"))
                database_user = unquote(database.username or "")
                if database_name != "oin_v01" or database_user != "oin_v01_app":
                    raise ValueError(
                        "Production PostgreSQL must use the isolated oin_v01 database "
                        "and oin_v01_app role"
                    )
                if self.database_auto_migrate:
                    raise ValueError(
                        "Production PostgreSQL migrations must run out of process as oin_v01_owner"
                    )
        if self.apk_path is not None and not self.apk_path.is_absolute():
            raise ValueError("OIN_APK_PATH must be absolute")
        if not 1 <= self.bind_port <= 65535:
            raise ValueError("OIN_BIND_PORT is invalid")
        if not 1 <= self.dispatch_freshness_seconds < self.heartbeat_timeout_seconds:
            raise ValueError(
                "OIN_DISPATCH_FRESHNESS_SECONDS must be positive and lower than the heartbeat timeout"
            )
        if not 60 <= self.route_activation_ttl_seconds <= 15 * 60:
            raise ValueError("OIN_ROUTE_ACTIVATION_TTL_SECONDS must be between 60 and 900")
        if not 1 <= self.task_ack_deadline_seconds < min(
            10, self.heartbeat_timeout_seconds
        ):
            raise ValueError(
                "OIN_TASK_ACK_DEADLINE_SECONDS must be positive and lower than both "
                "the heartbeat timeout and minimum task TTL"
            )
        if not 1 <= self.websocket_send_timeout_seconds <= 5:
            raise ValueError(
                "OIN_WEBSOCKET_SEND_TIMEOUT_SECONDS must be between 1 and 5"
            )
        if not 1 <= self.websocket_close_timeout_seconds <= 5:
            raise ValueError("OIN_WEBSOCKET_CLOSE_TIMEOUT_SECONDS must be between 1 and 5")
        if not 1 <= self.max_active_tasks_per_device <= 8:
            raise ValueError("OIN_MAX_ACTIVE_TASKS_PER_DEVICE must be between 1 and 8")
        if not 1 <= self.database_connect_timeout_seconds <= 30:
            raise ValueError("OIN_DATABASE_CONNECT_TIMEOUT_SECONDS is invalid")
        if not 100 <= self.database_lock_timeout_ms <= self.database_statement_timeout_ms:
            raise ValueError("OIN_DATABASE_LOCK_TIMEOUT_MS is invalid")
        if not 100 <= self.database_statement_timeout_ms <= 30_000:
            raise ValueError("OIN_DATABASE_STATEMENT_TIMEOUT_MS is invalid")

    @property
    def public_path_prefix(self) -> str:
        path = urlsplit(self.public_base_url).path.rstrip("/")
        return path

    @property
    def transport_mode(self) -> str:
        if self.internal_http_enabled:
            return "internal_http"
        return "https" if urlsplit(self.public_base_url).scheme == "https" else "development_http"

    @property
    def cookie_path(self) -> str:
        return self.public_path_prefix or "/"

    def public_url(self, path: str) -> str:
        return f"{self.public_base_url}/{path.lstrip('/')}"

    def browser_path(self, path: str) -> str:
        suffix = "/" + path.lstrip("/")
        return f"{self.public_path_prefix}{suffix}" if self.public_path_prefix else suffix

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.environ.get("OIN_DATABASE_URL", "").strip() or None
        database_path = Path(os.environ.get("OIN_DATABASE_PATH", "runtime/oin-v01.db")).resolve()
        apk_value = os.environ.get("OIN_APK_PATH", "").strip()
        return cls(
            database_path=database_path,
            database_url=database_url,
            database_auto_migrate=_environment_bool(
                "OIN_DATABASE_AUTO_MIGRATE", default=database_url is None
            ),
            internal_http_enabled=_environment_bool(
                "OIN_INTERNAL_HTTP_ENABLED", default=False
            ),
            public_base_url=os.environ.get("OIN_PUBLIC_BASE_URL", "https://example.invalid/oin"),
            apk_path=Path(apk_value).resolve() if apk_value else None,
            apk_version=os.environ.get("OIN_APK_VERSION", "unpublished"),
            environment=os.environ.get("OIN_ENVIRONMENT", "development").strip().lower(),
            bind_host=os.environ.get("OIN_BIND_HOST", "127.0.0.1"),
            bind_port=int(os.environ.get("OIN_BIND_PORT", "18080")),
            dispatch_freshness_seconds=int(
                os.environ.get("OIN_DISPATCH_FRESHNESS_SECONDS", "50")
            ),
            route_activation_ttl_seconds=int(
                os.environ.get("OIN_ROUTE_ACTIVATION_TTL_SECONDS", "300")
            ),
            task_ack_deadline_seconds=int(
                os.environ.get("OIN_TASK_ACK_DEADLINE_SECONDS", "8")
            ),
            websocket_send_timeout_seconds=int(
                os.environ.get("OIN_WEBSOCKET_SEND_TIMEOUT_SECONDS", "3")
            ),
            websocket_close_timeout_seconds=int(
                os.environ.get("OIN_WEBSOCKET_CLOSE_TIMEOUT_SECONDS", "2")
            ),
            max_active_tasks_per_device=int(
                os.environ.get("OIN_MAX_ACTIVE_TASKS_PER_DEVICE", "8")
            ),
            database_connect_timeout_seconds=int(
                os.environ.get("OIN_DATABASE_CONNECT_TIMEOUT_SECONDS", "3")
            ),
            database_statement_timeout_ms=int(
                os.environ.get("OIN_DATABASE_STATEMENT_TIMEOUT_MS", "3000")
            ),
            database_lock_timeout_ms=int(
                os.environ.get("OIN_DATABASE_LOCK_TIMEOUT_MS", "1500")
            ),
        )

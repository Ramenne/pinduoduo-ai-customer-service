from __future__ import annotations

import json
import hmac
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .config import Settings
from .database import Database, IntegrityError
from .protocol import (
    PROTOCOL_VERSION,
    TASK_STORE_PROTOCOL_V2,
    task_channel_ready,
    task_store_protocol,
)
from .security import (
    PublicKey,
    canonical_json,
    credential_repair_message,
    enrollment_message,
    hash_password,
    new_opaque_token,
    normalize_identifier,
    now_seconds,
    revocation_message,
    token_hash,
    validate_email,
    validate_username,
    verify_ecdsa_signature,
    verify_password,
)


class StoreError(RuntimeError):
    code = "store_error"


class ConflictError(StoreError):
    code = "conflict"


class NotFoundError(StoreError):
    code = "not_found"


class AuthenticationError(StoreError):
    code = "authentication_failed"


class TaskChannelNotReadyError(StoreError):
    code = "device_task_channel_not_ready"


class UnsupportedDeviceTaskError(StoreError):
    code = "device_task_type_unsupported"


class TaskCapacityError(StoreError):
    code = "device_task_capacity_reached"


class TaskCompletionKind(str, Enum):
    STORED = "stored"
    EXACT_DUPLICATE = "exact_duplicate"
    CONFLICT = "conflict"
    NOT_FOUND = "not_found"
    INVALID_STATUS = "invalid_status"


class DeliveryAckKind(str, Enum):
    ACKNOWLEDGED = "acknowledged"
    STALE = "stale"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class TaskCompletion:
    kind: TaskCompletionKind
    disposition: str | None = None

    @property
    def acknowledged(self) -> bool:
        return self.kind in {
            TaskCompletionKind.STORED,
            TaskCompletionKind.EXACT_DUPLICATE,
        }


@dataclass(frozen=True, slots=True)
class Session:
    account: dict[str, Any]
    csrf_token: str
    expires_at: int


@dataclass(frozen=True, slots=True)
class EnrollmentChallenge:
    id: str
    nonce: str
    expires_at: int


@dataclass(frozen=True, slots=True)
class DeviceRevocationChallenge:
    id: str
    nonce: str
    expires_at: int


@dataclass(frozen=True, slots=True)
class DeviceCredentialRepairChallenge:
    id: str
    nonce: str
    expires_at: int


class Store:
    DEVICE_STATE_FRESH_SECONDS = 600

    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings
        self._dummy_password_hash = hash_password(
            "dummy-password-never-valid", settings.password_iterations
        )
        self._cleanup_lock = threading.Lock()
        self._last_cleanup_monotonic = 0.0

    def record_audit(
        self,
        account_id: str | None,
        device_id: str | None,
        action: str,
        success: bool,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Persist an audit fact in its own transaction, including rejected attempts."""

        with self.database.transaction(immediate=True) as connection:
            self._audit(connection, account_id, device_id, action, success, detail)

    @staticmethod
    def _account(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "username": row["username"],
            "email": row["email"],
            "status": row["status"],
            "created_at": row["created_at"],
        }

    def create_account(self, username: str, email: str | None, password: str) -> dict[str, Any]:
        username = validate_username(username)
        email = validate_email(email)
        account_id = str(uuid.uuid4())
        created_at = now_seconds()
        password_hash = hash_password(password, self.settings.password_iterations)
        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    INSERT INTO accounts(
                        id, username, username_norm, email, email_norm,
                        password_hash, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
                    """,
                    (
                        account_id,
                        username,
                        normalize_identifier(username),
                        email,
                        normalize_identifier(email) if email else None,
                        password_hash,
                        created_at,
                    ),
                )
                user_id = f"user:{account_id}"
                connection.execute(
                    """
                    INSERT INTO account_users(
                        id, account_id, state, revision, created_at, updated_at
                    ) VALUES (?, ?, 'ACTIVE', 1, ?, ?)
                    """,
                    (user_id, account_id, created_at, created_at),
                )
                self._audit(connection, account_id, None, "account_registered", True)
        except IntegrityError as exc:
            raise ConflictError("username_or_email_already_registered") from exc
        return {
            "id": account_id,
            "user_id": user_id,
            "username": username,
            "email": email,
            "status": "active",
            "created_at": created_at,
        }

    def authenticate(self, identifier: str, password: str) -> dict[str, Any] | None:
        identifier_norm = normalize_identifier(identifier)
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT a.*, u.id AS user_id FROM accounts a
                JOIN account_users u ON u.account_id = a.id AND u.state = 'ACTIVE'
                WHERE a.username_norm = ? OR a.email_norm = ?
                LIMIT 1
                """,
                (identifier_norm, identifier_norm),
            ).fetchone()
        encoded = row["password_hash"] if row is not None else self._dummy_password_hash
        password_valid = verify_password(password, encoded)
        if row is None or not password_valid or row["status"] != "active":
            return None
        return self._account(row)

    def create_preauth_session(self) -> tuple[str, str]:
        token = new_opaque_token()
        csrf = new_opaque_token(24)
        expires_at = now_seconds() + self.settings.preauth_ttl_seconds
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO preauth_sessions(token_hash, csrf_token, expires_at) VALUES (?, ?, ?)",
                (token_hash(token), csrf, expires_at),
            )
        return token, csrf

    def validate_preauth_session(self, token: str | None, csrf: str | None) -> bool:
        if not token or not csrf:
            return False
        current = now_seconds()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT csrf_token, expires_at FROM preauth_sessions WHERE token_hash = ?",
                (token_hash(token),),
            ).fetchone()
        import hmac

        return bool(
            row
            and row["expires_at"] > current
            and hmac.compare_digest(row["csrf_token"], csrf)
        )

    def delete_preauth_session(self, token: str | None) -> None:
        if not token:
            return
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM preauth_sessions WHERE token_hash = ?", (token_hash(token),)
            )

    def create_web_session(self, account_id: str) -> tuple[str, str, int]:
        token = new_opaque_token()
        csrf = new_opaque_token(24)
        current = now_seconds()
        expires_at = current + self.settings.web_session_ttl_seconds
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO web_sessions(token_hash, account_id, csrf_token, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (token_hash(token), account_id, csrf, current, expires_at),
            )
        return token, csrf, expires_at

    def get_web_session(self, token: str | None) -> Session | None:
        if not token:
            return None
        current = now_seconds()
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT a.*, u.id AS user_id,
                       s.csrf_token AS session_csrf, s.expires_at AS session_expires_at
                FROM web_sessions s
                JOIN accounts a ON a.id = s.account_id
                JOIN account_users u ON u.account_id = a.id AND u.state = 'ACTIVE'
                WHERE s.token_hash = ? AND s.expires_at > ? AND a.status = 'active'
                """,
                (token_hash(token), current),
            ).fetchone()
        if row is None:
            return None
        return Session(
            account=self._account(row),
            csrf_token=row["session_csrf"],
            expires_at=row["session_expires_at"],
        )

    def delete_web_session(self, token: str | None) -> None:
        if not token:
            return
        with self.database.connect() as connection:
            connection.execute("DELETE FROM web_sessions WHERE token_hash = ?", (token_hash(token),))

    def create_mobile_session(self, account_id: str) -> tuple[str, int]:
        token = new_opaque_token()
        current = now_seconds()
        expires_at = current + self.settings.mobile_session_ttl_seconds
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO mobile_sessions(token_hash, account_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (token_hash(token), account_id, current, expires_at),
            )
        return token, expires_at

    def get_mobile_account(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        current = now_seconds()
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT a.*, u.id AS user_id
                FROM mobile_sessions s
                JOIN accounts a ON a.id = s.account_id
                JOIN account_users u ON u.account_id = a.id AND u.state = 'ACTIVE'
                WHERE s.token_hash = ? AND s.expires_at > ? AND s.revoked_at IS NULL
                  AND a.status = 'active'
                """,
                (token_hash(token), current),
            ).fetchone()
        return self._account(row) if row is not None else None

    def revoke_mobile_session(self, token: str | None) -> None:
        if not token:
            return
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE mobile_sessions SET revoked_at = ? WHERE token_hash = ?",
                (now_seconds(), token_hash(token)),
            )

    def create_enrollment_challenge(
        self, account_id: str, public_key: PublicKey
    ) -> EnrollmentChallenge:
        challenge = EnrollmentChallenge(
            id=str(uuid.uuid4()),
            nonce=new_opaque_token(32),
            expires_at=now_seconds() + self.settings.enrollment_ttl_seconds,
        )
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO enrollment_challenges(
                    id, account_id, public_key_der, public_key_sha256,
                    nonce, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    challenge.id,
                    account_id,
                    public_key.der,
                    public_key.fingerprint,
                    challenge.nonce,
                    now_seconds(),
                    challenge.expires_at,
                ),
            )
        return challenge

    def enroll_device(
        self,
        account_id: str,
        challenge_id: str,
        payload: dict[str, Any],
        signature: str,
    ) -> tuple[dict[str, Any], bool]:
        current = now_seconds()
        outcome: tuple[dict[str, Any], bool] | None = None
        rejection: AuthenticationError | ConflictError | None = None
        with self.database.transaction(immediate=True) as connection:
            challenge = connection.execute(
                "SELECT * FROM enrollment_challenges WHERE id = ?", (challenge_id,)
            ).fetchone()
            if (
                challenge is None
                or challenge["account_id"] != account_id
                or challenge["used_at"] is not None
                or challenge["expires_at"] <= current
            ):
                raise AuthenticationError("invalid_or_expired_enrollment_challenge")
            connection.execute(
                "UPDATE enrollment_challenges SET used_at = ? WHERE id = ?",
                (current, challenge_id),
            )
            message = enrollment_message(challenge_id, challenge["nonce"], payload)
            if not verify_ecdsa_signature(challenge["public_key_der"], message, signature):
                rejection = AuthenticationError("invalid_device_signature")
            else:
                existing = connection.execute(
                    """
                    SELECT d.*, c.revoked_at AS credential_revoked_at
                    FROM device_credentials c
                    JOIN devices d ON d.id = c.device_id
                    WHERE c.public_key_sha256 = ?
                    """,
                    (challenge["public_key_sha256"],),
                ).fetchone()
            if rejection is None and existing is not None:
                if (
                    existing["owner_account_id"] != account_id
                    or existing["binding_state"] != "bound"
                    or existing["credential_revoked_at"] is not None
                ):
                    rejection = ConflictError("device_key_already_bound_or_revoked")
                else:
                    self._update_device_row(connection, existing["id"], payload, current)
                    self._audit(
                        connection,
                        account_id,
                        existing["id"],
                        "device_enrollment_repeated",
                        True,
                    )
                    refreshed = connection.execute(
                        "SELECT * FROM devices WHERE id = ?", (existing["id"],)
                    ).fetchone()
                    outcome = (self._device(refreshed), True)

            if rejection is None and outcome is None:
                device_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO devices(
                        id, owner_account_id, display_name, manufacturer, brand, model,
                        android_version, sdk, app_version, app_version_code, kernel_version,
                        timezone, network_type, installation_id, info_json, capabilities_json,
                        binding_state, online, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'bound', 0, ?, ?)
                    """,
                    self._device_values(device_id, account_id, payload, current),
                )
                connection.execute(
                    """
                    INSERT INTO device_credentials(
                        id, device_id, public_key_der, public_key_sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        device_id,
                        challenge["public_key_der"],
                        challenge["public_key_sha256"],
                        current,
                    ),
                )
                self._audit(connection, account_id, device_id, "device_enrolled", True)
                row = connection.execute(
                    "SELECT * FROM devices WHERE id = ?", (device_id,)
                ).fetchone()
                outcome = (self._device(row), False)
        if rejection is not None:
            raise rejection
        if outcome is None:
            raise RuntimeError("device_enrollment_outcome_missing")
        return outcome

    @staticmethod
    def _device_values(
        device_id: str, account_id: str, payload: dict[str, Any], current: int
    ) -> tuple[Any, ...]:
        capabilities = payload["capabilities"]
        return (
            device_id,
            account_id,
            payload["display_name"],
            payload["manufacturer"],
            payload["brand"],
            payload["model"],
            payload["android_version"],
            payload["sdk"],
            payload["app_version"],
            payload["app_version_code"],
            payload["kernel_version"],
            payload["timezone"],
            payload["network_type"],
            payload["installation_id"],
            canonical_json({key: value for key, value in payload.items() if key != "capabilities"}),
            canonical_json(capabilities),
            current,
            current,
        )

    @staticmethod
    def _update_device_row(
        connection: Any,
        device_id: str,
        payload: dict[str, Any],
        current: int,
        *,
        preserve_task_channel: bool = False,
        device_state: str = "fresh",
        device_state_failure_stage: str = "none",
    ) -> None:
        existing = connection.execute(
            "SELECT info_json, capabilities_json FROM devices WHERE id = ?",
            (device_id,),
        ).fetchone()
        existing_info: dict[str, Any] = {}
        existing_capabilities: dict[str, Any] = {}
        if existing is not None:
            try:
                existing_info = json.loads(existing["info_json"])
                existing_capabilities = json.loads(existing["capabilities_json"])
            except (TypeError, ValueError):
                existing_info = {}
                existing_capabilities = {}
        capabilities = dict(payload["capabilities"])
        if preserve_task_channel and task_store_protocol(existing_capabilities) is not None:
            for field in ("task_store_protocol", "task_channel_ready", "task_store_state"):
                capabilities[field] = existing_capabilities[field]
        info = {key: value for key, value in payload.items() if key != "capabilities"}
        existing_task_channel = existing_info.get("_task_channel")
        if preserve_task_channel and isinstance(existing_task_channel, dict):
            info["_task_channel"] = existing_task_channel
        elif task_store_protocol(capabilities) is not None:
            info["_task_channel"] = {
                "state": capabilities["task_store_state"],
                "ready": capabilities["task_channel_ready"],
                "failure_stage": "none",
                "updated_at": current,
            }
        info["_device_state"] = {
            "state": device_state,
            "failure_stage": device_state_failure_stage,
            "updated_at": current,
        }
        connection.execute(
            """
            UPDATE devices SET
                display_name = ?, manufacturer = ?, brand = ?, model = ?,
                android_version = ?, sdk = ?, app_version = ?, app_version_code = ?,
                kernel_version = ?, timezone = ?, network_type = ?, installation_id = ?,
                info_json = ?, capabilities_json = ?, updated_at = ?
            WHERE id = ? AND binding_state = 'bound'
            """,
            (
                payload["display_name"],
                payload["manufacturer"],
                payload["brand"],
                payload["model"],
                payload["android_version"],
                payload["sdk"],
                payload["app_version"],
                payload["app_version_code"],
                payload["kernel_version"],
                payload["timezone"],
                payload["network_type"],
                payload["installation_id"],
                canonical_json(info),
                canonical_json(capabilities),
                current,
                device_id,
            ),
        )

    def get_device_credential(self, device_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT d.id AS device_id, d.owner_account_id, d.binding_state,
                       c.public_key_der, c.public_key_sha256
                FROM devices d
                JOIN device_credentials c ON c.device_id = d.id
                JOIN accounts a ON a.id = d.owner_account_id
                WHERE d.id = ? AND d.binding_state = 'bound' AND d.revoked_at IS NULL
                  AND c.revoked_at IS NULL AND a.status = 'active'
                """,
                (device_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def create_device_credential_repair_request(
        self,
        account_id: str,
        device_id: str,
        public_key: PublicKey,
    ) -> tuple[dict[str, Any], bool]:
        """Stage a replacement key without authorizing its use.

        A mobile bearer proves only same-account access.  A separate web-controller
        action must approve this exact request before a nonce is disclosed.
        """

        request_id = str(uuid.uuid4())
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            device = connection.execute(
                """
                SELECT binding_state FROM devices
                WHERE id = ? AND owner_account_id = ?
                """,
                (device_id, account_id),
            ).fetchone()
            if device is None:
                raise NotFoundError("device_not_found")
            if device["binding_state"] != "bound":
                raise ConflictError("revoked_device_cannot_rotate_credential")
            active = connection.execute(
                """
                SELECT public_key_sha256 FROM device_credentials
                WHERE device_id = ? AND revoked_at IS NULL
                """,
                (device_id,),
            ).fetchone()
            if active is not None and active["public_key_sha256"] == public_key.fingerprint:
                completed = connection.execute(
                    """
                    SELECT * FROM device_credential_repair_challenges
                    WHERE account_id = ? AND device_id = ? AND public_key_sha256 = ?
                      AND used_at IS NOT NULL
                    ORDER BY used_at DESC, id DESC LIMIT 1
                    """,
                    (account_id, device_id, public_key.fingerprint),
                ).fetchone()
                if completed is not None:
                    return self._credential_repair_request(completed), False
                raise ConflictError("credential_already_active")
            key_owner = connection.execute(
                "SELECT device_id FROM device_credentials WHERE public_key_sha256 = ?",
                (public_key.fingerprint,),
            ).fetchone()
            if key_owner is not None and key_owner["device_id"] != device_id:
                raise ConflictError("device_key_already_bound")
            existing = connection.execute(
                """
                SELECT * FROM device_credential_repair_challenges
                WHERE account_id = ? AND device_id = ? AND public_key_sha256 = ?
                  AND used_at IS NULL
                  AND (approved_at IS NULL OR expires_at > ?)
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (account_id, device_id, public_key.fingerprint, current),
            ).fetchone()
            if existing is not None:
                return self._credential_repair_request(existing), False
            connection.execute(
                """
                INSERT INTO device_credential_repair_challenges(
                    id, account_id, device_id, public_key_der, public_key_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    account_id,
                    device_id,
                    public_key.der,
                    public_key.fingerprint,
                    current,
                ),
            )
            self._audit(
                connection,
                account_id,
                device_id,
                "device_credential_repair_requested",
                True,
            )
            row = connection.execute(
                "SELECT * FROM device_credential_repair_challenges WHERE id = ?",
                (request_id,),
            ).fetchone()
        return self._credential_repair_request(row), True

    def authorize_device_credential_repair(
        self, account_id: str, device_id: str, request_id: str
    ) -> tuple[dict[str, Any], bool]:
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT r.*, d.binding_state
                FROM device_credential_repair_challenges r
                JOIN devices d ON d.id = r.device_id
                WHERE r.id = ? AND r.account_id = ? AND r.device_id = ?
                """,
                (request_id, account_id, device_id),
            ).fetchone()
            if row is None:
                raise NotFoundError("credential_repair_request_not_found")
            if row["binding_state"] != "bound" or row["used_at"] is not None:
                raise ConflictError("credential_repair_request_not_approvable")
            if row["approved_at"] is not None:
                return self._credential_repair_request(row, include_challenge=True), False
            nonce = new_opaque_token(32)
            expires_at = current + self.settings.enrollment_ttl_seconds
            connection.execute(
                """
                UPDATE device_credential_repair_challenges
                SET nonce = ?, approved_at = ?, expires_at = ?
                WHERE id = ? AND approved_at IS NULL
                """,
                (nonce, current, expires_at, request_id),
            )
            self._audit(
                connection,
                account_id,
                device_id,
                "device_credential_repair_authorized",
                True,
                {"request_id": request_id},
            )
            approved = connection.execute(
                "SELECT * FROM device_credential_repair_challenges WHERE id = ?",
                (request_id,),
            ).fetchone()
        return self._credential_repair_request(approved, include_challenge=True), True

    def get_device_credential_repair_request(
        self, account_id: str, device_id: str, request_id: str
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM device_credential_repair_challenges
                WHERE id = ? AND account_id = ? AND device_id = ?
                """,
                (request_id, account_id, device_id),
            ).fetchone()
        return (
            self._credential_repair_request(row, include_challenge=True)
            if row is not None
            else None
        )

    def list_device_credential_repair_requests(
        self, account_id: str, device_id: str
    ) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM device_credential_repair_challenges
                WHERE account_id = ? AND device_id = ?
                ORDER BY created_at DESC, id DESC LIMIT 20
                """,
                (account_id, device_id),
            ).fetchall()
        return [self._credential_repair_request(row) for row in rows]

    @staticmethod
    def _credential_repair_request(
        row: Mapping[str, Any], *, include_challenge: bool = False
    ) -> dict[str, Any]:
        if row["used_at"] is not None:
            state = "completed"
        elif (
            row["approved_at"] is not None
            and row["expires_at"] is not None
            and row["expires_at"] <= now_seconds()
        ):
            state = "expired"
        elif row["approved_at"] is not None:
            state = "approved"
        else:
            state = "pending_controller_approval"
        value: dict[str, Any] = {
            "id": row["id"],
            "device_id": row["device_id"],
            "public_key_sha256": row["public_key_sha256"],
            "created_at": row["created_at"],
            "approved_at": row["approved_at"],
            "expires_at": row["expires_at"],
            "used_at": row["used_at"],
            "state": state,
        }
        if include_challenge and state == "approved":
            value["challenge_id"] = row["id"]
            value["nonce"] = row["nonce"]
        return value

    def repair_device_credential(
        self,
        account_id: str,
        device_id: str,
        challenge_id: str,
        payload: dict[str, Any],
        signature: str,
    ) -> tuple[dict[str, Any], bool]:
        current = now_seconds()
        rejection: AuthenticationError | ConflictError | None = None
        outcome: tuple[dict[str, Any], bool] | None = None
        try:
            with self.database.transaction(immediate=True) as connection:
                challenge = connection.execute(
                    """
                    SELECT r.*, d.binding_state
                    FROM device_credential_repair_challenges r
                    JOIN devices d ON d.id = r.device_id
                    WHERE r.id = ?
                    """,
                    (challenge_id,),
                ).fetchone()
                if (
                    challenge is None
                    or challenge["account_id"] != account_id
                    or challenge["device_id"] != device_id
                ):
                    raise AuthenticationError("invalid_credential_repair_challenge")
                message = credential_repair_message(
                    device_id,
                    challenge_id,
                    challenge["nonce"],
                    payload,
                )
                signature_valid = verify_ecdsa_signature(
                    challenge["public_key_der"], message, signature
                )
                active = connection.execute(
                    """
                    SELECT public_key_sha256 FROM device_credentials
                    WHERE device_id = ? AND revoked_at IS NULL
                    """,
                    (device_id,),
                ).fetchone()
                already_active = bool(
                    active
                    and active["public_key_sha256"]
                    == challenge["public_key_sha256"]
                )
                if challenge["used_at"] is not None:
                    if not signature_valid or not already_active:
                        rejection = AuthenticationError(
                            "credential_repair_challenge_already_used"
                        )
                    else:
                        self._update_device_row(connection, device_id, payload, current)
                        row = connection.execute(
                            "SELECT * FROM devices WHERE id = ?", (device_id,)
                        ).fetchone()
                        outcome = (self._device(row), True)
                elif (
                    challenge["approved_at"] is None
                    or challenge["nonce"] is None
                    or challenge["expires_at"] is None
                    or challenge["expires_at"] <= current
                    or challenge["binding_state"] != "bound"
                ):
                    rejection = AuthenticationError(
                        "invalid_or_expired_credential_repair_challenge"
                    )
                else:
                    if not signature_valid:
                        rejection = AuthenticationError("invalid_device_signature")
                    else:
                        connection.execute(
                            """
                            UPDATE device_credential_repair_challenges
                            SET used_at = ? WHERE id = ? AND used_at IS NULL
                            """,
                            (current, challenge_id),
                        )
                        connection.execute(
                            """
                            UPDATE device_credentials SET revoked_at = ?
                            WHERE device_id = ? AND revoked_at IS NULL
                            """,
                            (current, device_id),
                        )
                        connection.execute(
                            """
                            INSERT INTO device_credentials(
                                id, device_id, public_key_der, public_key_sha256, created_at
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                str(uuid.uuid4()),
                                device_id,
                                challenge["public_key_der"],
                                challenge["public_key_sha256"],
                                current,
                            ),
                        )
                        self._update_device_row(connection, device_id, payload, current)
                        self._audit(
                            connection,
                            account_id,
                            device_id,
                            "device_credential_rotated",
                            True,
                        )
                        row = connection.execute(
                            "SELECT * FROM devices WHERE id = ?", (device_id,)
                        ).fetchone()
                        outcome = (self._device(row), False)
        except IntegrityError as exc:
            raise ConflictError("device_key_already_bound") from exc
        if rejection is not None:
            raise rejection
        if outcome is None:
            raise RuntimeError("credential_repair_outcome_missing")
        return outcome

    def get_device_auth_state(self, device_id: str) -> str:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT d.binding_state, d.revoked_at,
                       MAX(CASE WHEN c.revoked_at IS NULL THEN 1 ELSE 0 END) AS active_credential
                FROM devices d
                LEFT JOIN device_credentials c ON c.device_id = d.id
                WHERE d.id = ?
                GROUP BY d.id, d.binding_state, d.revoked_at
                """,
                (device_id,),
            ).fetchone()
        if row is None:
            return "unknown"
        if row["binding_state"] == "revoked" or row["revoked_at"] is not None:
            return "revoked"
        return "active" if row["active_credential"] else "credential_missing"

    def create_device_revocation_challenge(
        self, account_id: str, device_id: str
    ) -> DeviceRevocationChallenge:
        challenge = DeviceRevocationChallenge(
            id=str(uuid.uuid4()),
            nonce=new_opaque_token(32),
            expires_at=now_seconds() + self.settings.enrollment_ttl_seconds,
        )
        with self.database.transaction(immediate=True) as connection:
            credential = connection.execute(
                """
                SELECT c.id AS credential_id
                FROM devices d
                JOIN device_credentials c ON c.device_id = d.id
                WHERE d.id = ? AND d.owner_account_id = ?
                  AND d.binding_state = 'bound' AND d.revoked_at IS NULL
                  AND c.revoked_at IS NULL
                """,
                (device_id, account_id),
            ).fetchone()
            if credential is None:
                raise NotFoundError("device_not_found")
            connection.execute(
                """
                INSERT INTO device_revocation_challenges(
                    id, account_id, device_id, credential_id, nonce,
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    challenge.id,
                    account_id,
                    device_id,
                    credential["credential_id"],
                    challenge.nonce,
                    now_seconds(),
                    challenge.expires_at,
                ),
            )
        return challenge

    def self_revoke_device(
        self,
        account_id: str,
        device_id: str,
        challenge_id: str,
        signature: str,
    ) -> bool:
        current = now_seconds()
        rejection: AuthenticationError | None = None
        revoked = False
        with self.database.transaction(immediate=True) as connection:
            challenge = connection.execute(
                """
                SELECT r.*, c.public_key_der, c.revoked_at AS credential_revoked_at,
                       d.binding_state
                FROM device_revocation_challenges r
                JOIN device_credentials c ON c.id = r.credential_id
                JOIN devices d ON d.id = r.device_id
                WHERE r.id = ?
                """,
                (challenge_id,),
            ).fetchone()
            if (
                challenge is None
                or challenge["account_id"] != account_id
                or challenge["device_id"] != device_id
                or challenge["used_at"] is not None
                or challenge["expires_at"] <= current
                or challenge["credential_revoked_at"] is not None
                or challenge["binding_state"] != "bound"
            ):
                raise AuthenticationError("invalid_or_expired_revocation_challenge")
            connection.execute(
                "UPDATE device_revocation_challenges SET used_at = ? WHERE id = ?",
                (current, challenge_id),
            )
            message = revocation_message(device_id, challenge_id, challenge["nonce"])
            if not verify_ecdsa_signature(challenge["public_key_der"], message, signature):
                rejection = AuthenticationError("invalid_device_signature")
            else:
                revoked = self._revoke_device(connection, account_id, device_id, current)
        if rejection is not None:
            raise rejection
        return revoked

    def update_device_heartbeat(
        self,
        session_id: str,
        device_id: str,
        payload: dict[str, Any],
    ) -> None:
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT d.binding_state
                FROM device_sessions s
                JOIN devices d ON d.id = s.device_id
                WHERE s.id = ? AND s.device_id = ? AND s.disconnected_at IS NULL
                """,
                (session_id, device_id),
            ).fetchone()
            if row is None or row["binding_state"] != "bound":
                raise NotFoundError("device_session_not_active")
            self._update_device_row(connection, device_id, payload, current)
            connection.execute(
                """
                UPDATE device_sessions SET last_heartbeat_at = ?
                WHERE id = ? AND device_id = ? AND disconnected_at IS NULL
                """,
                (current, session_id, device_id),
            )
            connection.execute(
                "UPDATE devices SET online = 1, last_seen_at = ?, updated_at = ? WHERE id = ?",
                (current, current, device_id),
            )

    def update_transport_heartbeat(self, session_id: str, device_id: str) -> None:
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT d.binding_state
                FROM device_sessions s
                JOIN devices d ON d.id = s.device_id
                WHERE s.id = ? AND s.device_id = ? AND s.disconnected_at IS NULL
                """,
                (session_id, device_id),
            ).fetchone()
            if row is None or row["binding_state"] != "bound":
                raise NotFoundError("device_session_not_active")
            connection.execute(
                """
                UPDATE device_sessions SET last_heartbeat_at = ?
                WHERE id = ? AND device_id = ? AND disconnected_at IS NULL
                """,
                (current, session_id, device_id),
            )
            connection.execute(
                "UPDATE devices SET online = 1, last_seen_at = ? WHERE id = ?",
                (current, device_id),
            )

    def update_device_state(
        self,
        session_id: str,
        device_id: str,
        state: str,
        failure_stage: str,
        payload: dict[str, Any] | None,
    ) -> None:
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT d.binding_state, d.info_json
                FROM device_sessions s
                JOIN devices d ON d.id = s.device_id
                WHERE s.id = ? AND s.device_id = ? AND s.disconnected_at IS NULL
                """,
                (session_id, device_id),
            ).fetchone()
            if row is None or row["binding_state"] != "bound":
                raise NotFoundError("device_session_not_active")
            if state == "fresh":
                if payload is None:
                    raise ValueError("fresh_device_state_requires_payload")
                self._update_device_row(
                    connection,
                    device_id,
                    payload,
                    current,
                    preserve_task_channel=True,
                    device_state=state,
                    device_state_failure_stage=failure_stage,
                )
                return
            try:
                info = json.loads(row["info_json"])
            except (TypeError, ValueError):
                info = {}
            info["_device_state"] = {
                "state": state,
                "failure_stage": failure_stage,
                "updated_at": current,
            }
            connection.execute(
                "UPDATE devices SET info_json = ?, updated_at = ? WHERE id = ?",
                (canonical_json(info), current, device_id),
            )

    def update_task_channel_state(
        self,
        session_id: str,
        device_id: str,
        protocol: int,
        state: str,
        ready: bool,
        failure_stage: str,
    ) -> None:
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT d.binding_state, d.info_json, d.capabilities_json
                FROM device_sessions s
                JOIN devices d ON d.id = s.device_id
                WHERE s.id = ? AND s.device_id = ? AND s.disconnected_at IS NULL
                """,
                (session_id, device_id),
            ).fetchone()
            if row is None or row["binding_state"] != "bound":
                raise NotFoundError("device_session_not_active")
            try:
                capabilities = json.loads(row["capabilities_json"])
                info = json.loads(row["info_json"])
            except (TypeError, ValueError) as exc:
                raise NotFoundError("device_state_unavailable") from exc
            if task_store_protocol(capabilities) != protocol:
                raise NotFoundError("task_store_protocol_mismatch")
            capabilities["task_channel_ready"] = ready
            capabilities["task_store_state"] = state
            info["_task_channel"] = {
                "state": state,
                "ready": ready,
                "failure_stage": failure_stage,
                "updated_at": current,
            }
            connection.execute(
                """
                UPDATE devices
                SET capabilities_json = ?, info_json = ?, updated_at = ?
                WHERE id = ? AND binding_state = 'bound'
                """,
                (
                    canonical_json(capabilities),
                    canonical_json(info),
                    current,
                    device_id,
                ),
            )

    def open_transport_session(self, device_id: str, protocol: int) -> str:
        """Open code26 transport without treating authentication as a device snapshot."""

        if protocol != TASK_STORE_PROTOCOL_V2:
            raise ValueError("unsupported_task_store_protocol")
        session_id = str(uuid.uuid4())
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT binding_state, info_json, capabilities_json
                FROM devices WHERE id = ?
                """,
                (device_id,),
            ).fetchone()
            if row is None or row["binding_state"] != "bound":
                raise NotFoundError("device_not_active")
            try:
                info = json.loads(row["info_json"])
                if not isinstance(info, dict):
                    info = {}
            except (TypeError, ValueError):
                info = {}
            try:
                capabilities = json.loads(row["capabilities_json"])
                if not isinstance(capabilities, dict):
                    capabilities = {}
            except (TypeError, ValueError):
                capabilities = {}
            capabilities["task_store_protocol"] = protocol
            capabilities["task_channel_ready"] = False
            capabilities["task_store_state"] = "recovery_pending"
            info["_task_channel"] = {
                "state": "recovery_pending",
                "ready": False,
                "failure_stage": "none",
                "updated_at": current,
            }
            info["_device_state"] = {
                "state": "stale",
                "failure_stage": "device_snapshot_collect",
                "updated_at": current,
            }
            connection.execute(
                """
                UPDATE task_delivery_attempts SET outcome = 'superseded', completed_at = ?
                WHERE device_id = ? AND outcome IN ('prepared', 'sent')
                  AND server_session_id IN (
                      SELECT id FROM device_sessions
                      WHERE device_id = ? AND disconnected_at IS NULL
                  )
                """,
                (current, device_id, device_id),
            )
            connection.execute(
                """
                UPDATE device_sessions
                SET disconnected_at = ?, disconnect_reason = 'replaced_by_new_device_session'
                WHERE device_id = ? AND disconnected_at IS NULL
                """,
                (current, device_id),
            )
            connection.execute(
                """
                INSERT INTO device_sessions(
                    id, device_id, protocol_version, connected_at, last_heartbeat_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, device_id, PROTOCOL_VERSION, current, current),
            )
            connection.execute(
                """
                UPDATE devices
                SET online = 1, last_seen_at = ?, updated_at = ?,
                    info_json = ?, capabilities_json = ?
                WHERE id = ? AND binding_state = 'bound'
                """,
                (
                    current,
                    current,
                    canonical_json(info),
                    canonical_json(capabilities),
                    device_id,
                ),
            )
        return session_id

    def open_device_session(
        self,
        device_id: str,
        payload: dict[str, Any],
        *,
        defer_independent_lanes: bool = False,
    ) -> str:
        session_id = str(uuid.uuid4())
        current = now_seconds()
        stored_payload = payload
        if defer_independent_lanes:
            stored_payload = json.loads(canonical_json(payload))
            stored_payload["capabilities"]["task_channel_ready"] = False
            stored_payload["capabilities"]["task_store_state"] = "recovery_pending"
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT binding_state FROM devices WHERE id = ?", (device_id,)
            ).fetchone()
            if row is None or row["binding_state"] != "bound":
                raise NotFoundError("device_not_active")
            connection.execute(
                """
                UPDATE task_delivery_attempts SET outcome = 'superseded', completed_at = ?
                WHERE device_id = ? AND outcome IN ('prepared', 'sent')
                  AND server_session_id IN (
                      SELECT id FROM device_sessions
                      WHERE device_id = ? AND disconnected_at IS NULL
                  )
                """,
                (current, device_id, device_id),
            )
            connection.execute(
                """
                UPDATE device_sessions
                SET disconnected_at = ?, disconnect_reason = 'replaced_by_new_device_session'
                WHERE device_id = ? AND disconnected_at IS NULL
                """,
                (current, device_id),
            )
            connection.execute(
                """
                INSERT INTO device_sessions(
                    id, device_id, protocol_version, connected_at, last_heartbeat_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, device_id, PROTOCOL_VERSION, current, current),
            )
            self._update_device_row(
                connection,
                device_id,
                stored_payload,
                current,
                device_state="stale" if defer_independent_lanes else "fresh",
                device_state_failure_stage=(
                    "device_snapshot_collect" if defer_independent_lanes else "none"
                ),
            )
            connection.execute(
                "UPDATE devices SET online = 1, last_seen_at = ?, updated_at = ? WHERE id = ?",
                (current, current, device_id),
            )
        return session_id

    def reconcile_startup_connections(self) -> None:
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE device_sessions
                SET disconnected_at = ?, disconnect_reason = 'control_plane_restarted'
                WHERE disconnected_at IS NULL
                """,
                (current,),
            )
            connection.execute("UPDATE devices SET online = 0 WHERE online <> 0")

    def fresh_device_session_id(
        self, device_id: str, maximum_idle_seconds: int
    ) -> str | None:
        cutoff = now_seconds() - maximum_idle_seconds
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT s.id
                FROM devices d
                JOIN device_sessions s ON s.device_id = d.id
                WHERE d.id = ? AND d.binding_state = 'bound' AND d.online = 1
                  AND s.disconnected_at IS NULL AND s.last_heartbeat_at > ?
                ORDER BY s.connected_at DESC
                LIMIT 1
                """,
                (device_id, cutoff),
            ).fetchone()
        return row["id"] if row is not None else None

    def device_session_is_current(self, device_id: str, session_id: str) -> bool:
        with self.database.connect() as connection:
            return self._session_is_current(connection, device_id, session_id)

    def device_session_is_fresh(self, device_id: str, maximum_idle_seconds: int) -> bool:
        return self.fresh_device_session_id(device_id, maximum_idle_seconds) is not None

    def device_task_channel_ready(self, device_id: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT capabilities_json FROM devices WHERE id = ? AND binding_state = 'bound'",
                (device_id,),
            ).fetchone()
        if row is None:
            return False
        try:
            capabilities = json.loads(row["capabilities_json"])
        except (TypeError, ValueError):
            return False
        return task_channel_ready(capabilities)

    def task_recovery_safe_after(self, device_id: str, current: int) -> int:
        with self.database.transaction(immediate=True) as connection:
            self._expire_tasks(connection, current, device_id=device_id)
            row = connection.execute(
                """
                SELECT MAX(expires_at) AS maximum_expires_at
                FROM device_tasks
                WHERE device_id = ? AND status IN ('queued', 'dispatched', 'running')
                """,
                (device_id,),
            ).fetchone()
        maximum_expires_at = row["maximum_expires_at"] if row is not None else None
        return max(current, maximum_expires_at or current)

    def mark_device_offline_if_stale(
        self, device_id: str, maximum_idle_seconds: int
    ) -> bool:
        cutoff = now_seconds() - maximum_idle_seconds
        with self.database.transaction(immediate=True) as connection:
            fresh = connection.execute(
                """
                SELECT 1 FROM device_sessions
                WHERE device_id = ? AND disconnected_at IS NULL
                  AND last_heartbeat_at > ?
                LIMIT 1
                """,
                (device_id, cutoff),
            ).fetchone()
            if fresh is not None:
                return False
            row = connection.execute(
                "SELECT online FROM devices WHERE id = ?", (device_id,)
            ).fetchone()
            if row is None or not row["online"]:
                return False
            connection.execute("UPDATE devices SET online = 0 WHERE id = ?", (device_id,))
            return True

    def close_device_session(self, session_id: str, device_id: str, reason: str) -> None:
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE task_delivery_attempts
                SET outcome = 'superseded', completed_at = ?
                WHERE server_session_id = ? AND device_id = ?
                  AND outcome IN ('prepared', 'sent')
                """,
                (current, session_id, device_id),
            )
            connection.execute(
                """
                UPDATE device_sessions
                SET disconnected_at = ?, disconnect_reason = ?
                WHERE id = ? AND device_id = ? AND disconnected_at IS NULL
                """,
                (current, reason[:80], session_id, device_id),
            )
            connection.execute(
                """
                UPDATE devices SET online = 0
                WHERE id = ? AND NOT EXISTS (
                    SELECT 1 FROM device_sessions
                    WHERE device_id = ? AND disconnected_at IS NULL
                )
                """,
                (device_id, device_id),
            )

    def list_devices(self, account_id: str) -> list[dict[str, Any]]:
        current = now_seconds()
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT d.*,
                       (SELECT s.id FROM device_sessions s
                        WHERE s.device_id = d.id AND s.disconnected_at IS NULL
                        ORDER BY s.connected_at DESC, s.id DESC LIMIT 1) AS current_session_id
                      ,(SELECT s.last_heartbeat_at FROM device_sessions s
                        WHERE s.device_id = d.id AND s.disconnected_at IS NULL
                        ORDER BY s.connected_at DESC, s.id DESC LIMIT 1) AS current_session_last_heartbeat_at
                      ,(SELECT s.connected_at FROM device_sessions s
                        WHERE s.device_id = d.id
                        ORDER BY s.connected_at DESC, s.id DESC LIMIT 1) AS latest_session_connected_at
                      ,(SELECT s.last_heartbeat_at FROM device_sessions s
                        WHERE s.device_id = d.id
                        ORDER BY s.connected_at DESC, s.id DESC LIMIT 1) AS latest_session_last_heartbeat_at
                      ,(SELECT s.last_heartbeat_at FROM device_sessions s
                        WHERE s.device_id = d.id
                        ORDER BY s.connected_at DESC, s.id DESC LIMIT 1 OFFSET 1) AS previous_session_last_heartbeat_at
                      ,(SELECT COUNT(*) FROM device_tasks t
                        WHERE t.device_id = d.id
                          AND t.status IN ('queued', 'dispatched', 'running')
                          AND t.expires_at > ?) AS pending_task_count
                      ,(SELECT MIN(t.issued_at) FROM device_tasks t
                        WHERE t.device_id = d.id
                          AND t.status IN ('queued', 'dispatched', 'running')
                          AND t.expires_at > ?) AS oldest_pending_task_issued_at
                      ,(SELECT MIN(t.expires_at) FROM device_tasks t
                        WHERE t.device_id = d.id
                          AND t.status IN ('queued', 'dispatched', 'running')
                          AND t.expires_at > ?) AS nearest_pending_task_expires_at
                FROM devices d WHERE d.owner_account_id = ? ORDER BY d.created_at DESC
                """,
                (current, current, current, account_id),
            ).fetchall()
        return [self._device(row) for row in rows]

    def get_device(self, account_id: str, device_id: str) -> dict[str, Any] | None:
        current = now_seconds()
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT d.*,
                       (SELECT s.id FROM device_sessions s
                        WHERE s.device_id = d.id AND s.disconnected_at IS NULL
                        ORDER BY s.connected_at DESC, s.id DESC LIMIT 1) AS current_session_id
                      ,(SELECT s.last_heartbeat_at FROM device_sessions s
                        WHERE s.device_id = d.id AND s.disconnected_at IS NULL
                        ORDER BY s.connected_at DESC, s.id DESC LIMIT 1) AS current_session_last_heartbeat_at
                      ,(SELECT s.connected_at FROM device_sessions s
                        WHERE s.device_id = d.id
                        ORDER BY s.connected_at DESC, s.id DESC LIMIT 1) AS latest_session_connected_at
                      ,(SELECT s.last_heartbeat_at FROM device_sessions s
                        WHERE s.device_id = d.id
                        ORDER BY s.connected_at DESC, s.id DESC LIMIT 1) AS latest_session_last_heartbeat_at
                      ,(SELECT s.last_heartbeat_at FROM device_sessions s
                        WHERE s.device_id = d.id
                        ORDER BY s.connected_at DESC, s.id DESC LIMIT 1 OFFSET 1) AS previous_session_last_heartbeat_at
                      ,(SELECT COUNT(*) FROM device_tasks t
                        WHERE t.device_id = d.id
                          AND t.status IN ('queued', 'dispatched', 'running')
                          AND t.expires_at > ?) AS pending_task_count
                      ,(SELECT MIN(t.issued_at) FROM device_tasks t
                        WHERE t.device_id = d.id
                          AND t.status IN ('queued', 'dispatched', 'running')
                          AND t.expires_at > ?) AS oldest_pending_task_issued_at
                      ,(SELECT MIN(t.expires_at) FROM device_tasks t
                        WHERE t.device_id = d.id
                          AND t.status IN ('queued', 'dispatched', 'running')
                          AND t.expires_at > ?) AS nearest_pending_task_expires_at
                FROM devices d WHERE d.id = ? AND d.owner_account_id = ?
                """,
                (current, current, current, device_id, account_id),
            ).fetchone()
        return self._device(row) if row is not None else None

    def revoke_device(self, account_id: str, device_id: str) -> bool:
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            return self._revoke_device(connection, account_id, device_id, current)

    def _revoke_device(
        self,
        connection: Any,
        account_id: str,
        device_id: str,
        current: int,
    ) -> bool:
        row = connection.execute(
            "SELECT binding_state FROM devices WHERE id = ? AND owner_account_id = ?",
            (device_id, account_id),
        ).fetchone()
        if row is None:
            return False
        if row["binding_state"] == "revoked":
            return True
        connection.execute(
            """
            UPDATE devices SET binding_state = 'revoked', revoked_at = ?, online = 0, updated_at = ?
            WHERE id = ?
            """,
            (current, current, device_id),
        )
        connection.execute(
            "UPDATE device_credentials SET revoked_at = ? WHERE device_id = ? AND revoked_at IS NULL",
            (current, device_id),
        )
        interrupted = connection.execute(
            """
            SELECT id FROM device_tasks
            WHERE device_id = ? AND status IN ('queued', 'dispatched', 'running')
            """,
            (device_id,),
        ).fetchall()
        for task in interrupted:
            connection.execute(
                """
                UPDATE device_tasks
                SET status = 'rejected', error_code = 'admin_revoked_interrupted',
                    error_message = 'Execution interrupted by controller device revocation',
                    admin_disposition = 'admin_revoked_interrupted', completed_at = ?
                WHERE id = ?
                """,
                (current, task["id"]),
            )
            self._task_event(
                connection,
                task["id"],
                "admin_revoked_interrupted",
                {"device_id": device_id},
            )
        self._audit(connection, account_id, device_id, "device_revoked", True)
        return True

    def create_outbox_reconciliation_authorization(
        self,
        account_id: str,
        old_device_id: str,
        new_device_id: str,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]:
        if old_device_id == new_device_id:
            raise ConflictError("reconciliation_scopes_must_differ")
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            existing = connection.execute(
                """
                SELECT * FROM outbox_reconciliation_authorizations
                WHERE account_id = ? AND idempotency_key = ?
                """,
                (account_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["old_device_id"] != old_device_id
                    or existing["new_device_id"] != new_device_id
                ):
                    raise ConflictError(
                        "reconciliation_idempotency_scope_conflict"
                    )
                return self._outbox_reconciliation(existing), False
            active_exact_scope = connection.execute(
                """
                SELECT * FROM outbox_reconciliation_authorizations
                WHERE account_id = ? AND old_device_id = ? AND new_device_id = ?
                    AND device_acknowledged_at IS NULL AND expires_at > ?
                ORDER BY created_at, id LIMIT 1
                """,
                (account_id, old_device_id, new_device_id, current),
            ).fetchone()
            if active_exact_scope is not None:
                return self._outbox_reconciliation(active_exact_scope), False
            devices = connection.execute(
                """
                SELECT id, owner_account_id, binding_state, installation_id
                FROM devices WHERE id IN (?, ?)
                """,
                (old_device_id, new_device_id),
            ).fetchall()
            by_id = {row["id"]: row for row in devices}
            old_device = by_id.get(old_device_id)
            new_device = by_id.get(new_device_id)
            if (
                old_device is None
                or new_device is None
                or old_device["owner_account_id"] != account_id
                or new_device["owner_account_id"] != account_id
            ):
                raise NotFoundError("device_not_found")
            if old_device["binding_state"] != "revoked":
                raise ConflictError("old_device_not_revoked")
            if new_device["binding_state"] != "bound":
                raise ConflictError("new_device_not_bound")
            if old_device["installation_id"] != new_device["installation_id"]:
                raise ConflictError("reconciliation_installation_mismatch")
            authorization_id = str(uuid.uuid4())
            expires_at = current + self.settings.enrollment_ttl_seconds
            connection.execute(
                """
                INSERT INTO outbox_reconciliation_authorizations(
                    id, account_id, old_device_id, new_device_id, idempotency_key,
                    challenge, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    authorization_id,
                    account_id,
                    old_device_id,
                    new_device_id,
                    idempotency_key,
                    new_opaque_token(32),
                    current,
                    expires_at,
                ),
            )
            self._audit(
                connection,
                account_id,
                new_device_id,
                "outbox_reconciliation_authorized",
                True,
                {"old_device_id": old_device_id},
            )
            row = connection.execute(
                "SELECT * FROM outbox_reconciliation_authorizations WHERE id = ?",
                (authorization_id,),
            ).fetchone()
            return self._outbox_reconciliation(row), True

    def list_outbox_reconciliations(
        self, account_id: str, new_device_id: str
    ) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM outbox_reconciliation_authorizations
                WHERE account_id = ? AND new_device_id = ?
                ORDER BY created_at DESC, id DESC LIMIT 20
                """,
                (account_id, new_device_id),
            ).fetchall()
        return [self._outbox_reconciliation(row) for row in rows]

    def pending_outbox_reconciliation(
        self, new_device_id: str
    ) -> dict[str, Any] | None:
        current = now_seconds()
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM outbox_reconciliation_authorizations
                WHERE new_device_id = ? AND device_acknowledged_at IS NULL AND expires_at > ?
                ORDER BY created_at, id LIMIT 1
                """,
                (new_device_id, current),
            ).fetchone()
        return (
            self._outbox_reconciliation(row, include_challenge=True)
            if row is not None
            else None
        )

    def begin_outbox_reconciliation(
        self,
        new_device_id: str,
        authorization_id: str,
        challenge: str,
        old_scope: str,
        new_scope: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        current = now_seconds()
        manifest_json = canonical_json(items)
        with self.database.transaction(immediate=True) as connection:
            authorization = self._require_outbox_reconciliation(
                connection,
                new_device_id,
                authorization_id,
                challenge,
                old_scope,
                new_scope,
                current,
            )
            existing_manifest = authorization["manifest_json"]
            if existing_manifest is not None and existing_manifest != manifest_json:
                raise ConflictError("reconciliation_manifest_conflict")
            if existing_manifest is None:
                for item in items:
                    task = connection.execute(
                        """
                        SELECT status FROM device_tasks
                        WHERE id = ? AND device_id = ?
                        """,
                        (item["task_id"], old_scope),
                    ).fetchone()
                    if task is None:
                        raise NotFoundError("reconciliation_task_not_found")
                    if task["status"] not in {"expired", "rejected"}:
                        raise ConflictError("reconciliation_task_not_terminal")
                connection.execute(
                    """
                    UPDATE outbox_reconciliation_authorizations
                    SET manifest_json = ?, started_at = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (
                        manifest_json,
                        current,
                        current if not items else None,
                        authorization_id,
                    ),
                )
                self._audit(
                    connection,
                    authorization["account_id"],
                    new_device_id,
                    "outbox_reconciliation_started",
                    True,
                    {"old_device_id": old_scope, "item_count": len(items)},
                )
            completed = connection.execute(
                """
                SELECT completed_at FROM outbox_reconciliation_authorizations
                WHERE id = ?
                """,
                (authorization_id,),
            ).fetchone()
        return {
            "authorization_id": authorization_id,
            "old_scope": old_scope,
            "new_scope": new_scope,
            "item_count": len(items),
            "reconciliation_complete": bool(completed["completed_at"]),
        }

    def reconcile_outbox_pending_result(
        self,
        new_device_id: str,
        authorization_id: str,
        challenge: str,
        old_scope: str,
        new_scope: str,
        task_id: str,
        result_sha256: str,
        result_bytes: int,
        status: str,
        result: Any,
        error_code: str | None,
        error_message: str | None,
    ) -> dict[str, Any]:
        self._validate_reconciliation_manifest_item(
            new_device_id,
            authorization_id,
            challenge,
            old_scope,
            new_scope,
            task_id,
            "PENDING",
            result_sha256,
            result_bytes,
        )
        completion = self.complete_task(
            old_scope,
            task_id,
            status,
            result,
            error_code,
            error_message,
        )
        if completion.kind is TaskCompletionKind.CONFLICT:
            raise ConflictError("reconciliation_result_conflict")
        if not completion.acknowledged or completion.disposition not in {
            "expired_with_late_result",
            "admin_revoked_with_late_result",
        }:
            raise ConflictError("reconciliation_result_not_persistable")
        return self._record_reconciliation_item(
            new_device_id,
            authorization_id,
            challenge,
            old_scope,
            new_scope,
            task_id,
            "PENDING",
            result_sha256,
            result_bytes,
            completion.disposition,
        )

    def reconcile_outbox_running(
        self,
        new_device_id: str,
        authorization_id: str,
        challenge: str,
        old_scope: str,
        new_scope: str,
        task_id: str,
    ) -> dict[str, Any]:
        self._validate_reconciliation_manifest_item(
            new_device_id,
            authorization_id,
            challenge,
            old_scope,
            new_scope,
            task_id,
            "RUNNING",
            "",
            0,
        )
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            task = connection.execute(
                "SELECT status, admin_disposition FROM device_tasks WHERE id = ? AND device_id = ?",
                (task_id, old_scope),
            ).fetchone()
            if task is None:
                raise NotFoundError("reconciliation_task_not_found")
            if task["status"] not in {"expired", "rejected"}:
                raise ConflictError("reconciliation_running_task_not_terminal")
            if task["admin_disposition"] is None:
                connection.execute(
                    """
                    UPDATE device_tasks SET admin_disposition = 'admin_revoked_interrupted'
                    WHERE id = ?
                    """,
                    (task_id,),
                )
                self._task_event(
                    connection, task_id, "admin_revoked_interrupted"
                )
        return self._record_reconciliation_item(
            new_device_id,
            authorization_id,
            challenge,
            old_scope,
            new_scope,
            task_id,
            "RUNNING",
            "",
            0,
            "admin_revoked_interrupted",
        )

    def confirm_outbox_reconciliation(
        self,
        new_device_id: str,
        authorization_id: str,
        challenge: str,
        old_scope: str,
        new_scope: str,
    ) -> dict[str, Any]:
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            authorization = self._require_outbox_reconciliation(
                connection,
                new_device_id,
                authorization_id,
                challenge,
                old_scope,
                new_scope,
                current,
            )
            if authorization["completed_at"] is None:
                raise ConflictError("reconciliation_items_incomplete")
            acknowledged_at = authorization["device_acknowledged_at"]
            if acknowledged_at is None:
                acknowledged_at = current
                connection.execute(
                    """
                    UPDATE outbox_reconciliation_authorizations
                    SET device_acknowledged_at = ? WHERE id = ?
                    """,
                    (acknowledged_at, authorization_id),
                )
                self._audit(
                    connection,
                    authorization["account_id"],
                    new_device_id,
                    "outbox_reconciliation_device_acknowledged",
                    True,
                    {"old_device_id": old_scope},
                )
        return {
            "authorization_id": authorization_id,
            "old_scope": old_scope,
            "new_scope": new_scope,
            "device_acknowledged_at": acknowledged_at,
        }

    def _validate_reconciliation_manifest_item(
        self,
        new_device_id: str,
        authorization_id: str,
        challenge: str,
        old_scope: str,
        new_scope: str,
        task_id: str,
        local_state: str,
        result_sha256: str,
        result_bytes: int,
    ) -> None:
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            authorization = self._require_outbox_reconciliation(
                connection,
                new_device_id,
                authorization_id,
                challenge,
                old_scope,
                new_scope,
                current,
            )
            if authorization["manifest_json"] is None:
                raise ConflictError("reconciliation_not_started")
            manifest = json.loads(authorization["manifest_json"])
            expected = next(
                (item for item in manifest if item["task_id"] == task_id), None
            )
            supplied = {
                "task_id": task_id,
                "local_state": local_state,
                "result_sha256": result_sha256,
                "result_bytes": result_bytes,
            }
            if expected != supplied:
                raise ConflictError("reconciliation_manifest_item_conflict")

    def _record_reconciliation_item(
        self,
        new_device_id: str,
        authorization_id: str,
        challenge: str,
        old_scope: str,
        new_scope: str,
        task_id: str,
        local_state: str,
        result_sha256: str,
        result_bytes: int,
        disposition: str,
    ) -> dict[str, Any]:
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            authorization = self._require_outbox_reconciliation(
                connection,
                new_device_id,
                authorization_id,
                challenge,
                old_scope,
                new_scope,
                current,
            )
            existing = connection.execute(
                """
                SELECT * FROM outbox_reconciliation_items
                WHERE authorization_id = ? AND task_id = ?
                """,
                (authorization_id, task_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["local_state"] != local_state
                    or existing["result_sha256"] != result_sha256
                    or existing["result_bytes"] != result_bytes
                    or existing["disposition"] != disposition
                ):
                    raise ConflictError("reconciliation_item_conflict")
            else:
                connection.execute(
                    """
                    INSERT INTO outbox_reconciliation_items(
                        authorization_id, task_id, local_state, result_sha256,
                        result_bytes, disposition, reconciled_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        authorization_id,
                        task_id,
                        local_state,
                        result_sha256,
                        result_bytes,
                        disposition,
                        current,
                    ),
                )
            manifest = json.loads(authorization["manifest_json"] or "[]")
            item_count = connection.execute(
                """
                SELECT COUNT(*) AS value FROM outbox_reconciliation_items
                WHERE authorization_id = ?
                """,
                (authorization_id,),
            ).fetchone()["value"]
            complete = item_count == len(manifest)
            if complete and authorization["completed_at"] is None:
                connection.execute(
                    """
                    UPDATE outbox_reconciliation_authorizations
                    SET completed_at = ? WHERE id = ?
                    """,
                    (current, authorization_id),
                )
                self._audit(
                    connection,
                    authorization["account_id"],
                    new_device_id,
                    "outbox_reconciliation_completed",
                    True,
                    {"old_device_id": old_scope, "item_count": item_count},
                )
        return {
            "authorization_id": authorization_id,
            "old_scope": old_scope,
            "new_scope": new_scope,
            "task_id": task_id,
            "local_state": local_state,
            "result_sha256": result_sha256,
            "result_bytes": result_bytes,
            "disposition": disposition,
            "reconciliation_complete": complete,
        }

    @staticmethod
    def _require_outbox_reconciliation(
        connection: Any,
        authenticated_new_device_id: str,
        authorization_id: str,
        challenge: str,
        old_scope: str,
        new_scope: str,
        current: int,
    ) -> Mapping[str, Any]:
        authorization = connection.execute(
            """
            SELECT r.*, old.binding_state AS old_binding_state,
                   new.binding_state AS new_binding_state,
                   old.owner_account_id AS old_owner,
                   new.owner_account_id AS new_owner,
                   old.installation_id AS old_installation_id,
                   new.installation_id AS new_installation_id
            FROM outbox_reconciliation_authorizations r
            JOIN devices old ON old.id = r.old_device_id
            JOIN devices new ON new.id = r.new_device_id
            WHERE r.id = ?
            """,
            (authorization_id,),
        ).fetchone()
        if authorization is None:
            raise NotFoundError("reconciliation_authorization_not_found")
        if (
            authenticated_new_device_id != new_scope
            or authorization["new_device_id"] != new_scope
            or authorization["old_device_id"] != old_scope
            or authorization["account_id"] != authorization["old_owner"]
            or authorization["account_id"] != authorization["new_owner"]
            or authorization["old_installation_id"]
            != authorization["new_installation_id"]
        ):
            raise AuthenticationError("reconciliation_scope_mismatch")
        if authorization["old_binding_state"] != "revoked":
            raise ConflictError("old_device_not_revoked")
        if authorization["new_binding_state"] != "bound":
            raise ConflictError("new_device_not_bound")
        if authorization["expires_at"] <= current:
            raise AuthenticationError("reconciliation_authorization_expired")
        if not hmac.compare_digest(authorization["challenge"], challenge):
            raise AuthenticationError("reconciliation_challenge_mismatch")
        return authorization

    @staticmethod
    def _outbox_reconciliation(
        row: Mapping[str, Any], *, include_challenge: bool = False
    ) -> dict[str, Any]:
        value = {
            "id": row["id"],
            "old_device_id": row["old_device_id"],
            "new_device_id": row["new_device_id"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "device_acknowledged_at": row["device_acknowledged_at"],
            "state": (
                "device_acknowledged"
                if row["device_acknowledged_at"] is not None
                else "expired"
                if row["expires_at"] <= now_seconds()
                else "completed"
                if row["completed_at"] is not None
                else "in_progress"
                if row["started_at"] is not None
                else "authorized"
            ),
        }
        if include_challenge:
            value["challenge"] = row["challenge"]
        return value

    def create_task(
        self,
        account_id: str,
        device_id: str,
        task_type: str,
        idempotency_key: str,
        parameters: dict[str, Any],
        ttl_seconds: int,
    ) -> tuple[dict[str, Any], bool]:
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            device = connection.execute(
                """
                SELECT id, capabilities_json, app_version_code FROM devices
                WHERE id = ? AND owner_account_id = ? AND binding_state = 'bound'
                """,
                (device_id, account_id),
            ).fetchone()
            if device is None:
                raise NotFoundError("device_not_found")
            existing = connection.execute(
                "SELECT * FROM device_tasks WHERE device_id = ? AND idempotency_key = ?",
                (device_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                expected_parameters = canonical_json(parameters)
                expected_ttl = existing["expires_at"] - existing["issued_at"]
                if (
                    existing["task_type"] != task_type
                    or existing["parameters_json"] != expected_parameters
                    or expected_ttl != ttl_seconds
                ):
                    raise ConflictError("idempotency_key_reused_with_different_task")
                return self._task(existing), False
            if task_type == "action":
                owning_job = connection.execute(
                    "SELECT id FROM seller_jobs WHERE device_id=? AND status IN ('running','waiting','unknown') ORDER BY created_at,id LIMIT 1",
                    (device_id,),
                ).fetchone()
                if owning_job and not idempotency_key.startswith(f"seller:{owning_job['id']}:"):
                    raise ConflictError("seller_workflow_owns_device")
                other_action = connection.execute(
                    "SELECT id FROM device_tasks WHERE device_id=? AND task_type='action' AND status IN ('queued','dispatched','running') AND expires_at>? LIMIT 1",
                    (device_id,current),
                ).fetchone()
                if owning_job and other_action:
                    raise TaskCapacityError("device_action_in_progress")
            active = connection.execute(
                """
                SELECT COUNT(*) AS count FROM device_tasks
                WHERE device_id = ? AND status IN ('queued', 'dispatched', 'running')
                  AND expires_at > ?
                """,
                (device_id, current),
            ).fetchone()["count"]
            if active >= self.settings.max_active_tasks_per_device:
                raise TaskCapacityError("device_task_capacity_reached")
            capabilities = json.loads(device["capabilities_json"])
            if parameters.get("action") in {"click_unique_node", "set_node_text", "import_image"} and device["app_version_code"] < 35:
                raise UnsupportedDeviceTaskError("seller_agent_upgrade_required")
            if parameters.get("action") == "tap_unique_node" and device["app_version_code"] < 36:
                raise UnsupportedDeviceTaskError("seller_agent_upgrade_required")
            if parameters.get("action") == "set_description_text" and device["app_version_code"] < 41:
                raise UnsupportedDeviceTaskError("seller_agent_upgrade_required")
            if task_type not in capabilities.get("cloud_task_types", []):
                raise UnsupportedDeviceTaskError("device_task_type_unsupported")
            task_id = str(uuid.uuid4())
            expires_at = current + ttl_seconds
            connection.execute(
                """
                INSERT INTO device_tasks(
                    id, device_id, requested_by_account_id, task_type, idempotency_key,
                    status, protocol_version, parameters_json, issued_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                """,
                (
                    task_id,
                    device_id,
                    account_id,
                    task_type,
                    idempotency_key,
                    PROTOCOL_VERSION,
                    canonical_json(parameters),
                    current,
                    expires_at,
                ),
            )
            self._task_event(connection, task_id, "queued")
            self._audit(connection, account_id, device_id, f"task_created:{task_type}", True)
            row = connection.execute("SELECT * FROM device_tasks WHERE id = ?", (task_id,)).fetchone()
            return self._task(row), True

    def list_dispatchable_tasks(self, device_id: str) -> list[dict[str, Any]]:
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            self._expire_tasks(connection, current, device_id=device_id)
            rows = connection.execute(
                """
                SELECT * FROM device_tasks
                WHERE device_id = ? AND status IN ('queued', 'dispatched', 'running')
                  AND expires_at > ?
                ORDER BY issued_at, id
                """,
                (device_id, current),
            ).fetchall()
        return [self._task(row) for row in rows]

    def mark_task_dispatched(
        self,
        device_id: str,
        task_id: str,
        *,
        server_session_id: str | None = None,
        delivery_attempt_id: str | None = None,
        ack_deadline_seconds: int = 0,
    ) -> dict[str, Any] | None:
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            self._expire_tasks(connection, current, device_id=device_id)
            if server_session_id and not self._session_is_current(
                connection, device_id, server_session_id
            ):
                return None
            row = connection.execute(
                "SELECT status, expires_at FROM device_tasks WHERE id = ? AND device_id = ?",
                (task_id, device_id),
            ).fetchone()
            if row is None or row["expires_at"] <= current or row["status"] not in {
                "queued",
                "dispatched",
                "running",
            }:
                return None
            attempt = None
            if delivery_attempt_id:
                attempt = connection.execute(
                    "SELECT outcome FROM task_delivery_attempts WHERE id = ?",
                    (delivery_attempt_id,),
                ).fetchone()
                if attempt is None or attempt["outcome"] not in {"prepared", "acknowledged"}:
                    return None
                ack_deadline = min(
                    current + ack_deadline_seconds,
                    row["expires_at"] - 1,
                )
                if attempt["outcome"] == "prepared" and ack_deadline <= current:
                    return None
                updated = connection.execute(
                    """
                    UPDATE task_delivery_attempts
                    SET sent_at = ?, ack_deadline = ?, outcome = 'sent'
                    WHERE id = ? AND task_id = ? AND device_id = ?
                      AND server_session_id = ? AND outcome = 'prepared'
                    """,
                    (
                        current,
                        ack_deadline,
                        delivery_attempt_id,
                        task_id,
                        device_id,
                        server_session_id,
                    ),
                )
                if attempt["outcome"] == "prepared" and updated.rowcount != 1:
                    return None
                if attempt["outcome"] == "acknowledged":
                    connection.execute(
                        """
                        UPDATE task_delivery_attempts
                        SET sent_at = COALESCE(sent_at, ?)
                        WHERE id = ? AND task_id = ? AND device_id = ?
                          AND server_session_id = ? AND outcome = 'acknowledged'
                        """,
                        (
                            current,
                            delivery_attempt_id,
                            task_id,
                            device_id,
                            server_session_id,
                        ),
                    )
            if row["status"] == "queued":
                connection.execute(
                    "UPDATE device_tasks SET status = 'dispatched' WHERE id = ?", (task_id,)
                )
            self._task_event(
                connection,
                task_id,
                "delivery_attempt",
                self._session_event_detail(
                    server_session_id,
                    {
                        "delivery_attempt_id": delivery_attempt_id,
                        "sent_at": current,
                        "outcome": (
                            "acknowledged"
                            if attempt is not None and attempt["outcome"] == "acknowledged"
                            else "attempted"
                        ),
                    },
                ),
            )
            return {
                "ack_deadline": (
                    ack_deadline
                    if attempt is not None and attempt["outcome"] == "prepared"
                    else None
                ),
                "acknowledged": bool(
                    attempt is not None and attempt["outcome"] == "acknowledged"
                ),
            }

    def prepare_delivery_attempt(
        self,
        device_id: str,
        task_id: str,
        server_session_id: str,
        delivery_protocol: int,
    ) -> dict[str, Any] | None:
        current = now_seconds()
        attempt_id = str(uuid.uuid4())
        with self.database.transaction(immediate=True) as connection:
            self._expire_tasks(connection, current, device_id=device_id)
            if not self._session_is_current(connection, device_id, server_session_id):
                return None
            task = connection.execute(
                """
                SELECT status, expires_at FROM device_tasks
                WHERE id = ? AND device_id = ?
                """,
                (task_id, device_id),
            ).fetchone()
            if (
                task is None
                or task["status"] not in {"queued", "dispatched", "running"}
                or task["expires_at"] <= current
            ):
                return None
            connection.execute(
                """
                INSERT INTO task_delivery_attempts(
                    id, task_id, device_id, server_session_id, delivery_protocol,
                    prepared_at, ack_deadline, outcome
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'prepared')
                """,
                (
                    attempt_id,
                    task_id,
                    device_id,
                    server_session_id,
                    delivery_protocol,
                    current,
                ),
            )
            self._task_event(
                connection,
                task_id,
                "delivery_prepared",
                {
                    "delivery_attempt_id": attempt_id,
                    "server_session_id": server_session_id,
                    "prepared_at": current,
                    "ack_deadline": None,
                    "outcome": "prepared",
                },
            )
        return {
            "id": attempt_id,
            "delivery_protocol": delivery_protocol,
        }

    def fail_delivery_attempt(self, attempt_id: str, outcome: str) -> bool:
        if outcome not in {"send_failed", "ack_timeout", "superseded"}:
            raise ValueError("invalid_delivery_attempt_outcome")
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT task_id FROM task_delivery_attempts WHERE id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                return False
            updated = connection.execute(
                """
                UPDATE task_delivery_attempts SET outcome = ?, completed_at = ?
                WHERE id = ? AND outcome IN ('prepared', 'sent')
                """,
                (outcome, current, attempt_id),
            )
            if updated.rowcount != 1:
                return False
            self._task_event(
                connection,
                row["task_id"],
                "delivery_attempt_outcome",
                {
                    "delivery_attempt_id": attempt_id,
                    "outcome": outcome,
                },
            )
            return True

    def delivery_attempt_is_pending(self, attempt_id: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT outcome FROM task_delivery_attempts WHERE id = ?",
                (attempt_id,),
            ).fetchone()
        return row is not None and row["outcome"] == "sent"

    def acknowledge_delivery_attempt(
        self,
        device_id: str,
        task_id: str,
        server_session_id: str,
        delivery_attempt_id: str | None,
        *,
        require_attempt_id: bool,
    ) -> DeliveryAckKind:
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            self._expire_tasks(connection, current, device_id=device_id)
            if not self._session_is_current(connection, device_id, server_session_id):
                return DeliveryAckKind.REJECTED
            row = connection.execute(
                "SELECT status, expires_at FROM device_tasks WHERE id = ? AND device_id = ?",
                (task_id, device_id),
            ).fetchone()
            if row is None or row["expires_at"] <= current or row["status"] not in {
                "queued",
                "dispatched",
                "running",
            }:
                return DeliveryAckKind.REJECTED
            if require_attempt_id:
                if not delivery_attempt_id:
                    return DeliveryAckKind.REJECTED
                attempt = connection.execute(
                    """
                    SELECT id, outcome, ack_deadline FROM task_delivery_attempts
                    WHERE id = ? AND task_id = ? AND device_id = ?
                      AND server_session_id = ?
                    """,
                    (delivery_attempt_id, task_id, device_id, server_session_id),
                ).fetchone()
                if attempt is not None and attempt["outcome"] in {
                    "acknowledged",
                    "ack_timeout",
                    "superseded",
                }:
                    return DeliveryAckKind.STALE
            else:
                if delivery_attempt_id is not None:
                    return DeliveryAckKind.REJECTED
                attempt = connection.execute(
                    """
                    SELECT id, outcome, ack_deadline FROM task_delivery_attempts
                    WHERE task_id = ? AND device_id = ? AND server_session_id = ?
                      AND outcome IN ('prepared', 'sent')
                    ORDER BY prepared_at DESC LIMIT 1
                    """,
                    (task_id, device_id, server_session_id),
                ).fetchone()
            if attempt is None:
                return DeliveryAckKind.REJECTED
            if attempt["outcome"] not in {"prepared", "sent"}:
                return DeliveryAckKind.REJECTED
            selected_id = attempt["id"]
            if attempt["outcome"] == "sent":
                if attempt["ack_deadline"] is None:
                    return DeliveryAckKind.REJECTED
                if attempt["ack_deadline"] <= current:
                    expired = connection.execute(
                        """
                        UPDATE task_delivery_attempts
                        SET outcome = 'ack_timeout', completed_at = ?
                        WHERE id = ? AND task_id = ? AND device_id = ?
                          AND server_session_id = ? AND outcome = 'sent'
                          AND ack_deadline <= ?
                        """,
                        (
                            current,
                            selected_id,
                            task_id,
                            device_id,
                            server_session_id,
                            current,
                        ),
                    )
                    if expired.rowcount != 1:
                        return DeliveryAckKind.REJECTED
                    self._task_event(
                        connection,
                        task_id,
                        "delivery_attempt_outcome",
                        {
                            "delivery_attempt_id": selected_id,
                            "outcome": "ack_timeout",
                            "ack_deadline": attempt["ack_deadline"],
                        },
                    )
                    return DeliveryAckKind.STALE
            connection.execute(
                """
                UPDATE task_delivery_attempts
                SET outcome = CASE WHEN id = ? THEN 'acknowledged' ELSE 'superseded' END,
                    acknowledged_at = CASE WHEN id = ? THEN ? ELSE acknowledged_at END,
                    completed_at = ?
                WHERE task_id = ? AND device_id = ? AND server_session_id = ?
                  AND outcome IN ('prepared', 'sent')
                """,
                (
                    selected_id,
                    selected_id,
                    current,
                    current,
                    task_id,
                    device_id,
                    server_session_id,
                ),
            )
            if row["status"] != "running":
                connection.execute(
                    """
                    UPDATE device_tasks SET status = 'running',
                        started_at = COALESCE(started_at, ?) WHERE id = ?
                    """,
                    (current, task_id),
                )
                self._task_event(
                    connection,
                    task_id,
                    "running",
                    {
                        "server_session_id": server_session_id,
                        "delivery_attempt_id": selected_id,
                    },
                )
            return DeliveryAckKind.ACKNOWLEDGED

    def mark_task_running(
        self,
        device_id: str,
        task_id: str,
        *,
        server_session_id: str | None = None,
    ) -> bool:
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            if server_session_id and not self._session_is_current(
                connection, device_id, server_session_id
            ):
                return False
            self._expire_tasks(connection, current, device_id=device_id)
            row = connection.execute(
                "SELECT status, expires_at FROM device_tasks WHERE id = ? AND device_id = ?",
                (task_id, device_id),
            ).fetchone()
            if row is None or row["expires_at"] <= current or row["status"] not in {
                "queued",
                "dispatched",
                "running",
            }:
                return False
            if row["status"] != "running":
                connection.execute(
                    "UPDATE device_tasks SET status = 'running', started_at = COALESCE(started_at, ?) WHERE id = ?",
                    (current, task_id),
                )
                self._task_event(
                    connection,
                    task_id,
                    "running",
                    self._session_event_detail(server_session_id),
                )
            return True

    def complete_task(
        self,
        device_id: str,
        task_id: str,
        status: str,
        result: Any,
        error_code: str | None,
        error_message: str | None,
        *,
        server_session_id: str | None = None,
    ) -> TaskCompletion:
        if status not in {"succeeded", "failed", "rejected"}:
            return TaskCompletion(TaskCompletionKind.INVALID_STATUS)
        reported_result = self._reported_result_json(
            status, result, error_code, error_message
        )
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            if server_session_id and not self._session_is_current(
                connection, device_id, server_session_id
            ):
                return TaskCompletion(TaskCompletionKind.NOT_FOUND)
            self._expire_tasks(connection, current, device_id=device_id)
            row = connection.execute(
                """
                SELECT status, result_json, error_code, error_message,
                       reported_result_json, result_disposition, admin_disposition
                FROM device_tasks WHERE id = ? AND device_id = ?
                """,
                (task_id, device_id),
            ).fetchone()
            if row is None:
                return TaskCompletion(TaskCompletionKind.NOT_FOUND)
            if server_session_id:
                connection.execute(
                    """
                    UPDATE task_delivery_attempts
                    SET outcome = 'acknowledged', acknowledged_at = COALESCE(acknowledged_at, ?),
                        completed_at = ?
                    WHERE task_id = ? AND device_id = ? AND server_session_id = ?
                      AND outcome IN ('prepared', 'sent')
                    """,
                    (current, current, task_id, device_id, server_session_id),
                )
            if row["reported_result_json"] is not None:
                if row["reported_result_json"] != reported_result:
                    return TaskCompletion(TaskCompletionKind.CONFLICT)
                return TaskCompletion(
                    TaskCompletionKind.EXACT_DUPLICATE,
                    row["result_disposition"],
                )
            if row["status"] in {"succeeded", "failed", "rejected"}:
                supplied_result = canonical_json(result) if result is not None else None
                legacy_exact = bool(
                    row["status"] == status
                    and row["result_json"] == supplied_result
                    and row["error_code"] == error_code
                    and row["error_message"] == error_message
                )
                if legacy_exact:
                    connection.execute(
                        """
                        UPDATE device_tasks
                        SET reported_result_json = ?, result_received_at = COALESCE(result_received_at, ?),
                            result_disposition = COALESCE(result_disposition, 'on_time')
                        WHERE id = ?
                        """,
                        (reported_result, current, task_id),
                    )
                    return TaskCompletion(
                        TaskCompletionKind.EXACT_DUPLICATE, "on_time"
                    )
                if row["admin_disposition"] != "admin_revoked_interrupted":
                    return TaskCompletion(TaskCompletionKind.CONFLICT)
                disposition = "admin_revoked_with_late_result"
                connection.execute(
                    """
                    UPDATE device_tasks
                    SET reported_result_json = ?, result_received_at = ?,
                        result_disposition = ?
                    WHERE id = ? AND reported_result_json IS NULL
                    """,
                    (reported_result, current, disposition, task_id),
                )
                self._task_event(
                    connection,
                    task_id,
                    disposition,
                    self._session_event_detail(
                        server_session_id, {"reported_status": status}
                    ),
                )
                return TaskCompletion(TaskCompletionKind.STORED, disposition)
            if row["status"] == "expired":
                disposition = "expired_with_late_result"
                connection.execute(
                    """
                    UPDATE device_tasks
                    SET reported_result_json = ?, result_received_at = ?,
                        result_disposition = ?
                    WHERE id = ? AND reported_result_json IS NULL
                    """,
                    (reported_result, current, disposition, task_id),
                )
                self._task_event(
                    connection,
                    task_id,
                    disposition,
                    self._session_event_detail(
                        server_session_id, {"reported_status": status}
                    ),
                )
                return TaskCompletion(TaskCompletionKind.STORED, disposition)
            connection.execute(
                """
                UPDATE device_tasks SET status = ?, result_json = ?, error_code = ?,
                    error_message = ?, reported_result_json = ?, result_received_at = ?,
                    result_disposition = 'on_time', completed_at = ?,
                    started_at = COALESCE(started_at, ?)
                WHERE id = ?
                """,
                (
                    status,
                    canonical_json(result) if result is not None else None,
                    error_code,
                    error_message,
                    reported_result,
                    current,
                    current,
                    current,
                    task_id,
                ),
            )
            self._task_event(
                connection,
                task_id,
                status,
                self._session_event_detail(server_session_id),
            )
            return TaskCompletion(TaskCompletionKind.STORED, "on_time")

    @staticmethod
    def _reported_result_json(
        status: str,
        result: Any,
        error_code: str | None,
        error_message: str | None,
    ) -> str:
        envelope: dict[str, Any] = {"status": status}
        if status == "succeeded":
            envelope["result"] = result
        else:
            envelope["error"] = {
                "code": error_code,
                "message": error_message,
            }
        return canonical_json(envelope)

    def get_task(self, account_id: str, device_id: str, task_id: str) -> dict[str, Any] | None:
        with self.database.transaction(immediate=True) as connection:
            self._expire_tasks(connection, now_seconds(), device_id=device_id)
            row = connection.execute(
                """
                SELECT t.* FROM device_tasks t
                JOIN devices d ON d.id = t.device_id
                WHERE t.id = ? AND t.device_id = ? AND d.owner_account_id = ?
                """,
                (task_id, device_id, account_id),
            ).fetchone()
        return self._task(row) if row is not None else None

    def list_recent_tasks(self, account_id: str, device_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.database.transaction(immediate=True) as connection:
            self._expire_tasks(connection, now_seconds(), device_id=device_id)
            rows = connection.execute(
                """
                SELECT t.* FROM device_tasks t
                JOIN devices d ON d.id = t.device_id
                WHERE t.device_id = ? AND d.owner_account_id = ?
                ORDER BY t.issued_at DESC LIMIT ?
                """,
                (device_id, account_id, min(max(limit, 1), 100)),
            ).fetchall()
        return [self._task(row) for row in rows]

    def cleanup_expired(self) -> None:
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            self._reconcile_stale_sessions(connection, current)
            self._expire_tasks(connection, current)
            connection.execute("DELETE FROM preauth_sessions WHERE expires_at <= ?", (current,))
            connection.execute("DELETE FROM web_sessions WHERE expires_at <= ?", (current,))
            connection.execute(
                "DELETE FROM mobile_sessions WHERE expires_at <= ? OR revoked_at IS NOT NULL",
                (current,),
            )
            connection.execute(
                """
                DELETE FROM enrollment_challenges
                WHERE expires_at <= ? OR (used_at IS NOT NULL AND used_at <= ?)
                """,
                (current, current - 3600),
            )
            connection.execute(
                """
                DELETE FROM device_revocation_challenges
                WHERE expires_at <= ? OR (used_at IS NOT NULL AND used_at <= ?)
                """,
                (current, current - 3600),
            )

    def _reconcile_stale_sessions(self, connection: Any, current: int) -> None:
        cutoff = current - self.settings.heartbeat_timeout_seconds
        connection.execute(
            """
            UPDATE device_sessions
            SET disconnected_at = ?, disconnect_reason = 'heartbeat_timeout_reconciled'
            WHERE disconnected_at IS NULL AND last_heartbeat_at <= ?
            """,
            (current, cutoff),
        )
        connection.execute(
            """
            UPDATE devices SET online = 0
            WHERE online <> 0 AND NOT EXISTS (
                SELECT 1 FROM device_sessions s
                WHERE s.device_id = devices.id AND s.disconnected_at IS NULL
                  AND s.last_heartbeat_at > ?
            )
            """,
            (cutoff,),
        )

    def maybe_cleanup_expired(self, *, force: bool = False) -> bool:
        current_monotonic = time.monotonic()
        interval = max(0, self.settings.cleanup_interval_seconds)
        if not force and current_monotonic - self._last_cleanup_monotonic < interval:
            return False
        with self._cleanup_lock:
            current_monotonic = time.monotonic()
            if not force and current_monotonic - self._last_cleanup_monotonic < interval:
                return False
            self.cleanup_expired()
            self._last_cleanup_monotonic = current_monotonic
            return True

    def _expire_tasks(
        self,
        connection: Any,
        current: int,
        *,
        device_id: str | None = None,
    ) -> int:
        query = """
            SELECT id FROM device_tasks
            WHERE status IN ('queued', 'dispatched', 'running') AND expires_at <= ?
        """
        parameters: tuple[Any, ...] = (current,)
        if device_id is not None:
            query += " AND device_id = ?"
            parameters = (current, device_id)
        rows = connection.execute(query, parameters).fetchall()
        for row in rows:
            connection.execute(
                """
                UPDATE device_tasks SET status = 'expired', error_code = 'task_expired',
                    error_message = 'Task expired before completion', completed_at = ?
                WHERE id = ?
                """,
                (current, row["id"]),
            )
            self._task_event(connection, row["id"], "expired")
        return len(rows)

    def _device(self, row: Mapping[str, Any]) -> dict[str, Any]:
        info = json.loads(row["info_json"])
        capabilities = json.loads(row["capabilities_json"])
        recovery_protocol = task_store_protocol(capabilities)
        channel_ready = task_channel_ready(capabilities)
        current = now_seconds()
        row_keys = row.keys()
        last_seen_at = row["last_seen_at"]
        open_session_id = (
            row["current_session_id"] if "current_session_id" in row_keys else None
        )
        open_session_heartbeat_at = (
            row["current_session_last_heartbeat_at"]
            if "current_session_last_heartbeat_at" in row_keys
            else None
        )
        online = bool(
            row["online"]
            and last_seen_at is not None
            and last_seen_at > current - self.settings.dispatch_freshness_seconds
            and open_session_id is not None
            and open_session_heartbeat_at is not None
            and open_session_heartbeat_at
            > current - self.settings.dispatch_freshness_seconds
        )
        authoritative_session_id = open_session_id if online else None
        transport_activity_age_seconds = (
            max(0, current - last_seen_at) if last_seen_at is not None else None
        )
        offline_duration_seconds = (
            transport_activity_age_seconds if not online else None
        )
        latest_connected_at = (
            row["latest_session_connected_at"]
            if "latest_session_connected_at" in row_keys
            else None
        )
        previous_heartbeat_at = (
            row["previous_session_last_heartbeat_at"]
            if "previous_session_last_heartbeat_at" in row_keys
            else None
        )
        last_reconnect_gap_seconds = (
            max(0, latest_connected_at - previous_heartbeat_at)
            if latest_connected_at is not None
            and previous_heartbeat_at is not None
            else None
        )
        pending_task_count = (
            row["pending_task_count"] if "pending_task_count" in row_keys else 0
        ) or 0
        oldest_pending_task_issued_at = (
            row["oldest_pending_task_issued_at"]
            if "oldest_pending_task_issued_at" in row_keys
            else None
        )
        nearest_pending_task_expires_at = (
            row["nearest_pending_task_expires_at"]
            if "nearest_pending_task_expires_at" in row_keys
            else None
        )
        device_state_meta = info.get("_device_state")
        if not isinstance(device_state_meta, dict):
            device_state_meta = {}
        device_state_updated_at = device_state_meta.get("updated_at")
        device_state_fresh = bool(
            device_state_meta.get("state") == "fresh"
            and isinstance(device_state_updated_at, int)
            and device_state_updated_at > current - self.DEVICE_STATE_FRESH_SECONDS
        )
        task_channel_meta = info.get("_task_channel")
        if not isinstance(task_channel_meta, dict):
            task_channel_meta = {}
        reasons: list[str] = []
        if not online:
            reasons.append("device_offline")
        if recovery_protocol is not None and not channel_ready:
            reasons.append("task_channel_not_ready")
        if not device_state_fresh:
            reasons.append("device_state_stale")
        if not capabilities.get("accessibility_enabled"):
            reasons.append("accessibility_not_enabled")
        elif not capabilities.get("accessibility_connected"):
            reasons.append("accessibility_not_connected")
        if not capabilities.get("battery_optimization_ignored"):
            reasons.append("battery_optimization_not_ignored")
        if not capabilities.get("xianyu_installed"):
            reasons.append("xianyu_not_installed")
        elif not capabilities.get("xianyu_rules_verified"):
            reasons.append("xianyu_live_compatibility_unverified")
        if row["binding_state"] == "revoked":
            state = "REVOKED"
        elif not online:
            state = "BOUND_OFFLINE"
        elif recovery_protocol is not None and not channel_ready:
            state = "ONLINE_DIAGNOSTIC_ONLY"
        elif not device_state_fresh:
            state = "ONLINE_TASK_READY_DEVICE_STALE"
        elif capabilities.get("accessibility_connected"):
            state = "ONLINE_READY"
        else:
            state = "DEGRADED"
        return {
            "id": row["id"],
            "display_name": row["display_name"],
            "manufacturer": row["manufacturer"],
            "brand": row["brand"],
            "model": row["model"],
            "android_version": row["android_version"],
            "sdk": row["sdk"],
            "app_version": row["app_version"],
            "app_version_code": row["app_version_code"],
            "kernel_version": row["kernel_version"],
            "timezone": row["timezone"],
            "network_type": row["network_type"],
            "capabilities": capabilities,
            "diagnostics": info.get("diagnostics"),
            # Mobile diagnostics are display-only. Only a fresh open server session
            # may appear as the authoritative realtime session.
            "current_session_id": authoritative_session_id,
            "binding_state": row["binding_state"],
            "online": online,
            "transport_activity_age_seconds": transport_activity_age_seconds,
            "offline_duration_seconds": offline_duration_seconds,
            "last_reconnect_gap_seconds": last_reconnect_gap_seconds,
            "pending_task_count": pending_task_count,
            "oldest_pending_task_issued_at": oldest_pending_task_issued_at,
            "oldest_pending_task_age_seconds": (
                max(0, current - oldest_pending_task_issued_at)
                if oldest_pending_task_issued_at is not None
                else None
            ),
            "nearest_pending_task_expires_at": nearest_pending_task_expires_at,
            "nearest_pending_task_ttl_seconds": (
                max(0, nearest_pending_task_expires_at - current)
                if nearest_pending_task_expires_at is not None
                else None
            ),
            "task_store_protocol": recovery_protocol,
            "task_channel_ready": channel_ready,
            "task_store_state": capabilities.get("task_store_state"),
            "task_channel_failure_stage": task_channel_meta.get(
                "failure_stage", "none"
            ),
            "task_channel_updated_at": task_channel_meta.get("updated_at"),
            "device_state": "fresh" if device_state_fresh else "stale",
            "device_state_failure_stage": device_state_meta.get(
                "failure_stage", "device_snapshot_collect"
            ),
            "device_state_updated_at": device_state_updated_at,
            "state": state,
            "status_reasons": reasons,
            "last_seen_at": last_seen_at,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "revoked_at": row["revoked_at"],
        }

    @staticmethod
    def _task(row: Mapping[str, Any]) -> dict[str, Any]:
        reported_result = (
            json.loads(row["reported_result_json"])
            if row["reported_result_json"]
            else None
        )
        effective_status = (
            row["result_disposition"]
            or row["admin_disposition"]
            or row["status"]
        )
        return {
            "id": row["id"],
            "device_id": row["device_id"],
            "task_type": row["task_type"],
            "idempotency_key": row["idempotency_key"],
            "status": row["status"],
            "effective_status": effective_status,
            "protocol_version": row["protocol_version"],
            "parameters": json.loads(row["parameters_json"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "reported_result": reported_result,
            "result_received_at": row["result_received_at"],
            "result_disposition": row["result_disposition"],
            "admin_disposition": row["admin_disposition"],
            "error": (
                {"code": row["error_code"], "message": row["error_message"]}
                if row["error_code"] or row["error_message"]
                else None
            ),
            "issued_at": row["issued_at"],
            "expires_at": row["expires_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
        }

    @staticmethod
    def _session_is_current(
        connection: Any, device_id: str, session_id: str
    ) -> bool:
        row = connection.execute(
            """
            SELECT id FROM device_sessions
            WHERE device_id = ? AND id = ? AND disconnected_at IS NULL
            ORDER BY connected_at DESC LIMIT 1
            """,
            (device_id, session_id),
        ).fetchone()
        if row is None:
            return False
        newest = connection.execute(
            """
            SELECT id FROM device_sessions
            WHERE device_id = ? AND disconnected_at IS NULL
            ORDER BY connected_at DESC LIMIT 1
            """,
            (device_id,),
        ).fetchone()
        return newest is not None and newest["id"] == session_id

    @staticmethod
    def _session_event_detail(
        server_session_id: str | None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = dict(detail or {})
        if server_session_id:
            result["server_session_id"] = server_session_id
        return result

    @staticmethod
    def _task_event(
        connection: Any, task_id: str, event_type: str, detail: dict[str, Any] | None = None
    ) -> None:
        connection.execute(
            "INSERT INTO task_events(task_id, event_type, created_at, detail_json) VALUES (?, ?, ?, ?)",
            (task_id, event_type, now_seconds(), canonical_json(detail or {})),
        )

    @staticmethod
    def _audit(
        connection: Any,
        account_id: str | None,
        device_id: str | None,
        action: str,
        success: bool,
        detail: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_logs(account_id, device_id, action, success, created_at, detail_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (account_id, device_id, action, 1 if success else 0, now_seconds(), canonical_json(detail or {})),
        )

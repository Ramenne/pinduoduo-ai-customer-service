from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import unicodedata
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils


PASSWORD_SCHEME = "pbkdf2_sha256"
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,48}$")
EMAIL_PATTERN = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}$")
BASE64URL_PATTERN = re.compile(r"^[A-Za-z0-9_-]*$")
P256_ORDER = int("FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551", 16)
MAX_JCS_INTEGER = 9_007_199_254_740_991


class SecurityError(ValueError):
    pass


def now_seconds() -> int:
    import time

    return int(time.time())


def new_opaque_token(size: int = 32) -> str:
    return secrets.token_urlsafe(size)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def b64url_decode(value: str, *, max_bytes: int = 16_384) -> bytes:
    if (
        not isinstance(value, str)
        or len(value) > max_bytes * 2
        or len(value) % 4 == 1
        or not BASE64URL_PATTERN.fullmatch(value)
    ):
        raise SecurityError("invalid_base64url")
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, TypeError, binascii.Error) as exc:
        raise SecurityError("invalid_base64url") from exc
    if len(decoded) > max_bytes:
        raise SecurityError("decoded_value_too_large")
    if b64url_encode(decoded) != value:
        raise SecurityError("invalid_base64url")
    return decoded


def normalize_identifier(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def validate_username(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise SecurityError("username_must_be_3_to_48_safe_characters")
    return normalized


def validate_email(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = unicodedata.normalize("NFKC", value).strip()
    if len(normalized) > 254 or not EMAIL_PATTERN.fullmatch(normalized):
        raise SecurityError("invalid_email")
    return normalized


def validate_password(value: str) -> None:
    if len(value) < 12 or len(value) > 128:
        raise SecurityError("password_must_be_12_to_128_characters")
    if value.strip() != value:
        raise SecurityError("password_must_not_start_or_end_with_whitespace")


def hash_password(password: str, iterations: int) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)
    return "$".join(
        [PASSWORD_SCHEME, str(iterations), b64url_encode(salt), b64url_encode(digest)]
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(iterations_text)
        if iterations < 100_000 or iterations > 10_000_000:
            return False
        salt = b64url_decode(salt_text, max_bytes=64)
        expected = b64url_decode(digest_text, max_bytes=64)
        supplied = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations, dklen=len(expected)
        )
        return hmac.compare_digest(expected, supplied)
    except (ValueError, SecurityError, UnicodeError):
        return False


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_payload_digest(payload: dict[str, Any]) -> str:
    _validate_canonical_payload_value(payload)
    try:
        encoded = canonical_json(payload).encode("utf-8")
    except UnicodeError as exc:
        raise SecurityError("canonical_payload_invalid_unicode") from exc
    return b64url_encode(hashlib.sha256(encoded).digest())


def _jcs_sort_key(value: str) -> bytes:
    return value.encode("utf-16-be", errors="surrogatepass")


def _jcs_serialize(value: Any) -> str:
    """Serialize the integer-only RFC 8785 subset used by runtime-v2.

    This deliberately remains separate from the legacy v1 canonical payload
    function because changing the latter would invalidate deployed Android
    signatures.
    """

    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) > MAX_JCS_INTEGER:
            raise SecurityError("jcs_integer_out_of_range")
        return str(value)
    if isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise SecurityError("jcs_invalid_unicode") from exc
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_jcs_serialize(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise SecurityError("jcs_key_must_be_text")
        members = (
            _jcs_serialize(key) + ":" + _jcs_serialize(value[key])
            for key in sorted(value, key=_jcs_sort_key)
        )
        return "{" + ",".join(members) + "}"
    raise SecurityError("jcs_value_outside_integer_only_subset")


def canonical_jcs_bytes(value: dict[str, Any]) -> bytes:
    if not isinstance(value, dict):
        raise SecurityError("jcs_top_level_must_be_object")
    try:
        return _jcs_serialize(value).encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise SecurityError("jcs_invalid_unicode") from exc


def canonical_jcs_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_jcs_bytes(value)).hexdigest()


def _validate_canonical_payload_value(value: Any) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        if value < -(2**63) or value > 2**63 - 1:
            raise SecurityError("canonical_payload_integer_out_of_range")
        return
    if isinstance(value, list):
        for item in value:
            _validate_canonical_payload_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise SecurityError("canonical_payload_key_must_be_text")
            _validate_canonical_payload_value(item)
        return
    raise SecurityError("canonical_payload_non_integer_number_or_type")


@dataclass(frozen=True, slots=True)
class PublicKey:
    der: bytes
    fingerprint: str
    key: ec.EllipticCurvePublicKey


def load_p256_public_key(encoded_der: str) -> PublicKey:
    der = b64url_decode(encoded_der, max_bytes=1024)
    try:
        key = serialization.load_der_public_key(der)
    except (ValueError, TypeError) as exc:
        raise SecurityError("invalid_public_key") from exc
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise SecurityError("public_key_must_be_p256")
    canonical_der = key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return PublicKey(
        der=canonical_der,
        fingerprint=hashlib.sha256(canonical_der).hexdigest(),
        key=key,
    )


def p256_key_id(public_key: PublicKey) -> str:
    digest = hashlib.sha256(public_key.der).digest()
    return f"p256-spki-sha256-{b64url_encode(digest)}"


def load_p256_public_key_der(der: bytes) -> ec.EllipticCurvePublicKey:
    try:
        key = serialization.load_der_public_key(der)
    except (ValueError, TypeError) as exc:
        raise SecurityError("invalid_stored_public_key") from exc
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise SecurityError("stored_public_key_must_be_p256")
    return key


def verify_ecdsa_signature(
    public_key_der: bytes,
    message: str,
    signature_b64url: str,
) -> bool:
    try:
        signature = b64url_decode(signature_b64url, max_bytes=256)
        r, s = utils.decode_dss_signature(signature)
        if not (1 <= r < P256_ORDER and 1 <= s < P256_ORDER):
            return False
        if utils.encode_dss_signature(r, s) != signature:
            return False
        key = load_p256_public_key_der(public_key_der)
        key.verify(signature, message.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, SecurityError, ValueError, UnicodeError):
        return False


def verify_p256_p1363_signature(
    public_key_der: bytes,
    message: bytes,
    signature_b64url: str,
) -> bool:
    """Verify the runtime-v2 fixed-width, canonical low-S signature format."""

    try:
        signature = b64url_decode(signature_b64url, max_bytes=64)
        if len(signature) != 64:
            return False
        r = int.from_bytes(signature[:32], "big")
        s = int.from_bytes(signature[32:], "big")
        if not (1 <= r < P256_ORDER and 1 <= s <= P256_ORDER // 2):
            return False
        key = load_p256_public_key_der(public_key_der)
        key.verify(
            utils.encode_dss_signature(r, s),
            message,
            ec.ECDSA(hashes.SHA256()),
        )
        return True
    except (InvalidSignature, SecurityError, ValueError, UnicodeError):
        return False


def enrollment_message(challenge_id: str, nonce: str, payload: dict[str, Any]) -> str:
    return "\n".join(
        ["oin-v1-enroll", challenge_id, nonce, canonical_payload_digest(payload)]
    )


def websocket_auth_message(
    device_id: str,
    challenge_id: str,
    nonce: str,
    issued_at: int,
    payload: dict[str, Any],
) -> str:
    return "\n".join(
        [
            "oin-v1-wss-auth",
            device_id,
            challenge_id,
            nonce,
            str(issued_at),
            canonical_payload_digest(payload),
        ]
    )


def websocket_transport_auth_message(
    device_id: str,
    challenge_id: str,
    nonce: str,
    issued_at: int,
    transport_auth: dict[str, Any],
) -> str:
    return "\n".join(
        [
            "oin-v1-wss-transport-auth",
            device_id,
            challenge_id,
            nonce,
            str(issued_at),
            canonical_payload_digest(transport_auth),
        ]
    )


def revocation_message(device_id: str, challenge_id: str, nonce: str) -> str:
    return "\n".join(["oin-v1-revoke", device_id, challenge_id, nonce])


def credential_repair_message(
    device_id: str,
    challenge_id: str,
    nonce: str,
    payload: dict[str, Any],
) -> str:
    """Proof-of-possession message for a controller-authorized key rotation."""

    return "\n".join(
        [
            "oin-v1-credential-repair",
            device_id,
            challenge_id,
            nonce,
            canonical_payload_digest(payload),
        ]
    )


def safe_text(value: Any, *, maximum: int, field: str, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise SecurityError(f"{field}_must_be_text")
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not allow_empty and not normalized:
        raise SecurityError(f"{field}_required")
    if len(normalized) > maximum or any(ord(character) < 32 for character in normalized):
        raise SecurityError(f"{field}_invalid")
    return normalized


def safe_integer(value: Any, *, minimum: int, maximum: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SecurityError(f"{field}_must_be_integer")
    if value < minimum or value > maximum:
        raise SecurityError(f"{field}_out_of_range")
    return value

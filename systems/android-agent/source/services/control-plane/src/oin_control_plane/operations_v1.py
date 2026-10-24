from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .config import Settings
from .database import Database, IntegrityError
from .security import (
    SecurityError,
    b64url_decode,
    b64url_encode,
    canonical_jcs_bytes,
    canonical_jcs_sha256,
    canonical_json,
    load_p256_public_key,
    new_opaque_token,
    now_seconds,
    p256_key_id,
    token_hash,
    verify_p256_p1363_signature,
)


IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
PLATFORM_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$")
IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
HEX_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
UTC_PATTERN = re.compile(
    r"^(?!0000)[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
PDD_CHAT_TARGET_URL = "https://mms.pinduoduo.com/chat-merchant/index.html#/"
ROUTE_ACTIVATION_DOMAIN = b"OIN-WEB-ROUTE-ACTIVATION-V2\n"
ROUTE_ACTIVATION_NONCE_DOMAIN = b"OIN-WEB-ROUTE-ACTIVATION-NONCE-V2\0"
PDD_IDENTITY_SOURCE_KIND = "pinduoduo.webview2.identity_api"
PDD_IDENTITY_SOURCE_VERSION = "wv1"
PDD_RUNTIME_ADAPTER = "webview2"
PDD_IDENTITY_EVIDENCE_MAX_TTL_SECONDS = 300
PDD_IDENTITY_CLOCK_SKEW_SECONDS = 300
PDD_IDENTITY_INGRESS_CONTRACT = "oin.pdd.identity_observation_ingress"
PDD_IDENTITY_INGRESS_DOMAIN = b"OIN-PDD-IDENTITY-OBSERVATION-V1\n"
PDD_IDENTITY_INGRESS_MAX_BYTES = 16 * 1024
PDD_IDENTITY_INGRESS_NONCE_BYTES = 24
PDD_IDENTITY_INGRESS_MAX_AGE_SECONDS = 300
PDD_IDENTITY_INGRESS_FUTURE_SKEW_SECONDS = 30
PDD_PENDING_EVIDENCE_LIMIT = 50


class OperationsError(RuntimeError):
    code = "operations_error"


class OperationsNotFound(OperationsError):
    code = "not_found"


class OperationsConflict(OperationsError):
    code = "conflict"


class OperationsAuthentication(OperationsError):
    code = "authentication_failed"


class OperationsForbidden(OperationsError):
    code = "forbidden"


@dataclass(frozen=True, slots=True)
class AuthenticatedConnectorContext:
    """Connector identity established by verified P-256 transport admission."""

    tenant_id: str
    connector_id: str
    connector_installation_id: str
    registered_key_id: str
    registered_public_key_spki_sha256: str


@dataclass(frozen=True, slots=True)
class _PddPublicLegacyMapper:
    public_platform: str = "pinduoduo"
    storage_platform: str = "pdd"
    public_adapter: str = "pinduoduo.mms.web"
    storage_adapter: str = "pdd_web"
    runtime_kind: str = "windows_web"

    def platform_to_storage(self, value: Any) -> str:
        if _platform_key(value, "platform") != self.public_platform:
            raise SecurityError("platform_unsupported")
        return self.storage_platform

    def adapter_to_storage(self, value: Any) -> str:
        if _platform_key(value, "adapter_id") != self.public_adapter:
            raise SecurityError("adapter_id_unsupported")
        return self.storage_adapter

    def platform_to_public(self, value: Any) -> str:
        if value != self.storage_platform:
            raise OperationsConflict("platform_mapping_invalid")
        return self.public_platform

    def adapter_to_public(self, value: Any) -> str:
        if value != self.storage_adapter:
            raise OperationsConflict("adapter_mapping_invalid")
        return self.public_adapter

    def require_runtime(self, value: Any) -> str:
        if _platform_key(value, "runtime_kind") != self.runtime_kind:
            raise SecurityError("runtime_kind_invalid")
        return self.runtime_kind


PDD_ROUTE = _PddPublicLegacyMapper()


@dataclass(frozen=True, slots=True)
class OperationOutcome:
    value: dict[str, Any]
    replayed: bool = False


def _closed_object(
    value: Any,
    *,
    required: set[str],
    optional: set[str] | None = None,
    field: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SecurityError(f"{field}_must_be_object")
    optional = optional or set()
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        raise SecurityError(f"{field}_fields_invalid")
    return value


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise SecurityError(f"{field}_invalid")
    return value


def validate_route_identifier(value: Any, field: str) -> str:
    return _identifier(value, field)


def _platform_key(value: Any, field: str) -> str:
    if not isinstance(value, str) or not PLATFORM_KEY_PATTERN.fullmatch(value):
        raise SecurityError(f"{field}_invalid")
    return value


def _version(value: Any, field: str) -> str:
    if not isinstance(value, str) or not VERSION_PATTERN.fullmatch(value):
        raise SecurityError(f"{field}_invalid")
    return value


def _idempotency_key(value: Any) -> str:
    if not isinstance(value, str) or not IDEMPOTENCY_PATTERN.fullmatch(value):
        raise SecurityError("idempotency_key_invalid")
    return value


def _bounded_text(value: Any, *, field: str, maximum: int, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise SecurityError(f"{field}_invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise SecurityError(f"{field}_invalid")
    return value


def _provider_identifier(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _bounded_text(value, field=field, maximum=256)


def _positive_integer(value: Any, field: str, *, maximum: int = 9_007_199_254_740_991) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > maximum:
        raise SecurityError(f"{field}_invalid")
    return value


def _utc_text(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_seconds(value: Any, field: str) -> int:
    if not isinstance(value, str) or not UTC_PATTERN.fullmatch(value):
        raise SecurityError(f"{field}_invalid")
    try:
        timestamp_format = "%Y-%m-%dT%H:%M:%S.%f" if "." in value else "%Y-%m-%dT%H:%M:%S"
        parsed = datetime.strptime(value[:-1], timestamp_format).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise SecurityError(f"{field}_invalid") from exc
    return int(parsed.timestamp())


def _exact_utc_seconds(value: Any, field: str) -> int:
    seconds = _utc_seconds(value, field)
    if value != _utc_text(seconds):
        raise SecurityError(f"{field}_must_use_utc_second_precision")
    return seconds


def pdd_identity_evidence_sha256(observation: Mapping[str, Any]) -> str:
    """Digest the closed evidence/admission material, excluding its digest field."""

    try:
        evidence = dict(observation["evidence"])
        evidence.pop("evidence_sha256")
        material = {
            "contract": observation["contract"],
            "evidence": evidence,
            "admission": observation["admission"],
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise SecurityError("identity_evidence_digest_material_invalid") from exc
    return canonical_jcs_sha256(material)


def pdd_identity_ingress_signing_bytes(frame: Mapping[str, Any]) -> bytes:
    """Return the fixed domain-separated JCS bytes signed by an installation key."""

    try:
        unsigned = {
            "contract": frame["contract"],
            "connector_installation_id": frame["connector_installation_id"],
            "issued_at": frame["issued_at"],
            "nonce": frame["nonce"],
            "observation": frame["observation"],
        }
    except (KeyError, TypeError) as exc:
        raise SecurityError("identity_ingress_signing_material_invalid") from exc
    return PDD_IDENTITY_INGRESS_DOMAIN + canonical_jcs_bytes(unsigned)


def pdd_identity_authority_fence_sha256(
    *,
    route: Mapping[str, Any],
    authority: Mapping[str, Any],
    runtime_session_id: Any,
) -> str:
    """Return the v1 WebView2 authority fence over public route semantics."""

    route = _closed_object(
        dict(route),
        required={
            "tenant_id",
            "merchant_id",
            "shop_id",
            "principal_id",
            "connector_id",
            "profile_id",
            "profile_generation",
            "binding_id",
            "binding_generation",
            "platform",
            "adapter_id",
            "adapter_version",
            "runtime_kind",
        },
        field="identity_authority_fence_route",
    )
    normalized_route = {
        "tenant_id": _identifier(route["tenant_id"], "tenant_id"),
        "merchant_id": _identifier(route["merchant_id"], "merchant_id"),
        "shop_id": _identifier(route["shop_id"], "shop_id"),
        "principal_id": _identifier(route["principal_id"], "principal_id"),
        "connector_id": _identifier(route["connector_id"], "connector_id"),
        "profile_id": _identifier(route["profile_id"], "profile_id"),
        "profile_generation": _positive_integer(
            route["profile_generation"], "profile_generation"
        ),
        "binding_id": _identifier(route["binding_id"], "binding_id"),
        "binding_generation": _positive_integer(
            route["binding_generation"], "binding_generation"
        ),
        "platform": PDD_ROUTE.platform_to_public(
            PDD_ROUTE.platform_to_storage(route["platform"])
        ),
        "adapter_id": PDD_ROUTE.adapter_to_public(
            PDD_ROUTE.adapter_to_storage(route["adapter_id"])
        ),
        "adapter_version": _version(route["adapter_version"], "adapter_version"),
        "runtime_kind": PDD_ROUTE.require_runtime(route["runtime_kind"]),
    }
    authority = _closed_object(
        dict(authority),
        required={
            "shop_revision",
            "principal_revision",
            "binding_revision",
            "binding_lifecycle_state",
            "identity_state",
            "verified_identity_revision",
        },
        field="identity_authority_fence_state",
    )
    if authority["binding_lifecycle_state"] not in {"PENDING", "ACTIVE"}:
        raise SecurityError("binding_lifecycle_state_invalid")
    if authority["identity_state"] not in {"PENDING", "VERIFIED"}:
        raise SecurityError("identity_state_invalid")
    verified_revision = authority["verified_identity_revision"]
    if authority["identity_state"] == "PENDING":
        if verified_revision is not None:
            raise SecurityError("pending_identity_has_verified_revision")
    else:
        verified_revision = _positive_integer(
            verified_revision, "verified_identity_revision"
        )
    normalized_authority = {
        "shop_revision": _positive_integer(
            authority["shop_revision"], "shop_revision"
        ),
        "principal_revision": _positive_integer(
            authority["principal_revision"], "principal_revision"
        ),
        "binding_revision": _positive_integer(
            authority["binding_revision"], "binding_revision"
        ),
        "binding_lifecycle_state": authority["binding_lifecycle_state"],
        "identity_state": authority["identity_state"],
        "verified_identity_revision": verified_revision,
    }
    session_id = _identifier(runtime_session_id, "runtime_session_id")
    return canonical_jcs_sha256(
        {
            "contract": {
                "name": "oin.pdd.identity_authority_fence",
                "version": 1,
            },
            "route": normalized_route,
            "authority": normalized_authority,
            "runtime": {
                "runtime_adapter": PDD_RUNTIME_ADAPTER,
                "runtime_session_id": session_id,
            },
        }
    )


def _entity_meta(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "revision": int(row["revision"]),
        "created_at": _utc_text(int(row["created_at"])),
        "updated_at": _utc_text(int(row["updated_at"])),
    }


def _provider_identity(value: Any, *, kind: str) -> dict[str, Any]:
    prefix = "provider_platform_shop" if kind == "shop" else "provider_platform_principal"
    value = _closed_object(
        value,
        required={f"{prefix}_id", f"{prefix}_ref", "identity_quality"},
        field="provider_identity",
    )
    provider_id = _provider_identifier(value[f"{prefix}_id"], f"{prefix}_id")
    provider_ref = _provider_identifier(value[f"{prefix}_ref"], f"{prefix}_ref")
    quality = value["identity_quality"]
    if quality not in {"strong", "medium", "weak", "unknown"}:
        raise SecurityError("identity_quality_invalid")
    if quality == "strong" and provider_id is None:
        raise SecurityError("strong_identity_requires_provider_id")
    if quality != "unknown" and provider_id is None and provider_ref is None:
        raise SecurityError("provider_identity_missing")
    return {
        f"{prefix}_id": provider_id,
        f"{prefix}_ref": provider_ref,
        "identity_quality": quality,
    }


def _validate_capabilities(value: Any) -> dict[str, Any]:
    value = _closed_object(
        value,
        required={
            "max_registered_profiles",
            "max_active_profiles",
            "durable_inbox",
            "durable_outbox",
            "persistent_ack",
            "runtime_kinds",
            "wire_limits",
            "queue_limits",
        },
        field="capabilities",
    )
    registered = _positive_integer(value["max_registered_profiles"], "max_registered_profiles", maximum=64)
    active = _positive_integer(value["max_active_profiles"], "max_active_profiles", maximum=64)
    if active > registered:
        raise SecurityError("active_profile_capacity_exceeds_registered")
    if any(value[field] is not True for field in ("durable_inbox", "durable_outbox", "persistent_ack")):
        raise SecurityError("durable_runtime_capabilities_required")
    runtime_kinds = value["runtime_kinds"]
    if (
        not isinstance(runtime_kinds, list)
        or not 1 <= len(runtime_kinds) <= 8
        or len(set(runtime_kinds)) != len(runtime_kinds)
    ):
        raise SecurityError("runtime_kinds_invalid")
    runtime_kinds = [_platform_key(item, "runtime_kind") for item in runtime_kinds]
    if "windows_web" not in runtime_kinds:
        raise SecurityError("windows_web_runtime_not_offered")

    wire = _closed_object(
        value["wire_limits"],
        required={
            "max_frame_bytes",
            "max_payload_bytes",
            "max_batch_items",
            "max_unacked_deliveries",
            "max_unacked_bytes",
        },
        field="wire_limits",
    )
    wire_limits = {
        "max_frame_bytes": _positive_integer(wire["max_frame_bytes"], "max_frame_bytes", maximum=262_144),
        "max_payload_bytes": _positive_integer(wire["max_payload_bytes"], "max_payload_bytes", maximum=131_072),
        "max_batch_items": _positive_integer(wire["max_batch_items"], "max_batch_items", maximum=1),
        "max_unacked_deliveries": _positive_integer(wire["max_unacked_deliveries"], "max_unacked_deliveries", maximum=1_024),
        "max_unacked_bytes": _positive_integer(wire["max_unacked_bytes"], "max_unacked_bytes", maximum=67_108_864),
    }
    if wire_limits["max_frame_bytes"] < 16_384 or wire_limits["max_payload_bytes"] < 4_096:
        raise SecurityError("wire_limits_below_minimum")
    encoded_payload = (4 * wire_limits["max_payload_bytes"] + 2) // 3
    if wire_limits["max_frame_bytes"] < encoded_payload + 8_192:
        raise SecurityError("frame_budget_cannot_hold_payload")
    if wire_limits["max_unacked_bytes"] < wire_limits["max_payload_bytes"]:
        raise SecurityError("unacked_budget_cannot_hold_payload")

    queue = _closed_object(
        value["queue_limits"],
        required={
            "high_watermark_records",
            "hard_limit_records",
            "high_watermark_bytes",
            "hard_limit_bytes",
        },
        field="queue_limits",
    )
    queue_limits = {
        "high_watermark_records": _positive_integer(queue["high_watermark_records"], "high_watermark_records", maximum=999_999),
        "hard_limit_records": _positive_integer(queue["hard_limit_records"], "hard_limit_records", maximum=1_000_000),
        "high_watermark_bytes": _positive_integer(queue["high_watermark_bytes"], "high_watermark_bytes", maximum=10_737_287_168),
        "hard_limit_bytes": _positive_integer(queue["hard_limit_bytes"], "hard_limit_bytes", maximum=10_737_418_240),
    }
    if queue_limits["hard_limit_records"] < 2 or queue_limits["high_watermark_records"] >= queue_limits["hard_limit_records"]:
        raise SecurityError("record_queue_limits_invalid")
    if queue_limits["high_watermark_bytes"] < 131_072 or queue_limits["hard_limit_bytes"] < 262_144 or queue_limits["high_watermark_bytes"] >= queue_limits["hard_limit_bytes"]:
        raise SecurityError("byte_queue_limits_invalid")

    return {
        "max_registered_profiles": registered,
        "max_active_profiles": active,
        "durable_inbox": True,
        "durable_outbox": True,
        "persistent_ack": True,
        "runtime_kinds": runtime_kinds,
        "wire_limits": wire_limits,
        "queue_limits": queue_limits,
    }


def _parse_identity_observation(
    value: Any, *, current: int
) -> dict[str, Any]:
    observation = _closed_object(
        value,
        required={"contract", "envelope", "evidence", "admission"},
        field="identity_observation",
    )
    contract = _closed_object(
        observation["contract"],
        required={"name", "version"},
        field="identity_observation_contract",
    )
    if contract != {"name": "oin.operations", "version": 1}:
        raise SecurityError("identity_observation_contract_invalid")

    envelope = _closed_object(
        observation["envelope"],
        required={
            "event_id",
            "idempotency_key",
            "event_type",
            "occurred_at",
            "produced_at",
            "producer",
        },
        optional={"correlation_id", "causation_id"},
        field="identity_observation_envelope",
    )
    producer = _closed_object(
        envelope["producer"],
        required={"actor_kind", "actor_id"},
        field="identity_observation_producer",
    )
    if producer["actor_kind"] != "connector":
        raise SecurityError("identity_observation_producer_invalid")
    normalized_envelope: dict[str, Any] = {
        "event_id": _identifier(envelope["event_id"], "event_id"),
        "idempotency_key": _idempotency_key(envelope["idempotency_key"]),
        "event_type": envelope["event_type"],
        "occurred_at": envelope["occurred_at"],
        "produced_at": envelope["produced_at"],
        "producer": {
            "actor_kind": "connector",
            "actor_id": _identifier(producer["actor_id"], "producer_actor_id"),
        },
    }
    if envelope["event_type"] != "operations.identity_observation":
        raise SecurityError("identity_observation_event_type_invalid")
    for optional_id in ("correlation_id", "causation_id"):
        if optional_id in envelope:
            candidate = envelope[optional_id]
            normalized_envelope[optional_id] = (
                None if candidate is None else _identifier(candidate, optional_id)
            )

    evidence = _closed_object(
        observation["evidence"],
        required={
            "evidence_id",
            "tenant_id",
            "merchant_id",
            "shop_id",
            "principal_id",
            "connector_id",
            "profile_id",
            "profile_generation",
            "binding_id",
            "binding_generation",
            "platform",
            "adapter_id",
            "adapter_version",
            "provider_shop_identity",
            "provider_principal_identity",
            "observed_at",
            "expires_at",
            "evidence_sha256",
        },
        field="identity_evidence",
    )
    provider_shop = _provider_identity(
        evidence["provider_shop_identity"], kind="shop"
    )
    provider_principal = _provider_identity(
        evidence["provider_principal_identity"], kind="principal"
    )
    if (
        provider_shop["identity_quality"] != "strong"
        or provider_shop["provider_platform_shop_id"] is None
        or provider_shop["provider_platform_shop_ref"] is not None
        or provider_principal["identity_quality"] != "strong"
        or provider_principal["provider_platform_principal_id"] is None
        or provider_principal["provider_platform_principal_ref"] is not None
    ):
        raise SecurityError("identity_evidence_requires_strong_ids")

    observed_at = _exact_utc_seconds(evidence["observed_at"], "observed_at")
    expires_at = _exact_utc_seconds(evidence["expires_at"], "expires_at")
    occurred_at = _exact_utc_seconds(envelope["occurred_at"], "occurred_at")
    produced_at = _exact_utc_seconds(envelope["produced_at"], "produced_at")
    if occurred_at != observed_at or not observed_at <= produced_at <= expires_at:
        raise SecurityError("identity_evidence_time_order_invalid")
    if (
        expires_at <= current
        or observed_at > current + PDD_IDENTITY_CLOCK_SKEW_SECONDS
        or produced_at > current + PDD_IDENTITY_CLOCK_SKEW_SECONDS
        or expires_at - observed_at > PDD_IDENTITY_EVIDENCE_MAX_TTL_SECONDS
        or expires_at <= observed_at
    ):
        raise SecurityError("identity_evidence_expiry_invalid")

    public_platform = evidence["platform"]
    public_adapter = evidence["adapter_id"]
    storage_platform = PDD_ROUTE.platform_to_storage(public_platform)
    storage_adapter = PDD_ROUTE.adapter_to_storage(public_adapter)
    normalized_evidence = {
        "evidence_id": _identifier(evidence["evidence_id"], "evidence_id"),
        "tenant_id": _identifier(evidence["tenant_id"], "tenant_id"),
        "merchant_id": _identifier(evidence["merchant_id"], "merchant_id"),
        "shop_id": _identifier(evidence["shop_id"], "shop_id"),
        "principal_id": _identifier(evidence["principal_id"], "principal_id"),
        "connector_id": _identifier(evidence["connector_id"], "connector_id"),
        "profile_id": _identifier(evidence["profile_id"], "profile_id"),
        "profile_generation": _positive_integer(
            evidence["profile_generation"], "profile_generation"
        ),
        "binding_id": _identifier(evidence["binding_id"], "binding_id"),
        "binding_generation": _positive_integer(
            evidence["binding_generation"], "binding_generation"
        ),
        "platform": PDD_ROUTE.platform_to_public(storage_platform),
        "adapter_id": PDD_ROUTE.adapter_to_public(storage_adapter),
        "adapter_version": _version(evidence["adapter_version"], "adapter_version"),
        "provider_shop_identity": provider_shop,
        "provider_principal_identity": provider_principal,
        "observed_at": evidence["observed_at"],
        "expires_at": evidence["expires_at"],
        "evidence_sha256": evidence["evidence_sha256"],
    }
    if (
        not isinstance(evidence["evidence_sha256"], str)
        or not HEX_SHA256_PATTERN.fullmatch(evidence["evidence_sha256"])
    ):
        raise SecurityError("evidence_sha256_invalid")

    admission = _closed_object(
        observation["admission"],
        required={
            "runtime_kind",
            "runtime_session_id",
            "authority_fence_sha256",
            "page_capability",
            "source_kind",
            "source_version",
            "mall_name",
        },
        field="identity_observation_admission",
    )
    if admission["page_capability"] != "chat":
        raise SecurityError("identity_page_capability_invalid")
    if (
        admission["source_kind"] != PDD_IDENTITY_SOURCE_KIND
        or admission["source_version"] != PDD_IDENTITY_SOURCE_VERSION
    ):
        raise SecurityError("identity_source_unsupported")
    authority_fence = admission["authority_fence_sha256"]
    if not isinstance(authority_fence, str) or not HEX_SHA256_PATTERN.fullmatch(
        authority_fence
    ):
        raise SecurityError("authority_fence_sha256_invalid")
    normalized_admission = {
        "runtime_kind": PDD_ROUTE.require_runtime(admission["runtime_kind"]),
        "runtime_session_id": _identifier(
            admission["runtime_session_id"], "runtime_session_id"
        ),
        "authority_fence_sha256": authority_fence,
        "page_capability": "chat",
        "source_kind": PDD_IDENTITY_SOURCE_KIND,
        "source_version": PDD_IDENTITY_SOURCE_VERSION,
        "mall_name": _bounded_text(
            admission["mall_name"], field="mall_name", maximum=128, nullable=True
        ),
    }
    normalized = {
        "contract": {"name": "oin.operations", "version": 1},
        "envelope": normalized_envelope,
        "evidence": normalized_evidence,
        "admission": normalized_admission,
    }
    computed_evidence_sha256 = pdd_identity_evidence_sha256(normalized)
    if not secrets.compare_digest(
        computed_evidence_sha256, normalized_evidence["evidence_sha256"]
    ):
        raise SecurityError("identity_evidence_digest_mismatch")
    return {
        "observation": normalized,
        "storage_platform": storage_platform,
        "storage_adapter": storage_adapter,
        "observed_at": observed_at,
        "expires_at": expires_at,
        "produced_at": produced_at,
        "observation_sha256": canonical_jcs_sha256(normalized),
    }


def _parse_identity_ingress_frame(
    value: Any, *, current: int
) -> dict[str, Any]:
    frame = _closed_object(
        value,
        required={
            "contract",
            "connector_installation_id",
            "issued_at",
            "nonce",
            "observation",
            "proof",
        },
        field="identity_ingress",
    )
    contract = _closed_object(
        frame["contract"],
        required={"name", "version"},
        field="identity_ingress_contract",
    )
    if contract != {"name": PDD_IDENTITY_INGRESS_CONTRACT, "version": 1}:
        raise SecurityError("identity_ingress_contract_invalid")
    installation_id = _identifier(
        frame["connector_installation_id"], "connector_installation_id"
    )
    issued_at = _positive_integer(frame["issued_at"], "identity_ingress_issued_at")
    if (
        issued_at > current + PDD_IDENTITY_INGRESS_FUTURE_SKEW_SECONDS
        or issued_at < current - PDD_IDENTITY_INGRESS_MAX_AGE_SECONDS
    ):
        raise OperationsAuthentication("identity_ingress_request_stale")
    try:
        nonce = b64url_decode(
            frame["nonce"], max_bytes=PDD_IDENTITY_INGRESS_NONCE_BYTES
        )
    except SecurityError as exc:
        raise OperationsAuthentication("identity_ingress_proof_invalid") from exc
    if len(nonce) != PDD_IDENTITY_INGRESS_NONCE_BYTES:
        raise OperationsAuthentication("identity_ingress_proof_invalid")

    proof = _closed_object(
        frame["proof"],
        required={"algorithm", "key_id", "signed_payload_sha256", "signature"},
        field="identity_ingress_proof",
    )
    if proof["algorithm"] != "ECDSA_P256_SHA256":
        raise OperationsAuthentication("identity_ingress_proof_invalid")
    key_id = _bounded_text(proof["key_id"], field="key_id", maximum=96)
    signed_payload_sha256 = proof["signed_payload_sha256"]
    if (
        not isinstance(signed_payload_sha256, str)
        or not HEX_SHA256_PATTERN.fullmatch(signed_payload_sha256)
    ):
        raise OperationsAuthentication("identity_ingress_proof_invalid")
    try:
        signature = b64url_decode(proof["signature"], max_bytes=64)
    except SecurityError as exc:
        raise OperationsAuthentication("identity_ingress_proof_invalid") from exc
    if len(signature) != 64:
        raise OperationsAuthentication("identity_ingress_proof_invalid")

    signed_payload = pdd_identity_ingress_signing_bytes(frame)
    if not secrets.compare_digest(
        hashlib.sha256(signed_payload).hexdigest(), signed_payload_sha256
    ):
        raise OperationsAuthentication("identity_ingress_proof_invalid")
    if not isinstance(frame["observation"], dict):
        raise SecurityError("identity_ingress_observation_must_be_object")
    return {
        "connector_installation_id": installation_id,
        "key_id": key_id,
        "signed_payload": signed_payload,
        "signature": proof["signature"],
        "observation": frame["observation"],
    }


def _parse_identity_confirmation_command(
    value: Any, *, account_id: str, user_id: str
) -> dict[str, Any]:
    command = _closed_object(
        value,
        required={
            "contract",
            "envelope",
            "confirmation_id",
            "account_id",
            "user_id",
            "tenant_id",
            "membership_id",
            "membership_revision",
            "evidence_id",
            "evidence_sha256",
            "expires_at",
        },
        field="identity_confirmation",
    )
    contract = _closed_object(
        command["contract"],
        required={"name", "version"},
        field="identity_confirmation_contract",
    )
    if contract != {"name": "oin.operations", "version": 1}:
        raise SecurityError("identity_confirmation_contract_invalid")

    normalized_account_id = _identifier(command["account_id"], "account_id")
    normalized_user_id = _identifier(command["user_id"], "user_id")
    if normalized_account_id != account_id or normalized_user_id != user_id:
        raise OperationsForbidden("identity_confirmation_actor_mismatch")

    envelope = _closed_object(
        command["envelope"],
        required={
            "command_id",
            "idempotency_key",
            "command_type",
            "issued_at",
            "producer",
        },
        optional={"correlation_id", "causation_id"},
        field="identity_confirmation_envelope",
    )
    if envelope["command_type"] != "operations.identity.confirm":
        raise SecurityError("identity_confirmation_command_type_invalid")
    producer = _closed_object(
        envelope["producer"],
        required={"actor_kind", "actor_id", "account_id", "user_id"},
        field="identity_confirmation_producer",
    )
    if producer != {
        "actor_kind": "user",
        "actor_id": user_id,
        "account_id": account_id,
        "user_id": user_id,
    }:
        raise OperationsForbidden("identity_confirmation_actor_mismatch")

    normalized_envelope: dict[str, Any] = {
        "command_id": _identifier(envelope["command_id"], "command_id"),
        "idempotency_key": _idempotency_key(envelope["idempotency_key"]),
        "command_type": "operations.identity.confirm",
        "issued_at": envelope["issued_at"],
        "producer": producer,
    }
    issued_at = _exact_utc_seconds(envelope["issued_at"], "issued_at")
    for optional_id in ("correlation_id", "causation_id"):
        if optional_id in envelope:
            candidate = envelope[optional_id]
            normalized_envelope[optional_id] = (
                None if candidate is None else _identifier(candidate, optional_id)
            )

    evidence_sha256 = command["evidence_sha256"]
    if (
        not isinstance(evidence_sha256, str)
        or not HEX_SHA256_PATTERN.fullmatch(evidence_sha256)
    ):
        raise SecurityError("evidence_sha256_invalid")
    expires_at = _exact_utc_seconds(command["expires_at"], "expires_at")
    normalized = {
        "contract": {"name": "oin.operations", "version": 1},
        "envelope": normalized_envelope,
        "confirmation_id": _identifier(
            command["confirmation_id"], "confirmation_id"
        ),
        "account_id": account_id,
        "user_id": user_id,
        "tenant_id": _identifier(command["tenant_id"], "tenant_id"),
        "membership_id": _identifier(command["membership_id"], "membership_id"),
        "membership_revision": _positive_integer(
            command["membership_revision"], "membership_revision"
        ),
        "evidence_id": _identifier(command["evidence_id"], "evidence_id"),
        "evidence_sha256": evidence_sha256,
        "expires_at": command["expires_at"],
    }
    return {
        "command": normalized,
        "command_sha256": canonical_jcs_sha256(normalized),
        "issued_at": issued_at,
        "expires_at": expires_at,
    }


class B4IdentityStore:
    """B4-min identity authority, intentionally separate from Android v1 devices."""

    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings

    @staticmethod
    def _audit(
        connection: Any,
        account_id: str | None,
        action: str,
        success: bool,
        detail: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_logs(account_id, device_id, action, success, created_at, detail_json)
            VALUES (?, NULL, ?, ?, ?, ?)
            """,
            (account_id, action, 1 if success else 0, now_seconds(), canonical_json(detail)),
        )

    @staticmethod
    def _receipt(
        connection: Any,
        tenant_id: str,
        idempotency_key: str,
        account_id: str,
        command_sha256: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            """
            SELECT * FROM operation_idempotency
            WHERE tenant_id = ? AND idempotency_key = ?
            """,
            (tenant_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row["actor_account_id"] != account_id:
            raise OperationsNotFound("route_not_found")
        if row["command_sha256"] != command_sha256:
            raise OperationsConflict("idempotency_key_reuse")
        return json.loads(row["result_json"])

    @staticmethod
    def _save_receipt(
        connection: Any,
        *,
        tenant_id: str,
        idempotency_key: str,
        operation_kind: str,
        account_id: str,
        command_sha256: str,
        result: dict[str, Any],
        current: int,
    ) -> None:
        connection.execute(
            """
            INSERT INTO operation_idempotency(
                tenant_id, idempotency_key, operation_kind, actor_account_id,
                command_sha256, result_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tenant_id,
                idempotency_key,
                operation_kind,
                account_id,
                command_sha256,
                canonical_json(result),
                current,
            ),
        )

    @staticmethod
    def _require_actor(connection: Any, account_id: str, user_id: str) -> None:
        row = connection.execute(
            """
            SELECT 1 FROM accounts a
            JOIN account_users u ON u.account_id = a.id
            WHERE a.id = ? AND a.status = 'active' AND u.id = ? AND u.state = 'ACTIVE'
            """,
            (account_id, user_id),
        ).fetchone()
        if row is None:
            raise OperationsNotFound("actor_not_found")

    @staticmethod
    def _require_tenant_member(
        connection: Any, account_id: str, user_id: str, tenant_id: str
    ) -> None:
        row = connection.execute(
            """
            SELECT 1 FROM memberships m
            JOIN tenants t ON t.id = m.tenant_id AND t.state = 'ACTIVE'
            WHERE m.tenant_id = ? AND m.merchant_id IS NULL
              AND m.account_id = ? AND m.user_id = ?
              AND m.scope_kind = 'tenant' AND m.state = 'ACTIVE'
            """,
            (tenant_id, account_id, user_id),
        ).fetchone()
        if row is None:
            raise OperationsNotFound("tenant_route_not_found")

    @staticmethod
    def _require_merchant_member(
        connection: Any,
        account_id: str,
        user_id: str,
        tenant_id: str,
        merchant_id: str,
    ) -> None:
        row = connection.execute(
            """
            SELECT 1 FROM memberships m
            JOIN tenants t ON t.id = m.tenant_id AND t.state = 'ACTIVE'
            JOIN merchants merchant
              ON merchant.tenant_id = m.tenant_id AND merchant.id = m.merchant_id
             AND merchant.state = 'ACTIVE'
            WHERE m.tenant_id = ? AND m.merchant_id = ?
              AND m.account_id = ? AND m.user_id = ?
              AND m.scope_kind = 'merchant' AND m.state = 'ACTIVE'
            """,
            (tenant_id, merchant_id, account_id, user_id),
        ).fetchone()
        if row is None:
            raise OperationsNotFound("merchant_route_not_found")

    @staticmethod
    def _tenant(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "entity_type": "tenant",
            "tenant_id": row["id"],
            "state": row["state"],
            "meta": _entity_meta(row),
        }

    @staticmethod
    def _membership(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "entity_type": "membership",
            "membership_id": row["id"],
            "membership_scope_kind": row["scope_kind"],
            "account_id": row["account_id"],
            "tenant_id": row["tenant_id"],
            "user_id": row["user_id"],
            "merchant_id": row["merchant_id"],
            "role": row["role"],
            "state": row["state"],
            "meta": _entity_meta(row),
        }

    @staticmethod
    def _merchant(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "entity_type": "merchant",
            "tenant_id": row["tenant_id"],
            "merchant_id": row["id"],
            "display_name": row["display_name"],
            "state": row["state"],
            "meta": _entity_meta(row),
        }

    @staticmethod
    def _shop(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "entity_type": "shop",
            "tenant_id": row["tenant_id"],
            "merchant_id": row["merchant_id"],
            "shop_id": row["id"],
            "platform": PDD_ROUTE.platform_to_public(row["platform"]),
            "provider_identity": {
                "provider_platform_shop_id": row["provider_shop_id"],
                "provider_platform_shop_ref": row["provider_shop_ref"],
                "identity_quality": row["identity_quality"],
            },
            "display_name": row["display_name"],
            "identity_state": row["identity_state"],
            "state": row["state"],
            "meta": _entity_meta(row),
        }

    @staticmethod
    def _principal(row: Mapping[str, Any], shop_ids: list[str]) -> dict[str, Any]:
        return {
            "entity_type": "platform_principal",
            "tenant_id": row["tenant_id"],
            "merchant_id": row["merchant_id"],
            "platform": PDD_ROUTE.platform_to_public(row["platform"]),
            "principal_id": row["id"],
            "provider_identity": {
                "provider_platform_principal_id": row["provider_principal_id"],
                "provider_platform_principal_ref": row["provider_principal_ref"],
                "identity_quality": row["identity_quality"],
            },
            "display_name": row["display_name"],
            "role_note": row["role_note"],
            "shop_ids": shop_ids,
            "identity_state": row["identity_state"],
            "state": row["state"],
            "meta": _entity_meta(row),
        }

    @staticmethod
    def _enrollment(row: Mapping[str, Any], *, immutable_authorized: bool = False) -> dict[str, Any]:
        state = "AUTHORIZED" if immutable_authorized else row["state"]
        return {
            "entity_type": "enrollment_authorization",
            "authorization_id": row["id"],
            "account_id": row["account_id"],
            "user_id": row["user_id"],
            "tenant_id": row["tenant_id"],
            "merchant_id": row["merchant_id"],
            "connector_installation_id": row["connector_installation_id"],
            "key_id": row["key_id"],
            "public_key_spki_sha256": row["public_key_spki_sha256"],
            "purpose": row["purpose"],
            "state": state,
            "issued_at": _utc_text(int(row["issued_at"])),
            "expires_at": _utc_text(int(row["expires_at"])),
            "consumed_at": None if immutable_authorized else (
                _utc_text(int(row["consumed_at"])) if row["consumed_at"] is not None else None
            ),
            "connector_id": None if immutable_authorized else row["connector_id"],
            "registration_epoch": None if immutable_authorized else row["registration_epoch"],
            "meta": _entity_meta(row),
        }

    @staticmethod
    def _connector(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "entity_type": "connector",
            "tenant_id": row["tenant_id"],
            "connector_id": row["id"],
            "connector_installation_id": row["connector_installation_id"],
            "enrollment_authorization_id": row["enrollment_authorization_id"],
            "registered_account_id": row["registered_account_id"],
            "registered_by_user_id": row["registered_by_user_id"],
            "registered_key_id": row["registered_key_id"],
            "registered_public_key_spki_sha256": row["registered_public_key_spki_sha256"],
            "device_generation": int(row["device_generation"]),
            "runtime_kind": row["runtime_kind"],
            "capabilities": json.loads(row["capabilities_json"]),
            "identity_state": row["identity_state"],
            "state": row["state"],
            "meta": _entity_meta(row),
        }

    @staticmethod
    def _profile(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "entity_type": "profile",
            "tenant_id": row["tenant_id"],
            "connector_id": row["connector_id"],
            "profile_id": row["id"],
            "profile_generation": int(row["profile_generation"]),
            "runtime_kind": row["runtime_kind"],
            "state": row["state"],
            "meta": _entity_meta(row),
        }

    @staticmethod
    def _binding(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "entity_type": "binding",
            "binding": {
                "tenant_id": row["tenant_id"],
                "merchant_id": row["merchant_id"],
                "shop_id": row["shop_id"],
                "platform": PDD_ROUTE.platform_to_public(row["platform"]),
                "principal_id": row["principal_id"],
                "connector_id": row["connector_id"],
                "profile_id": row["profile_id"],
                "binding_id": row["id"],
                "binding_generation": int(row["binding_generation"]),
                "runtime_kind": PDD_ROUTE.require_runtime(row["runtime_kind"]),
                "adapter_id": PDD_ROUTE.adapter_to_public(row["adapter_id"]),
                "adapter_version": row["adapter_version"],
            },
            "identity_state": row["identity_state"],
            "verified_identity_revision": row["verified_identity_revision"],
            "capabilities": json.loads(row["capabilities_json"]),
            "state": row["state"],
            "bound_at": _utc_text(int(row["bound_at"])),
            "unbound_at": _utc_text(int(row["unbound_at"])) if row["unbound_at"] is not None else None,
            "meta": _entity_meta(row),
        }

    @staticmethod
    def _identity_observation_view(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "event": {
                "event_id": row["event_id"],
                "idempotency_key": row["idempotency_key"],
            },
            "evidence": {
                "evidence_id": row["evidence_id"],
                "tenant_id": row["tenant_id"],
                "merchant_id": row["merchant_id"],
                "shop_id": row["shop_id"],
                "principal_id": row["principal_id"],
                "connector_id": row["connector_id"],
                "profile_id": row["profile_id"],
                "profile_generation": int(row["profile_generation"]),
                "binding_id": row["binding_id"],
                "binding_generation": int(row["binding_generation"]),
                "platform": PDD_ROUTE.platform_to_public(row["platform"]),
                "adapter_id": PDD_ROUTE.adapter_to_public(row["adapter_id"]),
                "adapter_version": row["adapter_version"],
                "provider_shop_identity": {
                    "provider_platform_shop_id": row["provider_shop_id"],
                    "provider_platform_shop_ref": None,
                    "identity_quality": "strong",
                },
                "provider_principal_identity": {
                    "provider_platform_principal_id": row[
                        "provider_principal_id"
                    ],
                    "provider_platform_principal_ref": None,
                    "identity_quality": "strong",
                },
                "observed_at": _utc_text(int(row["observed_at"])),
                "expires_at": _utc_text(int(row["expires_at"])),
                "evidence_sha256": row["evidence_sha256"],
            },
            "admission": {
                "runtime_kind": PDD_ROUTE.require_runtime(row["runtime_kind"]),
                "runtime_session_id": row["runtime_session_id"],
                "authority_fence_sha256": row["authority_fence_sha256"],
                "page_capability": row["page_capability"],
                "source_kind": row["source_kind"],
                "source_version": row["source_version"],
                "mall_name": row["mall_name"],
            },
            "status": row["observation_state"],
            "captured_revisions": {
                "shop_revision": int(row["shop_revision"]),
                "principal_revision": int(row["principal_revision"]),
                "binding_revision": int(row["binding_revision"]),
            },
            "stored_at": _utc_text(int(row["created_at"])),
        }

    @staticmethod
    def _pending_identity_summary(row: Mapping[str, Any]) -> dict[str, Any]:
        """Return only the bounded fields needed for an explicit confirmation card."""

        return {
            "evidence_id": row["evidence_id"],
            "evidence_sha256": row["evidence_sha256"],
            "status": "PENDING",
            "tenant_id": row["tenant_id"],
            "merchant_id": row["merchant_id"],
            "route": {
                "shop_id": row["shop_id"],
                "principal_id": row["principal_id"],
                "connector_id": row["connector_id"],
                "profile_id": row["profile_id"],
                "profile_generation": int(row["profile_generation"]),
                "binding_id": row["binding_id"],
                "binding_generation": int(row["binding_generation"]),
                "platform": PDD_ROUTE.platform_to_public(row["platform"]),
                "adapter_id": PDD_ROUTE.adapter_to_public(row["adapter_id"]),
                "adapter_version": row["adapter_version"],
                "runtime_kind": PDD_ROUTE.require_runtime(row["runtime_kind"]),
            },
            "identity": {
                "provider_platform_shop_id": row["provider_shop_id"],
                "provider_platform_principal_id": row["provider_principal_id"],
                "mall_name": row["mall_name"],
                "identity_quality": "strong",
            },
            "observed_at": _utc_text(int(row["observed_at"])),
            "expires_at": _utc_text(int(row["expires_at"])),
            "captured_revisions": {
                "shop_revision": int(row["shop_revision"]),
                "principal_revision": int(row["principal_revision"]),
                "binding_revision": int(row["binding_revision"]),
            },
        }

    @staticmethod
    def _identity_confirmation_result(
        parsed: Mapping[str, Any],
        *,
        result: str,
        confirmed_at: int,
        error: dict[str, Any] | None,
    ) -> dict[str, Any]:
        command = parsed["command"]
        envelope = command["envelope"]
        return {
            "contract": {"name": "oin.operations", "version": 1},
            "envelope": {
                "event_id": command["confirmation_id"],
                "idempotency_key": envelope["idempotency_key"],
                "event_type": "operations.identity_confirmation",
                "occurred_at": _utc_text(confirmed_at),
                "produced_at": _utc_text(confirmed_at),
                "producer": {
                    "actor_kind": "system",
                    "actor_id": "oin-control-plane",
                },
                "correlation_id": envelope.get("correlation_id"),
                "causation_id": envelope.get("causation_id"),
            },
            "command_id": envelope["command_id"],
            "command_idempotency_key": envelope["idempotency_key"],
            "command_sha256": parsed["command_sha256"],
            "result": result,
            "confirmation_id": command["confirmation_id"],
            "account_id": command["account_id"],
            "user_id": command["user_id"],
            "tenant_id": command["tenant_id"],
            "membership_id": command["membership_id"],
            "membership_revision": command["membership_revision"],
            "evidence_id": command["evidence_id"],
            "evidence_sha256": command["evidence_sha256"],
            "confirmed_at": _utc_text(confirmed_at),
            "expires_at": command["expires_at"],
            "error": error,
        }

    @staticmethod
    def _confirmation_error(code: str, detail_code: str) -> dict[str, Any]:
        return {
            "code": code,
            "retryable": False,
            "detail_code": detail_code,
        }

    @staticmethod
    def _expected_platform_identity(row: Mapping[str, Any]) -> dict[str, Any]:
        try:
            merchant_id = _identifier(row["merchant_id"], "merchant_id")
            shop_id = _identifier(row["shop_id"], "shop_id")
            principal_id = _identifier(row["principal_id"], "principal_id")
            storage_platform = _platform_key(row["platform"], "platform")
            platform = PDD_ROUTE.platform_to_public(storage_platform)
            provider_shop_id = _provider_identifier(
                row["expected_provider_shop_id"], "provider_platform_shop_id"
            )
            provider_principal_id = _provider_identifier(
                row["expected_provider_principal_id"],
                "provider_platform_principal_id",
            )
            shop_revision = _positive_integer(row["shop_revision"], "shop_revision")
            principal_revision = _positive_integer(
                row["principal_revision"], "principal_revision"
            )
            binding_revision = _positive_integer(
                row["binding_revision"], "binding_revision"
            )
        except (KeyError, SecurityError) as exc:
            raise OperationsConflict("route_activation_identity_invalid") from exc

        lifecycle_state = row["binding_lifecycle_state"]
        identity_state = row["binding_identity_state"]
        verified_revision = row["expected_verified_identity_revision"]
        if storage_platform != PDD_ROUTE.storage_platform or lifecycle_state not in {"PENDING", "ACTIVE"}:
            raise OperationsConflict("route_activation_identity_invalid")
        if identity_state == "PENDING":
            if verified_revision is not None:
                raise OperationsConflict("route_activation_identity_invalid")
        elif identity_state == "VERIFIED":
            if provider_shop_id is None or provider_principal_id is None:
                raise OperationsConflict("route_activation_identity_invalid")
            try:
                verified_revision = _positive_integer(
                    verified_revision, "verified_identity_revision"
                )
            except SecurityError as exc:
                raise OperationsConflict("route_activation_identity_invalid") from exc
        else:
            raise OperationsConflict("route_activation_identity_invalid")

        return {
            "merchant_id": merchant_id,
            "shop_id": shop_id,
            "platform": platform,
            "principal_id": principal_id,
            "provider_platform_shop_id": provider_shop_id,
            "provider_platform_principal_id": provider_principal_id,
            "shop_revision": shop_revision,
            "principal_revision": principal_revision,
            "binding_revision": binding_revision,
            "binding_lifecycle_state": lifecycle_state,
            "identity_state": identity_state,
            "verified_identity_revision": verified_revision,
        }

    def authorized_context(self, account_id: str, user_id: str) -> dict[str, Any]:
        """Project only the caller's currently active tenant/merchant memberships."""

        with self.database.connect() as connection:
            self._require_actor(connection, account_id, user_id)
            memberships = connection.execute(
                """
                SELECT m.*
                FROM memberships m
                JOIN tenants t ON t.id = m.tenant_id AND t.state = 'ACTIVE'
                LEFT JOIN merchants merchant
                  ON merchant.tenant_id = m.tenant_id AND merchant.id = m.merchant_id
                WHERE m.account_id = ? AND m.user_id = ? AND m.state = 'ACTIVE'
                  AND (
                    (m.scope_kind = 'tenant' AND m.merchant_id IS NULL) OR
                    (m.scope_kind = 'merchant' AND merchant.state = 'ACTIVE')
                  )
                ORDER BY m.tenant_id, m.scope_kind, m.merchant_id, m.id
                """,
                (account_id, user_id),
            ).fetchall()
            tenant_memberships = [
                row for row in memberships if row["scope_kind"] == "tenant"
            ]
            merchant_memberships = [
                row for row in memberships if row["scope_kind"] == "merchant"
            ]
            tenant_ids = sorted({row["tenant_id"] for row in memberships})
            merchant_routes = sorted(
                {
                    (row["tenant_id"], row["merchant_id"])
                    for row in merchant_memberships
                }
            )

            tenants: list[Mapping[str, Any]] = []
            if tenant_ids:
                placeholders = ",".join("?" for _ in tenant_ids)
                tenants = connection.execute(
                    f"SELECT * FROM tenants WHERE state = 'ACTIVE' AND id IN ({placeholders}) ORDER BY id",
                    tuple(tenant_ids),
                ).fetchall()
            merchants: list[Mapping[str, Any]] = []
            if merchant_routes:
                clauses = " OR ".join(
                    "(tenant_id = ? AND id = ?)" for _ in merchant_routes
                )
                parameters = tuple(
                    item for route in merchant_routes for item in route
                )
                merchants = connection.execute(
                    f"SELECT * FROM merchants WHERE state = 'ACTIVE' AND ({clauses}) ORDER BY tenant_id, id",
                    parameters,
                ).fetchall()
            return {
                "account_id": account_id,
                "user_id": user_id,
                "tenant_memberships": [
                    self._membership(row) for row in tenant_memberships
                ],
                "merchant_memberships": [
                    self._membership(row) for row in merchant_memberships
                ],
                "tenants": [self._tenant(row) for row in tenants],
                "merchants": [self._merchant(row) for row in merchants],
            }

    def list_pending_identity_evidence(
        self, account_id: str, user_id: str
    ) -> dict[str, Any]:
        """List actionable evidence visible through the actor's active merchant memberships."""

        current = now_seconds()
        with self.database.connect() as connection:
            self._require_actor(connection, account_id, user_id)
            rows = connection.execute(
                """
                SELECT e.*
                FROM identity_evidence e
                JOIN memberships membership
                  ON membership.tenant_id = e.tenant_id
                 AND membership.merchant_id = e.merchant_id
                 AND membership.account_id = ?
                 AND membership.user_id = ?
                 AND membership.scope_kind = 'merchant'
                 AND membership.state = 'ACTIVE'
                JOIN tenants tenant
                  ON tenant.id = e.tenant_id AND tenant.state = 'ACTIVE'
                JOIN merchants merchant
                  ON merchant.tenant_id = e.tenant_id
                 AND merchant.id = e.merchant_id
                 AND merchant.state = 'ACTIVE'
                WHERE e.observation_state = 'PENDING'
                  AND e.expires_at > ?
                  AND NOT EXISTS (
                    SELECT 1 FROM identity_confirmations confirmation
                    WHERE confirmation.tenant_id = e.tenant_id
                      AND confirmation.evidence_id = e.evidence_id
                  )
                ORDER BY e.created_at DESC, e.evidence_id DESC
                LIMIT ?
                """,
                (account_id, user_id, current, PDD_PENDING_EVIDENCE_LIMIT + 1),
            ).fetchall()
        truncated = len(rows) > PDD_PENDING_EVIDENCE_LIMIT
        visible = rows[:PDD_PENDING_EVIDENCE_LIMIT]
        return {
            "items": [self._pending_identity_summary(row) for row in visible],
            "limit": PDD_PENDING_EVIDENCE_LIMIT,
            "truncated": truncated,
        }

    def bootstrap_workspace(
        self, account_id: str, user_id: str, command: dict[str, Any]
    ) -> OperationOutcome:
        command = _closed_object(
            command,
            required={
                "idempotency_key",
                "tenant_id",
                "tenant_owner_membership_id",
                "merchant_id",
                "merchant_display_name",
                "merchant_owner_membership_id",
            },
            field="workspace_bootstrap",
        )
        tenant_id = _identifier(command["tenant_id"], "tenant_id")
        tenant_membership_id = _identifier(command["tenant_owner_membership_id"], "tenant_owner_membership_id")
        merchant_id = _identifier(command["merchant_id"], "merchant_id")
        merchant_name = _bounded_text(command["merchant_display_name"], field="merchant_display_name", maximum=120)
        merchant_membership_id = _identifier(command["merchant_owner_membership_id"], "merchant_owner_membership_id")
        idem = _idempotency_key(command["idempotency_key"])
        digest = canonical_jcs_sha256({"operation": "workspace.bootstrap", "account_id": account_id, "user_id": user_id, **command})
        current = now_seconds()
        try:
            with self.database.transaction(immediate=True) as connection:
                self._require_actor(connection, account_id, user_id)
                replay = self._receipt(connection, tenant_id, idem, account_id, digest)
                if replay is not None:
                    return OperationOutcome(replay, True)
                existing_membership = connection.execute(
                    """
                    SELECT tenant_id FROM memberships
                    WHERE account_id = ? AND scope_kind = 'tenant' AND state = 'ACTIVE'
                    """,
                    (account_id,),
                ).fetchone()
                if existing_membership is not None:
                    raise OperationsConflict("account_already_bootstrapped")
                if connection.execute("SELECT 1 FROM tenants WHERE id = ?", (tenant_id,)).fetchone():
                    raise OperationsConflict("tenant_exists")
                connection.execute(
                    "INSERT INTO tenants(id, state, revision, created_at, updated_at) VALUES (?, 'ACTIVE', 1, ?, ?)",
                    (tenant_id, current, current),
                )
                connection.execute(
                    """
                    INSERT INTO memberships(
                        id, tenant_id, merchant_id, account_id, user_id, scope_kind,
                        role, state, revision, created_at, updated_at
                    ) VALUES (?, ?, NULL, ?, ?, 'tenant', 'owner', 'ACTIVE', 1, ?, ?)
                    """,
                    (tenant_membership_id, tenant_id, account_id, user_id, current, current),
                )
                connection.execute(
                    """
                    INSERT INTO merchants(id, tenant_id, display_name, state, revision, created_at, updated_at)
                    VALUES (?, ?, ?, 'ACTIVE', 1, ?, ?)
                    """,
                    (merchant_id, tenant_id, merchant_name, current, current),
                )
                connection.execute(
                    """
                    INSERT INTO memberships(
                        id, tenant_id, merchant_id, account_id, user_id, scope_kind,
                        role, state, revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'merchant', 'owner', 'ACTIVE', 1, ?, ?)
                    """,
                    (merchant_membership_id, tenant_id, merchant_id, account_id, user_id, current, current),
                )
                tenant = connection.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
                tenant_membership = connection.execute("SELECT * FROM memberships WHERE id = ?", (tenant_membership_id,)).fetchone()
                merchant = connection.execute("SELECT * FROM merchants WHERE id = ?", (merchant_id,)).fetchone()
                merchant_membership = connection.execute("SELECT * FROM memberships WHERE id = ?", (merchant_membership_id,)).fetchone()
                result = {
                    "tenant": self._tenant(tenant),
                    "tenant_owner_membership": self._membership(tenant_membership),
                    "merchant": self._merchant(merchant),
                    "merchant_owner_membership": self._membership(merchant_membership),
                }
                self._audit(connection, account_id, "workspace_bootstrapped", True, {"tenant_id": tenant_id, "merchant_id": merchant_id})
                self._save_receipt(
                    connection,
                    tenant_id=tenant_id,
                    idempotency_key=idem,
                    operation_kind="workspace.bootstrap",
                    account_id=account_id,
                    command_sha256=digest,
                    result=result,
                    current=current,
                )
                return OperationOutcome(result)
        except IntegrityError as exc:
            raise OperationsConflict("workspace_bootstrap_conflict") from exc

    def create_shop(
        self, account_id: str, user_id: str, command: dict[str, Any]
    ) -> OperationOutcome:
        command = _closed_object(
            command,
            required={
                "idempotency_key", "tenant_id", "merchant_id", "shop_id",
                "platform", "display_name", "provider_identity",
            },
            field="shop_create",
        )
        tenant_id = _identifier(command["tenant_id"], "tenant_id")
        merchant_id = _identifier(command["merchant_id"], "merchant_id")
        shop_id = _identifier(command["shop_id"], "shop_id")
        platform = PDD_ROUTE.platform_to_storage(command["platform"])
        display_name = _bounded_text(command["display_name"], field="display_name", maximum=120)
        provider = _provider_identity(command["provider_identity"], kind="shop")
        idem = _idempotency_key(command["idempotency_key"])
        normalized = {**command, "provider_identity": provider}
        digest = canonical_jcs_sha256({"operation": "shop.create", "account_id": account_id, "user_id": user_id, **normalized})
        current = now_seconds()
        try:
            with self.database.transaction(immediate=True) as connection:
                self._require_actor(connection, account_id, user_id)
                self._require_merchant_member(connection, account_id, user_id, tenant_id, merchant_id)
                replay = self._receipt(connection, tenant_id, idem, account_id, digest)
                if replay is not None:
                    return OperationOutcome(replay, True)
                connection.execute(
                    """
                    INSERT INTO platform_shops(
                        id, tenant_id, merchant_id, platform, provider_shop_id,
                        provider_shop_ref, identity_quality, display_name,
                        identity_state, state, revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 'ACTIVE', 1, ?, ?)
                    """,
                    (
                        shop_id, tenant_id, merchant_id, platform,
                        provider["provider_platform_shop_id"],
                        provider["provider_platform_shop_ref"],
                        provider["identity_quality"], display_name, current, current,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM platform_shops WHERE tenant_id = ? AND merchant_id = ? AND id = ?",
                    (tenant_id, merchant_id, shop_id),
                ).fetchone()
                result = {"shop": self._shop(row)}
                self._audit(connection, account_id, "shop_created", True, {"tenant_id": tenant_id, "merchant_id": merchant_id, "shop_id": shop_id})
                self._save_receipt(connection, tenant_id=tenant_id, idempotency_key=idem, operation_kind="shop.create", account_id=account_id, command_sha256=digest, result=result, current=current)
                return OperationOutcome(result)
        except IntegrityError as exc:
            raise OperationsConflict("shop_create_conflict") from exc

    def create_principal(
        self, account_id: str, user_id: str, command: dict[str, Any]
    ) -> OperationOutcome:
        command = _closed_object(
            command,
            required={
                "idempotency_key", "tenant_id", "merchant_id", "principal_id",
                "platform", "display_name", "role_note", "shop_ids", "provider_identity",
            },
            field="principal_create",
        )
        tenant_id = _identifier(command["tenant_id"], "tenant_id")
        merchant_id = _identifier(command["merchant_id"], "merchant_id")
        principal_id = _identifier(command["principal_id"], "principal_id")
        platform = PDD_ROUTE.platform_to_storage(command["platform"])
        display_name = _bounded_text(command["display_name"], field="display_name", maximum=120)
        role_note = _bounded_text(command["role_note"], field="role_note", maximum=80, nullable=True)
        shop_ids_raw = command["shop_ids"]
        if not isinstance(shop_ids_raw, list) or not 1 <= len(shop_ids_raw) <= 64:
            raise SecurityError("shop_ids_invalid")
        shop_ids = [_identifier(item, "shop_id") for item in shop_ids_raw]
        if len(set(shop_ids)) != len(shop_ids):
            raise SecurityError("shop_ids_duplicate")
        provider = _provider_identity(command["provider_identity"], kind="principal")
        idem = _idempotency_key(command["idempotency_key"])
        normalized = {**command, "role_note": role_note, "shop_ids": shop_ids, "provider_identity": provider}
        digest = canonical_jcs_sha256({"operation": "principal.create", "account_id": account_id, "user_id": user_id, **normalized})
        current = now_seconds()
        try:
            with self.database.transaction(immediate=True) as connection:
                self._require_actor(connection, account_id, user_id)
                self._require_merchant_member(connection, account_id, user_id, tenant_id, merchant_id)
                replay = self._receipt(connection, tenant_id, idem, account_id, digest)
                if replay is not None:
                    return OperationOutcome(replay, True)
                placeholders = ",".join("?" for _ in shop_ids)
                rows = connection.execute(
                    f"""
                    SELECT id FROM platform_shops
                    WHERE tenant_id = ? AND merchant_id = ? AND platform = ?
                      AND state = 'ACTIVE' AND id IN ({placeholders})
                    """,
                    (tenant_id, merchant_id, platform, *shop_ids),
                ).fetchall()
                if {row["id"] for row in rows} != set(shop_ids):
                    raise OperationsNotFound("shop_route_not_found")
                connection.execute(
                    """
                    INSERT INTO platform_principals(
                        id, tenant_id, merchant_id, platform, provider_principal_id,
                        provider_principal_ref, identity_quality, display_name, role_note,
                        identity_state, state, revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 'ACTIVE', 1, ?, ?)
                    """,
                    (
                        principal_id, tenant_id, merchant_id, platform,
                        provider["provider_platform_principal_id"],
                        provider["provider_platform_principal_ref"],
                        provider["identity_quality"], display_name, role_note, current, current,
                    ),
                )
                for shop_id in shop_ids:
                    connection.execute(
                        """
                        INSERT INTO platform_principal_shops(
                            tenant_id, merchant_id, platform, principal_id, shop_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (tenant_id, merchant_id, platform, principal_id, shop_id, current),
                    )
                row = connection.execute(
                    "SELECT * FROM platform_principals WHERE tenant_id = ? AND merchant_id = ? AND id = ?",
                    (tenant_id, merchant_id, principal_id),
                ).fetchone()
                result = {"principal": self._principal(row, shop_ids)}
                self._audit(connection, account_id, "platform_principal_created", True, {"tenant_id": tenant_id, "merchant_id": merchant_id, "principal_id": principal_id})
                self._save_receipt(connection, tenant_id=tenant_id, idempotency_key=idem, operation_kind="principal.create", account_id=account_id, command_sha256=digest, result=result, current=current)
                return OperationOutcome(result)
        except IntegrityError as exc:
            raise OperationsConflict("principal_create_conflict") from exc

    def authorize_enrollment(
        self, account_id: str, user_id: str, command: dict[str, Any]
    ) -> OperationOutcome:
        command = _closed_object(
            command,
            required={
                "idempotency_key", "authorization_id", "tenant_id", "merchant_id",
                "connector_installation_id", "public_key_spki",
            },
            field="enrollment_authorize",
        )
        tenant_id = _identifier(command["tenant_id"], "tenant_id")
        merchant_id = _identifier(command["merchant_id"], "merchant_id")
        authorization_id = _identifier(command["authorization_id"], "authorization_id")
        installation_id = _identifier(command["connector_installation_id"], "connector_installation_id")
        public_key = load_p256_public_key(command["public_key_spki"])
        if len(public_key.der) != 91:
            raise SecurityError("public_key_spki_invalid_length")
        key_id = p256_key_id(public_key)
        idem = _idempotency_key(command["idempotency_key"])
        digest = canonical_jcs_sha256(
            {
                "operation": "enrollment.authorize",
                "account_id": account_id,
                "user_id": user_id,
                "authorization_id": authorization_id,
                "tenant_id": tenant_id,
                "merchant_id": merchant_id,
                "connector_installation_id": installation_id,
                "key_id": key_id,
                "public_key_spki_sha256": public_key.fingerprint,
            }
        )
        current = now_seconds()
        expires_at = current + self.settings.enrollment_ttl_seconds
        try:
            with self.database.transaction(immediate=True) as connection:
                self._require_actor(connection, account_id, user_id)
                self._require_merchant_member(connection, account_id, user_id, tenant_id, merchant_id)
                replay = self._receipt(connection, tenant_id, idem, account_id, digest)
                if replay is not None:
                    return OperationOutcome(replay, True)
                connection.execute(
                    """
                    INSERT INTO connector_enrollment_authorizations(
                        id, account_id, user_id, tenant_id, merchant_id,
                        connector_installation_id, key_id, public_key_spki_sha256,
                        public_key_spki, purpose, state, issued_at, expires_at,
                        revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'connector_registration',
                              'AUTHORIZED', ?, ?, 1, ?, ?)
                    """,
                    (
                        authorization_id, account_id, user_id, tenant_id, merchant_id,
                        installation_id, key_id, public_key.fingerprint, public_key.der,
                        current, expires_at, current, current,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM connector_enrollment_authorizations WHERE id = ?",
                    (authorization_id,),
                ).fetchone()
                result = {"enrollment": self._enrollment(row, immutable_authorized=True)}
                self._audit(connection, account_id, "connector_enrollment_authorized", True, {"tenant_id": tenant_id, "merchant_id": merchant_id, "authorization_id": authorization_id, "connector_installation_id": installation_id})
                self._save_receipt(connection, tenant_id=tenant_id, idempotency_key=idem, operation_kind="enrollment.authorize", account_id=account_id, command_sha256=digest, result=result, current=current)
                return OperationOutcome(result)
        except IntegrityError as exc:
            raise OperationsConflict("enrollment_authorization_conflict") from exc

    @staticmethod
    def _enrollment_reference(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "authorization_id": row["id"],
            "authorization_revision": int(row["revision"]),
            "account_id": row["account_id"],
            "user_id": row["user_id"],
            "tenant_id": row["tenant_id"],
            "merchant_id": row["merchant_id"],
            "connector_installation_id": row["connector_installation_id"],
            "key_id": row["key_id"],
            "public_key_spki_sha256": row["public_key_spki_sha256"],
            "purpose": row["purpose"],
            "expires_at": _utc_text(int(row["expires_at"])),
        }

    @staticmethod
    def _command_envelope(
        value: Any,
        *,
        command_type: str,
        actor_id: str,
        require_fresh: bool = True,
    ) -> dict[str, Any]:
        value = _closed_object(
            value,
            required={"command_id", "idempotency_key", "command_type", "issued_at", "producer"},
            optional={"correlation_id", "causation_id"},
            field="envelope",
        )
        _identifier(value["command_id"], "command_id")
        _idempotency_key(value["idempotency_key"])
        if value["command_type"] != command_type:
            raise SecurityError("command_type_invalid")
        _utc_seconds(value["issued_at"], "issued_at")
        producer = _closed_object(value["producer"], required={"actor_kind", "actor_id"}, field="producer")
        if producer != {"actor_kind": "connector", "actor_id": actor_id}:
            raise SecurityError("connector_actor_invalid")
        for field in ("correlation_id", "causation_id"):
            if field in value and value[field] is not None:
                _identifier(value[field], field)
        if require_fresh:
            B4IdentityStore._require_command_fresh(value, now_seconds())
        return value

    @staticmethod
    def _require_command_fresh(envelope: Mapping[str, Any], current: int) -> None:
        issued = _utc_seconds(envelope["issued_at"], "issued_at")
        if issued > current + 30 or issued < current - 300:
            raise SecurityError("command_issued_at_outside_window")

    @staticmethod
    def _validate_enrollment_reference(value: Any) -> dict[str, Any]:
        value = _closed_object(
            value,
            required={
                "authorization_id", "authorization_revision", "account_id", "user_id",
                "tenant_id", "merchant_id", "connector_installation_id", "key_id",
                "public_key_spki_sha256", "purpose", "expires_at",
            },
            field="enrollment",
        )
        for field in ("authorization_id", "account_id", "user_id", "tenant_id", "connector_installation_id"):
            _identifier(value[field], field)
        if value["merchant_id"] is not None:
            _identifier(value["merchant_id"], "merchant_id")
        _positive_integer(value["authorization_revision"], "authorization_revision")
        _bounded_text(value["key_id"], field="key_id", maximum=60)
        if not isinstance(value["public_key_spki_sha256"], str) or not HEX_SHA256_PATTERN.fullmatch(value["public_key_spki_sha256"]):
            raise SecurityError("public_key_spki_sha256_invalid")
        if value["purpose"] != "connector_registration":
            raise SecurityError("enrollment_purpose_invalid")
        _utc_seconds(value["expires_at"], "enrollment_expires_at")
        return value

    def create_registration_challenge(self, frame: dict[str, Any]) -> dict[str, Any]:
        frame = _closed_object(
            frame,
            required={"contract", "envelope", "wire_direction", "connector_installation_id", "agent_version", "enrollment"},
            field="registration_challenge_request",
        )
        if frame["contract"] != {"name": "oin.runtime", "version": 2} or frame["wire_direction"] != "connector_to_control_plane":
            raise SecurityError("runtime_contract_invalid")
        installation_id = _identifier(frame["connector_installation_id"], "connector_installation_id")
        _version(frame["agent_version"], "agent_version")
        envelope = self._command_envelope(frame["envelope"], command_type="runtime.registration.challenge.request", actor_id=installation_id)
        reference = self._validate_enrollment_reference(frame["enrollment"])
        request_digest = canonical_jcs_sha256(frame)
        current = now_seconds()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM connector_enrollment_authorizations WHERE id = ?",
                (reference["authorization_id"],),
            ).fetchone()
            if row is None or row["state"] != "AUTHORIZED" or int(row["expires_at"]) <= current:
                raise OperationsAuthentication("enrollment_required_or_expired")
            if self._enrollment_reference(row) != reference or row["connector_installation_id"] != installation_id:
                raise OperationsAuthentication("enrollment_identity_mismatch")
            existing = connection.execute(
                """
                SELECT * FROM connector_registration_challenges
                WHERE authorization_id = ? AND challenge_idempotency_key = ?
                """,
                (row["id"], envelope["idempotency_key"]),
            ).fetchone()
            if existing is not None:
                if existing["challenge_request_sha256"] != request_digest:
                    raise OperationsConflict("registration_challenge_idempotency_reuse")
                return json.loads(existing["challenge_json"])
            challenge_id = str(uuid.uuid4())
            expires_at = min(current + 60, int(row["expires_at"]))
            if expires_at <= current:
                raise OperationsAuthentication("enrollment_expired")
            challenge = {
                "challenge_id": challenge_id,
                "purpose": "registration",
                "nonce": new_opaque_token(32),
                "issued_at": _utc_text(current),
                "expires_at": _utc_text(expires_at),
                "signature_algorithm": "ECDSA_P256_SHA256",
                "canonicalization": "JCS_RFC8785",
                "domain_separator": "OIN-RUNTIME-V2-REGISTRATION",
                "bound_key_id": row["key_id"],
                "bound_public_key_spki_sha256": row["public_key_spki_sha256"],
            }
            response = {
                "contract": {"name": "oin.runtime", "version": 2},
                "envelope": {
                    "event_id": str(uuid.uuid4()),
                    "idempotency_key": f"evt:{challenge_id}",
                    "event_type": "runtime.registration.challenge",
                    "occurred_at": _utc_text(current),
                    "produced_at": _utc_text(current),
                    "producer": {"actor_kind": "system", "actor_id": "oin_control_plane"},
                    "correlation_id": envelope["command_id"],
                },
                "wire_direction": "control_plane_to_connector",
                "connector_installation_id": installation_id,
                "enrollment": reference,
                "challenge": challenge,
            }
            connection.execute(
                """
                INSERT INTO connector_registration_challenges(
                    id, authorization_id, connector_installation_id, key_id,
                    public_key_spki_sha256, nonce, issued_at, expires_at, state,
                    challenge_request_id, challenge_idempotency_key,
                    challenge_request_sha256, challenge_json, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ISSUED', ?, ?, ?, ?, 1)
                """,
                (
                    challenge_id, row["id"], installation_id, row["key_id"],
                    row["public_key_spki_sha256"], challenge["nonce"], current,
                    expires_at, envelope["command_id"], envelope["idempotency_key"],
                    request_digest, canonical_json(response),
                ),
            )
            self._audit(connection, row["account_id"], "connector_registration_challenge_issued", True, {"authorization_id": row["id"], "connector_installation_id": installation_id, "challenge_id": challenge_id})
            return response

    def register_connector(self, frame: dict[str, Any]) -> dict[str, Any]:
        frame = _closed_object(
            frame,
            required={
                "contract", "envelope", "wire_direction", "connector_installation_id",
                "runtime_kind", "agent_version", "operating_system",
                "installation_public_key", "enrollment", "proof", "capabilities",
            },
            field="registration_request",
        )
        if frame["contract"] != {"name": "oin.runtime", "version": 2} or frame["wire_direction"] != "connector_to_control_plane":
            raise SecurityError("runtime_contract_invalid")
        installation_id = _identifier(frame["connector_installation_id"], "connector_installation_id")
        envelope = self._command_envelope(
            frame["envelope"],
            command_type="runtime.registration.request",
            actor_id=installation_id,
            require_fresh=False,
        )
        if frame["runtime_kind"] != "windows_web":
            raise SecurityError("runtime_kind_invalid")
        agent_version = _version(frame["agent_version"], "agent_version")
        operating_system = _closed_object(frame["operating_system"], required={"family", "version", "architecture"}, field="operating_system")
        if operating_system["family"] != "windows" or operating_system["architecture"] != "x64":
            raise SecurityError("operating_system_not_supported")
        _bounded_text(operating_system["version"], field="operating_system_version", maximum=64)
        key_value = _closed_object(frame["installation_public_key"], required={"algorithm", "key_id", "public_key_spki", "public_key_spki_sha256"}, field="installation_public_key")
        if key_value["algorithm"] != "ECDSA_P256_SHA256":
            raise SecurityError("public_key_algorithm_invalid")
        public_key = load_p256_public_key(key_value["public_key_spki"])
        if len(public_key.der) != 91:
            raise SecurityError("public_key_spki_invalid_length")
        key_id = p256_key_id(public_key)
        if key_value["key_id"] != key_id or key_value["public_key_spki_sha256"] != public_key.fingerprint:
            raise SecurityError("public_key_identity_mismatch")
        reference = self._validate_enrollment_reference(frame["enrollment"])
        proof = _closed_object(frame["proof"], required={"algorithm", "key_id", "challenge_id", "signed_payload_sha256", "signature"}, field="proof")
        if proof["algorithm"] != "ECDSA_P256_SHA256" or proof["key_id"] != key_id:
            raise OperationsAuthentication("proof_key_mismatch")
        challenge_id = _identifier(proof["challenge_id"], "challenge_id")
        if not isinstance(proof["signed_payload_sha256"], str) or not HEX_SHA256_PATTERN.fullmatch(proof["signed_payload_sha256"]):
            raise SecurityError("signed_payload_sha256_invalid")
        offered = _validate_capabilities(frame["capabilities"])
        command_digest = canonical_jcs_sha256(frame)
        current = now_seconds()
        try:
            with self.database.transaction(immediate=True) as connection:
                challenge_row = connection.execute(
                    "SELECT * FROM connector_registration_challenges WHERE id = ?",
                    (challenge_id,),
                ).fetchone()
                if challenge_row is None:
                    raise OperationsAuthentication("registration_challenge_invalid")
                if challenge_row["state"] == "CONSUMED":
                    if (
                        challenge_row["consumed_by_command_id"] == envelope["command_id"]
                        and challenge_row["consumed_command_sha256"] == command_digest
                    ):
                        return json.loads(challenge_row["accepted_json"])
                    raise OperationsConflict("registration_already_consumed")
                if challenge_row["state"] != "ISSUED" or int(challenge_row["expires_at"]) <= current:
                    raise OperationsAuthentication("registration_challenge_expired")
                self._require_command_fresh(envelope, current)
                authorization = connection.execute(
                    "SELECT * FROM connector_enrollment_authorizations WHERE id = ?",
                    (challenge_row["authorization_id"],),
                ).fetchone()
                if authorization is None or authorization["state"] != "AUTHORIZED" or int(authorization["expires_at"]) <= current:
                    raise OperationsAuthentication("enrollment_required_or_expired")
                if (
                    self._enrollment_reference(authorization) != reference
                    or authorization["connector_installation_id"] != installation_id
                    or authorization["key_id"] != key_id
                    or authorization["public_key_spki_sha256"] != public_key.fingerprint
                    or bytes(authorization["public_key_spki"]) != public_key.der
                    or challenge_row["connector_installation_id"] != installation_id
                    or challenge_row["key_id"] != key_id
                    or challenge_row["public_key_spki_sha256"] != public_key.fingerprint
                ):
                    raise OperationsAuthentication("registration_identity_mismatch")
                challenge_message = json.loads(challenge_row["challenge_json"])
                challenge = challenge_message["challenge"]
                unsigned = copy.deepcopy(frame)
                unsigned.pop("proof")
                signed = b"OIN-RUNTIME-V2-REGISTRATION\n" + canonical_jcs_bytes(
                    {"challenge": challenge, "request": unsigned}
                )
                if hashlib.sha256(signed).hexdigest() != proof["signed_payload_sha256"]:
                    raise OperationsAuthentication("signed_payload_digest_mismatch")
                if not verify_p256_p1363_signature(public_key.der, signed, proof["signature"]):
                    raise OperationsAuthentication("registration_signature_invalid")

                accepted_capabilities = copy.deepcopy(offered)
                accepted_capabilities["max_registered_profiles"] = 1
                accepted_capabilities["max_active_profiles"] = 1
                connector_id = str(uuid.uuid4())
                registration_epoch = 1
                accepted_at = _utc_text(current)
                accepted = {
                    "contract": {"name": "oin.runtime", "version": 2},
                    "envelope": {
                        "event_id": str(uuid.uuid4()),
                        "idempotency_key": f"evt:{connector_id}:registration",
                        "event_type": "runtime.registration.accepted",
                        "occurred_at": accepted_at,
                        "produced_at": accepted_at,
                        "producer": {"actor_kind": "system", "actor_id": "oin_control_plane"},
                        "correlation_id": envelope["command_id"],
                    },
                    "wire_direction": "control_plane_to_connector",
                    "connector_installation_id": installation_id,
                    "connector_id": connector_id,
                    "registered_key_id": key_id,
                    "registered_public_key_spki_sha256": public_key.fingerprint,
                    "enrollment": reference,
                    "registration_epoch": registration_epoch,
                    "accepted_at": accepted_at,
                    "accepted_capabilities": accepted_capabilities,
                }
                connection.execute(
                    """
                    INSERT INTO web_connectors(
                        id, tenant_id, connector_installation_id,
                        enrollment_authorization_id, registered_account_id,
                        registered_by_user_id, registered_key_id,
                        registered_public_key_spki_sha256, registered_public_key_spki,
                        registration_epoch, device_generation, runtime_kind, agent_version,
                        capabilities_json, identity_state, state, revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 'windows_web', ?, ?,
                              'PENDING', 'ACTIVE', 1, ?, ?)
                    """,
                    (
                        connector_id, authorization["tenant_id"], installation_id,
                        authorization["id"], authorization["account_id"],
                        authorization["user_id"], key_id, public_key.fingerprint,
                        public_key.der, agent_version, canonical_json(accepted_capabilities),
                        current, current,
                    ),
                )
                updated_authorization = connection.execute(
                    """
                    UPDATE connector_enrollment_authorizations
                    SET state = 'CONSUMED', consumed_at = ?, connector_id = ?,
                        registration_epoch = 1, revision = revision + 1, updated_at = ?
                    WHERE id = ? AND state = 'AUTHORIZED' AND expires_at > ?
                    """,
                    (current, connector_id, current, authorization["id"], current),
                )
                updated_challenge = connection.execute(
                    """
                    UPDATE connector_registration_challenges
                    SET state = 'CONSUMED', consumed_by_command_id = ?,
                        consumed_command_sha256 = ?, consumed_at = ?, accepted_json = ?,
                        revision = revision + 1
                    WHERE id = ? AND state = 'ISSUED' AND expires_at > ?
                    """,
                    (
                        envelope["command_id"], command_digest, current,
                        canonical_json(accepted), challenge_id, current,
                    ),
                )
                if updated_authorization.rowcount != 1 or updated_challenge.rowcount != 1:
                    raise OperationsConflict("registration_authority_raced")
                self._audit(connection, authorization["account_id"], "web_connector_registered", True, {"tenant_id": authorization["tenant_id"], "connector_id": connector_id, "connector_installation_id": installation_id})
                return accepted
        except IntegrityError as exc:
            raise OperationsConflict("connector_registration_conflict") from exc

    def create_profile(
        self, account_id: str, user_id: str, command: dict[str, Any]
    ) -> OperationOutcome:
        command = _closed_object(
            command,
            required={"idempotency_key", "tenant_id", "connector_id", "profile_id"},
            field="profile_create",
        )
        tenant_id = _identifier(command["tenant_id"], "tenant_id")
        connector_id = _identifier(command["connector_id"], "connector_id")
        profile_id = _identifier(command["profile_id"], "profile_id")
        idem = _idempotency_key(command["idempotency_key"])
        digest = canonical_jcs_sha256({"operation": "profile.create", "account_id": account_id, "user_id": user_id, **command})
        current = now_seconds()
        try:
            with self.database.transaction(immediate=True) as connection:
                self._require_actor(connection, account_id, user_id)
                self._require_tenant_member(connection, account_id, user_id, tenant_id)
                replay = self._receipt(connection, tenant_id, idem, account_id, digest)
                if replay is not None:
                    return OperationOutcome(replay, True)
                connector = connection.execute(
                    "SELECT * FROM web_connectors WHERE tenant_id = ? AND id = ? AND state = 'ACTIVE'",
                    (tenant_id, connector_id),
                ).fetchone()
                if connector is None:
                    raise OperationsNotFound("connector_route_not_found")
                capabilities = json.loads(connector["capabilities_json"])
                count = connection.execute(
                    """
                    SELECT COUNT(*) AS value FROM connector_profiles
                    WHERE tenant_id = ? AND connector_id = ? AND state NOT IN ('REVOKED', 'DELETED')
                    """,
                    (tenant_id, connector_id),
                ).fetchone()["value"]
                if int(count) >= int(capabilities["max_registered_profiles"]):
                    raise OperationsConflict("profile_capacity_reached")
                connection.execute(
                    """
                    INSERT INTO connector_profiles(
                        id, profile_generation, tenant_id, connector_id, runtime_kind,
                        state, revision, created_at, updated_at
                    ) VALUES (?, 1, ?, ?, 'windows_web', 'CREATED', 1, ?, ?)
                    """,
                    (profile_id, tenant_id, connector_id, current, current),
                )
                row = connection.execute(
                    """
                    SELECT * FROM connector_profiles
                    WHERE tenant_id = ? AND connector_id = ? AND id = ? AND profile_generation = 1
                    """,
                    (tenant_id, connector_id, profile_id),
                ).fetchone()
                result = {"profile": self._profile(row)}
                self._audit(connection, account_id, "connector_profile_created", True, {"tenant_id": tenant_id, "connector_id": connector_id, "profile_id": profile_id})
                self._save_receipt(connection, tenant_id=tenant_id, idempotency_key=idem, operation_kind="profile.create", account_id=account_id, command_sha256=digest, result=result, current=current)
                return OperationOutcome(result)
        except IntegrityError as exc:
            raise OperationsConflict("profile_create_conflict") from exc

    def create_binding(
        self, account_id: str, user_id: str, command: dict[str, Any]
    ) -> OperationOutcome:
        command = _closed_object(
            command,
            required={
                "idempotency_key", "tenant_id", "merchant_id", "shop_id", "platform",
                "principal_id", "connector_id", "profile_id", "profile_generation",
                "binding_id", "runtime_kind", "adapter_id", "adapter_version", "capabilities",
            },
            field="binding_create",
        )
        tenant_id = _identifier(command["tenant_id"], "tenant_id")
        merchant_id = _identifier(command["merchant_id"], "merchant_id")
        shop_id = _identifier(command["shop_id"], "shop_id")
        platform = PDD_ROUTE.platform_to_storage(command["platform"])
        principal_id = _identifier(command["principal_id"], "principal_id")
        connector_id = _identifier(command["connector_id"], "connector_id")
        profile_id = _identifier(command["profile_id"], "profile_id")
        profile_generation = _positive_integer(command["profile_generation"], "profile_generation")
        binding_id = _identifier(command["binding_id"], "binding_id")
        PDD_ROUTE.require_runtime(command["runtime_kind"])
        adapter_id = PDD_ROUTE.adapter_to_storage(command["adapter_id"])
        adapter_version = _version(command["adapter_version"], "adapter_version")
        capabilities_raw = command["capabilities"]
        if not isinstance(capabilities_raw, list) or not 1 <= len(capabilities_raw) <= 32:
            raise SecurityError("binding_capabilities_invalid")
        capabilities = [_platform_key(item, "binding_capability") for item in capabilities_raw]
        if len(set(capabilities)) != len(capabilities):
            raise SecurityError("binding_capabilities_duplicate")
        idem = _idempotency_key(command["idempotency_key"])
        normalized = {**command, "capabilities": capabilities}
        digest = canonical_jcs_sha256({"operation": "binding.create", "account_id": account_id, "user_id": user_id, **normalized})
        current = now_seconds()
        try:
            with self.database.transaction(immediate=True) as connection:
                self._require_actor(connection, account_id, user_id)
                self._require_merchant_member(connection, account_id, user_id, tenant_id, merchant_id)
                replay = self._receipt(connection, tenant_id, idem, account_id, digest)
                if replay is not None:
                    return OperationOutcome(replay, True)
                exact = connection.execute(
                    """
                    SELECT 1 FROM platform_principal_shops ps
                    JOIN platform_shops s
                      ON s.tenant_id = ps.tenant_id AND s.merchant_id = ps.merchant_id
                     AND s.platform = ps.platform AND s.id = ps.shop_id
                    JOIN platform_principals p
                      ON p.tenant_id = ps.tenant_id AND p.merchant_id = ps.merchant_id
                     AND p.platform = ps.platform AND p.id = ps.principal_id
                    JOIN connector_profiles cp
                      ON cp.tenant_id = ? AND cp.connector_id = ? AND cp.id = ?
                     AND cp.profile_generation = ? AND cp.runtime_kind = 'windows_web'
                    JOIN web_connectors c
                      ON c.tenant_id = cp.tenant_id AND c.id = cp.connector_id
                    WHERE ps.tenant_id = ? AND ps.merchant_id = ? AND ps.platform = ?
                      AND ps.principal_id = ? AND ps.shop_id = ?
                      AND s.state = 'ACTIVE' AND p.state = 'ACTIVE'
                      AND cp.state = 'CREATED' AND c.state = 'ACTIVE'
                    """,
                    (
                        tenant_id, connector_id, profile_id, profile_generation,
                        tenant_id, merchant_id, platform, principal_id, shop_id,
                    ),
                ).fetchone()
                if exact is None:
                    raise OperationsNotFound("binding_route_not_found")
                connection.execute(
                    """
                    INSERT INTO platform_account_bindings(
                        id, binding_generation, tenant_id, merchant_id, shop_id, platform,
                        principal_id, connector_id, profile_id, profile_generation,
                        runtime_kind, adapter_id, adapter_version, capabilities_json,
                        identity_state, verified_identity_revision, state, bound_at,
                        unbound_at, revision, created_at, updated_at
                    ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, 'windows_web', ?, ?, ?,
                              'PENDING', NULL, 'PENDING', ?, NULL, 1, ?, ?)
                    """,
                    (
                        binding_id, tenant_id, merchant_id, shop_id, platform,
                        principal_id, connector_id, profile_id, profile_generation,
                        adapter_id, adapter_version, canonical_json(capabilities),
                        current, current, current,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM platform_account_bindings WHERE tenant_id = ? AND id = ? AND binding_generation = 1",
                    (tenant_id, binding_id),
                ).fetchone()
                result = {"binding": self._binding(row)}
                self._audit(connection, account_id, "platform_binding_created", True, {"tenant_id": tenant_id, "merchant_id": merchant_id, "shop_id": shop_id, "principal_id": principal_id, "connector_id": connector_id, "profile_id": profile_id, "binding_id": binding_id})
                self._save_receipt(connection, tenant_id=tenant_id, idempotency_key=idem, operation_kind="binding.create", account_id=account_id, command_sha256=digest, result=result, current=current)
                return OperationOutcome(result)
        except IntegrityError as exc:
            raise OperationsConflict("binding_create_conflict") from exc

    def record_authenticated_identity_observation(
        self, frame: dict[str, Any]
    ) -> OperationOutcome:
        """Authenticate one canonical HTTP ingress before constructing Store context."""

        parsed = _parse_identity_ingress_frame(frame, current=now_seconds())
        with self.database.connect() as connection:
            connector = connection.execute(
                """
                SELECT * FROM web_connectors
                WHERE connector_installation_id = ? AND registered_key_id = ?
                  AND state = 'ACTIVE'
                """,
                (parsed["connector_installation_id"], parsed["key_id"]),
            ).fetchone()
        if connector is None or not verify_p256_p1363_signature(
            bytes(connector["registered_public_key_spki"]),
            parsed["signed_payload"],
            parsed["signature"],
        ):
            raise OperationsAuthentication("identity_ingress_proof_invalid")

        authenticated = AuthenticatedConnectorContext(
            tenant_id=connector["tenant_id"],
            connector_id=connector["id"],
            connector_installation_id=connector["connector_installation_id"],
            registered_key_id=connector["registered_key_id"],
            registered_public_key_spki_sha256=connector[
                "registered_public_key_spki_sha256"
            ],
        )
        return self.record_identity_observation(
            authenticated,
            parsed["observation"],
        )

    def record_identity_observation(
        self,
        authenticated_connector: AuthenticatedConnectorContext,
        observation: dict[str, Any],
    ) -> OperationOutcome:
        """Durably admit one PDD identity observation without changing authority."""

        current = now_seconds()
        parsed = _parse_identity_observation(observation, current=current)
        normalized = parsed["observation"]
        evidence = normalized["evidence"]
        envelope = normalized["envelope"]
        admission = normalized["admission"]
        try:
            authenticated_tenant_id = _identifier(
                authenticated_connector.tenant_id, "authenticated_tenant_id"
            )
            authenticated_connector_id = _identifier(
                authenticated_connector.connector_id, "authenticated_connector_id"
            )
            authenticated_installation_id = _identifier(
                authenticated_connector.connector_installation_id,
                "authenticated_connector_installation_id",
            )
            authenticated_key_id = _bounded_text(
                authenticated_connector.registered_key_id,
                field="authenticated_registered_key_id",
                maximum=96,
            )
            authenticated_key_sha256 = (
                authenticated_connector.registered_public_key_spki_sha256
            )
            if (
                not isinstance(authenticated_key_sha256, str)
                or not HEX_SHA256_PATTERN.fullmatch(authenticated_key_sha256)
            ):
                raise SecurityError("authenticated_registered_key_sha256_invalid")
        except (AttributeError, SecurityError) as exc:
            raise OperationsAuthentication(
                "identity_observation_authentication_failed"
            ) from exc
        if (
            evidence["tenant_id"] != authenticated_tenant_id
            or evidence["connector_id"] != authenticated_connector_id
            or envelope["producer"]["actor_id"] != authenticated_connector_id
        ):
            raise OperationsAuthentication(
                "identity_observation_authentication_failed"
            )

        try:
            with self.database.transaction(immediate=True) as connection:
                connector = connection.execute(
                    """
                    SELECT * FROM web_connectors
                    WHERE tenant_id = ? AND id = ?
                    """,
                    (authenticated_tenant_id, authenticated_connector_id),
                ).fetchone()
                if (
                    connector is None
                    or connector["state"] != "ACTIVE"
                    or connector["connector_installation_id"]
                    != authenticated_installation_id
                    or connector["registered_key_id"] != authenticated_key_id
                    or connector["registered_public_key_spki_sha256"]
                    != authenticated_key_sha256
                ):
                    raise OperationsAuthentication(
                        "identity_observation_authentication_failed"
                    )

                existing = connection.execute(
                    """
                    SELECT * FROM identity_evidence
                    WHERE tenant_id = ? AND connector_id = ? AND idempotency_key = ?
                    """,
                    (
                        authenticated_tenant_id,
                        authenticated_connector_id,
                        envelope["idempotency_key"],
                    ),
                ).fetchone()
                if existing is not None:
                    if not secrets.compare_digest(
                        existing["observation_sha256"],
                        parsed["observation_sha256"],
                    ):
                        raise OperationsConflict(
                            "identity_observation_idempotency_reuse"
                        )
                    return OperationOutcome(
                        {"observation": self._identity_observation_view(existing)},
                        True,
                    )

                route = connection.execute(
                    """
                    SELECT b.*,
                           c.registered_account_id,
                           c.state AS connector_state,
                           cp.state AS profile_state,
                           m.state AS merchant_state,
                           s.state AS shop_state,
                           s.provider_shop_id AS existing_provider_shop_id,
                           s.identity_state AS shop_identity_state,
                           s.revision AS shop_revision,
                           p.state AS principal_state,
                           p.provider_principal_id AS existing_provider_principal_id,
                           p.identity_state AS principal_identity_state,
                           p.revision AS principal_revision,
                           b.state AS binding_lifecycle_state,
                           b.identity_state AS binding_identity_state,
                           b.revision AS binding_revision,
                           b.verified_identity_revision AS expected_verified_identity_revision
                    FROM platform_account_bindings b
                    JOIN merchants m
                      ON m.tenant_id = b.tenant_id AND m.id = b.merchant_id
                    JOIN platform_shops s
                      ON s.tenant_id = b.tenant_id AND s.merchant_id = b.merchant_id
                     AND s.platform = b.platform AND s.id = b.shop_id
                    JOIN platform_principals p
                      ON p.tenant_id = b.tenant_id AND p.merchant_id = b.merchant_id
                     AND p.platform = b.platform AND p.id = b.principal_id
                    JOIN connector_profiles cp
                      ON cp.tenant_id = b.tenant_id AND cp.connector_id = b.connector_id
                     AND cp.id = b.profile_id
                     AND cp.profile_generation = b.profile_generation
                    JOIN web_connectors c
                      ON c.tenant_id = b.tenant_id AND c.id = b.connector_id
                    WHERE b.tenant_id = ? AND b.merchant_id = ?
                      AND b.shop_id = ? AND b.principal_id = ?
                      AND b.connector_id = ? AND b.profile_id = ?
                      AND b.profile_generation = ? AND b.id = ?
                      AND b.binding_generation = ? AND b.platform = ?
                      AND b.adapter_id = ? AND b.adapter_version = ?
                      AND b.runtime_kind = ?
                    """,
                    (
                        evidence["tenant_id"],
                        evidence["merchant_id"],
                        evidence["shop_id"],
                        evidence["principal_id"],
                        evidence["connector_id"],
                        evidence["profile_id"],
                        evidence["profile_generation"],
                        evidence["binding_id"],
                        evidence["binding_generation"],
                        parsed["storage_platform"],
                        parsed["storage_adapter"],
                        evidence["adapter_version"],
                        admission["runtime_kind"],
                    ),
                ).fetchone()
                if route is None:
                    raise OperationsNotFound(
                        "identity_observation_route_not_found"
                    )
                if (
                    route["connector_state"] != "ACTIVE"
                    or route["profile_state"] not in {"CREATED", "ACTIVE"}
                    or route["merchant_state"] != "ACTIVE"
                    or route["shop_state"] != "ACTIVE"
                    or route["principal_state"] != "ACTIVE"
                    or route["binding_lifecycle_state"] != "PENDING"
                    or route["shop_identity_state"] != "PENDING"
                    or route["principal_identity_state"] != "PENDING"
                    or route["binding_identity_state"] != "PENDING"
                    or route["expected_verified_identity_revision"] is not None
                    or json.loads(route["capabilities_json"])
                    != ["message.observe"]
                ):
                    raise OperationsConflict(
                        "identity_observation_requires_pending_route"
                    )

                provider_shop_id = evidence["provider_shop_identity"][
                    "provider_platform_shop_id"
                ]
                provider_principal_id = evidence["provider_principal_identity"][
                    "provider_platform_principal_id"
                ]
                if (
                    route["existing_provider_shop_id"] is not None
                    and route["existing_provider_shop_id"] != provider_shop_id
                ) or (
                    route["existing_provider_principal_id"] is not None
                    and route["existing_provider_principal_id"]
                    != provider_principal_id
                ):
                    raise OperationsConflict(
                        "identity_observation_identity_conflict"
                    )

                expected_fence = pdd_identity_authority_fence_sha256(
                    route={
                        "tenant_id": route["tenant_id"],
                        "merchant_id": route["merchant_id"],
                        "shop_id": route["shop_id"],
                        "principal_id": route["principal_id"],
                        "connector_id": route["connector_id"],
                        "profile_id": route["profile_id"],
                        "profile_generation": int(route["profile_generation"]),
                        "binding_id": route["id"],
                        "binding_generation": int(route["binding_generation"]),
                        "platform": PDD_ROUTE.platform_to_public(route["platform"]),
                        "adapter_id": PDD_ROUTE.adapter_to_public(
                            route["adapter_id"]
                        ),
                        "adapter_version": route["adapter_version"],
                        "runtime_kind": route["runtime_kind"],
                    },
                    authority={
                        "shop_revision": int(route["shop_revision"]),
                        "principal_revision": int(route["principal_revision"]),
                        "binding_revision": int(route["binding_revision"]),
                        "binding_lifecycle_state": route[
                            "binding_lifecycle_state"
                        ],
                        "identity_state": route["binding_identity_state"],
                        "verified_identity_revision": route[
                            "expected_verified_identity_revision"
                        ],
                    },
                    runtime_session_id=admission["runtime_session_id"],
                )
                if not secrets.compare_digest(
                    expected_fence, admission["authority_fence_sha256"]
                ):
                    raise OperationsConflict(
                        "identity_observation_authority_fence_stale"
                    )

                if connection.execute(
                    "SELECT 1 FROM identity_evidence WHERE evidence_id = ?",
                    (evidence["evidence_id"],),
                ).fetchone() is not None:
                    raise OperationsConflict("identity_evidence_id_reuse")
                if connection.execute(
                    """
                    SELECT 1 FROM identity_evidence
                    WHERE tenant_id = ? AND connector_id = ? AND event_id = ?
                    """,
                    (
                        evidence["tenant_id"],
                        evidence["connector_id"],
                        envelope["event_id"],
                    ),
                ).fetchone() is not None:
                    raise OperationsConflict("identity_observation_event_id_reuse")

                connection.execute(
                    """
                    INSERT INTO identity_evidence(
                        evidence_id, event_id, idempotency_key,
                        observation_sha256, tenant_id, merchant_id, shop_id,
                        principal_id, connector_id, connector_installation_id,
                        registered_key_id, registered_public_key_spki_sha256,
                        profile_id, profile_generation, binding_id,
                        binding_generation, platform, adapter_id, adapter_version,
                        runtime_kind, runtime_session_id, authority_fence_sha256,
                        page_capability, source_kind, source_version, mall_name,
                        provider_shop_id, provider_principal_id, identity_quality,
                        observed_at, produced_at, expires_at, evidence_sha256,
                        observation_state, shop_revision, principal_revision,
                        binding_revision, created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, 'strong', ?, ?, ?, ?, 'PENDING',
                        ?, ?, ?, ?
                    )
                    """,
                    (
                        evidence["evidence_id"],
                        envelope["event_id"],
                        envelope["idempotency_key"],
                        parsed["observation_sha256"],
                        evidence["tenant_id"],
                        evidence["merchant_id"],
                        evidence["shop_id"],
                        evidence["principal_id"],
                        evidence["connector_id"],
                        authenticated_installation_id,
                        authenticated_key_id,
                        authenticated_key_sha256,
                        evidence["profile_id"],
                        evidence["profile_generation"],
                        evidence["binding_id"],
                        evidence["binding_generation"],
                        parsed["storage_platform"],
                        parsed["storage_adapter"],
                        evidence["adapter_version"],
                        admission["runtime_kind"],
                        admission["runtime_session_id"],
                        admission["authority_fence_sha256"],
                        admission["page_capability"],
                        admission["source_kind"],
                        admission["source_version"],
                        admission["mall_name"],
                        provider_shop_id,
                        provider_principal_id,
                        parsed["observed_at"],
                        parsed["produced_at"],
                        parsed["expires_at"],
                        evidence["evidence_sha256"],
                        int(route["shop_revision"]),
                        int(route["principal_revision"]),
                        int(route["binding_revision"]),
                        current,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM identity_evidence WHERE evidence_id = ?",
                    (evidence["evidence_id"],),
                ).fetchone()
                self._audit(
                    connection,
                    connector["registered_account_id"],
                    "pdd_identity_observation_persisted",
                    True,
                    {
                        "evidence_id": evidence["evidence_id"],
                        "tenant_id": evidence["tenant_id"],
                        "merchant_id": evidence["merchant_id"],
                        "shop_id": evidence["shop_id"],
                        "principal_id": evidence["principal_id"],
                        "connector_id": evidence["connector_id"],
                        "profile_id": evidence["profile_id"],
                        "profile_generation": evidence["profile_generation"],
                        "binding_id": evidence["binding_id"],
                        "binding_generation": evidence["binding_generation"],
                        "evidence_sha256": evidence["evidence_sha256"],
                        "observation_state": "PENDING",
                        "shop_revision": int(route["shop_revision"]),
                        "principal_revision": int(route["principal_revision"]),
                        "binding_revision": int(route["binding_revision"]),
                    },
                )
                return OperationOutcome(
                    {"observation": self._identity_observation_view(row)}
                )
        except IntegrityError as exc:
            raise OperationsConflict("identity_observation_conflict") from exc

    def confirm_identity(
        self,
        account_id: str,
        user_id: str,
        command_value: dict[str, Any],
    ) -> OperationOutcome:
        """Atomically confirm one current evidence snapshot as formal authority."""

        parsed = _parse_identity_confirmation_command(
            command_value,
            account_id=account_id,
            user_id=user_id,
        )
        command = parsed["command"]
        envelope = command["envelope"]
        tenant_id = command["tenant_id"]
        idempotency_key = envelope["idempotency_key"]
        current = now_seconds()

        try:
            with self.database.transaction(immediate=True) as connection:
                self._require_actor(connection, account_id, user_id)
                membership = connection.execute(
                    """
                    SELECT membership.*
                    FROM memberships membership
                    JOIN tenants tenant
                      ON tenant.id = membership.tenant_id
                     AND tenant.state = 'ACTIVE'
                    JOIN merchants merchant
                      ON merchant.tenant_id = membership.tenant_id
                     AND merchant.id = membership.merchant_id
                     AND merchant.state = 'ACTIVE'
                    WHERE membership.tenant_id = ? AND membership.id = ?
                      AND membership.account_id = ? AND membership.user_id = ?
                      AND membership.scope_kind = 'merchant'
                      AND membership.state = 'ACTIVE'
                    """,
                    (
                        tenant_id,
                        command["membership_id"],
                        account_id,
                        user_id,
                    ),
                ).fetchone()
                if membership is None:
                    raise OperationsNotFound(
                        "identity_confirmation_route_not_found"
                    )
                replay = self._receipt(
                    connection,
                    tenant_id,
                    idempotency_key,
                    account_id,
                    parsed["command_sha256"],
                )
                if replay is not None:
                    return OperationOutcome(copy.deepcopy(replay), True)

                def rejected(code: str, detail_code: str) -> OperationOutcome:
                    result = {
                        "confirmation": self._identity_confirmation_result(
                            parsed,
                            result="REJECTED",
                            confirmed_at=current,
                            error=self._confirmation_error(code, detail_code),
                        ),
                        "route_authority": None,
                    }
                    self._save_receipt(
                        connection,
                        tenant_id=tenant_id,
                        idempotency_key=idempotency_key,
                        operation_kind="identity.confirm",
                        account_id=account_id,
                        command_sha256=parsed["command_sha256"],
                        result=result,
                        current=current,
                    )
                    self._audit(
                        connection,
                        account_id,
                        "pdd_identity_confirmation_rejected",
                        False,
                        {
                            "confirmation_id": command["confirmation_id"],
                            "tenant_id": tenant_id,
                            "membership_id": command["membership_id"],
                            "evidence_id": command["evidence_id"],
                            "evidence_sha256": command["evidence_sha256"],
                            "error_code": code,
                            "detail_code": detail_code,
                        },
                    )
                    return OperationOutcome(result)

                if int(membership["revision"]) != command["membership_revision"]:
                    return rejected(
                        "REVISION_CONFLICT",
                        "identity_confirmation_membership_revision_stale",
                    )
                if (
                    parsed["issued_at"] > current + 30
                    or parsed["issued_at"] < current - 300
                ):
                    return rejected("EXPIRED", "identity_confirmation_command_stale")

                evidence = connection.execute(
                    """
                    SELECT * FROM identity_evidence
                    WHERE tenant_id = ? AND merchant_id = ? AND evidence_id = ?
                    """,
                    (
                        tenant_id,
                        membership["merchant_id"],
                        command["evidence_id"],
                    ),
                ).fetchone()
                if evidence is None:
                    return rejected(
                        "IDENTITY_EVIDENCE_MISMATCH",
                        "identity_confirmation_evidence_not_visible",
                    )
                if (
                    not secrets.compare_digest(
                        evidence["evidence_sha256"], command["evidence_sha256"]
                    )
                    or int(evidence["expires_at"]) != parsed["expires_at"]
                ):
                    return rejected(
                        "IDENTITY_EVIDENCE_MISMATCH",
                        "identity_confirmation_evidence_digest_mismatch",
                    )
                if int(evidence["expires_at"]) <= current:
                    return rejected(
                        "IDENTITY_EVIDENCE_EXPIRED",
                        "identity_confirmation_evidence_expired",
                    )
                if evidence["observation_state"] != "PENDING":
                    return rejected(
                        "STATE_CONFLICT",
                        "identity_confirmation_evidence_state_invalid",
                    )
                if connection.execute(
                    """
                    SELECT 1 FROM identity_confirmations
                    WHERE tenant_id = ? AND evidence_id = ?
                    """,
                    (tenant_id, evidence["evidence_id"]),
                ).fetchone() is not None:
                    return rejected(
                        "STATE_CONFLICT",
                        "identity_confirmation_evidence_already_confirmed",
                    )
                if connection.execute(
                    """
                    SELECT 1 FROM identity_confirmations
                    WHERE confirmation_id = ?
                       OR (tenant_id = ? AND command_id = ?)
                    """,
                    (
                        command["confirmation_id"],
                        tenant_id,
                        envelope["command_id"],
                    ),
                ).fetchone() is not None:
                    return rejected(
                        "IDEMPOTENCY_CONFLICT",
                        "identity_confirmation_identifier_reuse",
                    )

                route = connection.execute(
                    """
                    SELECT b.*,
                           merchant.state AS merchant_state,
                           shop.provider_shop_id AS existing_provider_shop_id,
                           shop.identity_state AS shop_identity_state,
                           shop.state AS shop_state,
                           shop.revision AS shop_revision,
                           principal.provider_principal_id AS existing_provider_principal_id,
                           principal.identity_state AS principal_identity_state,
                           principal.state AS principal_state,
                           principal.revision AS principal_revision,
                           connector.connector_installation_id,
                           connector.registered_key_id,
                           connector.registered_public_key_spki_sha256,
                           connector.state AS connector_state,
                           profile.state AS profile_state,
                           b.state AS binding_lifecycle_state,
                           b.identity_state AS binding_identity_state,
                           b.revision AS binding_revision,
                           b.verified_identity_revision AS expected_verified_identity_revision
                    FROM platform_account_bindings b
                    JOIN merchants merchant
                      ON merchant.tenant_id = b.tenant_id
                     AND merchant.id = b.merchant_id
                    JOIN platform_shops shop
                      ON shop.tenant_id = b.tenant_id
                     AND shop.merchant_id = b.merchant_id
                     AND shop.platform = b.platform
                     AND shop.id = b.shop_id
                    JOIN platform_principals principal
                      ON principal.tenant_id = b.tenant_id
                     AND principal.merchant_id = b.merchant_id
                     AND principal.platform = b.platform
                     AND principal.id = b.principal_id
                    JOIN connector_profiles profile
                      ON profile.tenant_id = b.tenant_id
                     AND profile.connector_id = b.connector_id
                     AND profile.id = b.profile_id
                     AND profile.profile_generation = b.profile_generation
                    JOIN web_connectors connector
                      ON connector.tenant_id = b.tenant_id
                     AND connector.id = b.connector_id
                    WHERE b.tenant_id = ? AND b.merchant_id = ?
                      AND b.shop_id = ? AND b.principal_id = ?
                      AND b.connector_id = ? AND b.profile_id = ?
                      AND b.profile_generation = ? AND b.id = ?
                      AND b.binding_generation = ? AND b.platform = ?
                      AND b.adapter_id = ? AND b.adapter_version = ?
                      AND b.runtime_kind = ?
                    """,
                    (
                        evidence["tenant_id"],
                        evidence["merchant_id"],
                        evidence["shop_id"],
                        evidence["principal_id"],
                        evidence["connector_id"],
                        evidence["profile_id"],
                        int(evidence["profile_generation"]),
                        evidence["binding_id"],
                        int(evidence["binding_generation"]),
                        evidence["platform"],
                        evidence["adapter_id"],
                        evidence["adapter_version"],
                        evidence["runtime_kind"],
                    ),
                ).fetchone()
                if route is None:
                    return rejected(
                        "REVISION_CONFLICT",
                        "identity_confirmation_route_stale",
                    )
                if (
                    route["merchant_state"] != "ACTIVE"
                    or route["shop_state"] != "ACTIVE"
                    or route["principal_state"] != "ACTIVE"
                    or route["connector_state"] != "ACTIVE"
                    or route["profile_state"] not in {"CREATED", "ACTIVE"}
                    or route["binding_lifecycle_state"] != "PENDING"
                    or route["shop_identity_state"] != "PENDING"
                    or route["principal_identity_state"] != "PENDING"
                    or route["binding_identity_state"] != "PENDING"
                    or route["expected_verified_identity_revision"] is not None
                    or json.loads(route["capabilities_json"])
                    != ["message.observe"]
                    or route["connector_installation_id"]
                    != evidence["connector_installation_id"]
                    or route["registered_key_id"] != evidence["registered_key_id"]
                    or route["registered_public_key_spki_sha256"]
                    != evidence["registered_public_key_spki_sha256"]
                ):
                    return rejected(
                        "STATE_CONFLICT",
                        "identity_confirmation_route_not_pending",
                    )
                if (
                    int(route["shop_revision"]) != int(evidence["shop_revision"])
                    or int(route["principal_revision"])
                    != int(evidence["principal_revision"])
                    or int(route["binding_revision"])
                    != int(evidence["binding_revision"])
                ):
                    return rejected(
                        "REVISION_CONFLICT",
                        "identity_confirmation_route_revision_stale",
                    )

                expected_fence = pdd_identity_authority_fence_sha256(
                    route={
                        "tenant_id": route["tenant_id"],
                        "merchant_id": route["merchant_id"],
                        "shop_id": route["shop_id"],
                        "principal_id": route["principal_id"],
                        "connector_id": route["connector_id"],
                        "profile_id": route["profile_id"],
                        "profile_generation": int(route["profile_generation"]),
                        "binding_id": route["id"],
                        "binding_generation": int(route["binding_generation"]),
                        "platform": PDD_ROUTE.platform_to_public(route["platform"]),
                        "adapter_id": PDD_ROUTE.adapter_to_public(route["adapter_id"]),
                        "adapter_version": route["adapter_version"],
                        "runtime_kind": route["runtime_kind"],
                    },
                    authority={
                        "shop_revision": int(route["shop_revision"]),
                        "principal_revision": int(route["principal_revision"]),
                        "binding_revision": int(route["binding_revision"]),
                        "binding_lifecycle_state": route[
                            "binding_lifecycle_state"
                        ],
                        "identity_state": route["binding_identity_state"],
                        "verified_identity_revision": route[
                            "expected_verified_identity_revision"
                        ],
                    },
                    runtime_session_id=evidence["runtime_session_id"],
                )
                if not secrets.compare_digest(
                    expected_fence, evidence["authority_fence_sha256"]
                ):
                    return rejected(
                        "REVISION_CONFLICT",
                        "identity_confirmation_authority_fence_stale",
                    )

                provider_shop_id = evidence["provider_shop_id"]
                provider_principal_id = evidence["provider_principal_id"]
                if (
                    route["existing_provider_shop_id"] is not None
                    and route["existing_provider_shop_id"] != provider_shop_id
                ) or (
                    route["existing_provider_principal_id"] is not None
                    and route["existing_provider_principal_id"]
                    != provider_principal_id
                ):
                    return rejected(
                        "IDENTITY_MISMATCH",
                        "identity_confirmation_provider_identity_conflict",
                    )
                if connection.execute(
                    """
                    SELECT 1 FROM platform_shops
                    WHERE tenant_id = ? AND platform = ? AND provider_shop_id = ?
                      AND id <> ?
                    """,
                    (
                        tenant_id,
                        evidence["platform"],
                        provider_shop_id,
                        evidence["shop_id"],
                    ),
                ).fetchone() is not None or connection.execute(
                    """
                    SELECT 1 FROM platform_principals
                    WHERE tenant_id = ? AND platform = ?
                      AND provider_principal_id = ? AND id <> ?
                    """,
                    (
                        tenant_id,
                        evidence["platform"],
                        provider_principal_id,
                        evidence["principal_id"],
                    ),
                ).fetchone() is not None:
                    return rejected(
                        "IDENTITY_MISMATCH",
                        "identity_confirmation_provider_identity_already_bound",
                    )

                revision_row = connection.execute(
                    """
                    SELECT MAX(verified_identity_revision) AS value
                    FROM platform_account_bindings
                    WHERE tenant_id = ? AND id = ?
                    """,
                    (tenant_id, evidence["binding_id"]),
                ).fetchone()
                verified_identity_revision = (
                    int(revision_row["value"])
                    if revision_row is not None and revision_row["value"] is not None
                    else 0
                ) + 1

                shop_update = connection.execute(
                    """
                    UPDATE platform_shops
                    SET provider_shop_id = ?, provider_shop_ref = NULL,
                        identity_quality = 'strong', identity_state = 'VERIFIED',
                        revision = revision + 1, updated_at = ?
                    WHERE tenant_id = ? AND merchant_id = ? AND platform = ?
                      AND id = ? AND revision = ? AND state = 'ACTIVE'
                      AND identity_state = 'PENDING'
                      AND (provider_shop_id IS NULL OR provider_shop_id = ?)
                    """,
                    (
                        provider_shop_id,
                        current,
                        tenant_id,
                        evidence["merchant_id"],
                        evidence["platform"],
                        evidence["shop_id"],
                        int(evidence["shop_revision"]),
                        provider_shop_id,
                    ),
                )
                principal_update = connection.execute(
                    """
                    UPDATE platform_principals
                    SET provider_principal_id = ?, provider_principal_ref = NULL,
                        identity_quality = 'strong', identity_state = 'VERIFIED',
                        revision = revision + 1, updated_at = ?
                    WHERE tenant_id = ? AND merchant_id = ? AND platform = ?
                      AND id = ? AND revision = ? AND state = 'ACTIVE'
                      AND identity_state = 'PENDING'
                      AND (provider_principal_id IS NULL OR provider_principal_id = ?)
                    """,
                    (
                        provider_principal_id,
                        current,
                        tenant_id,
                        evidence["merchant_id"],
                        evidence["platform"],
                        evidence["principal_id"],
                        int(evidence["principal_revision"]),
                        provider_principal_id,
                    ),
                )
                binding_update = connection.execute(
                    """
                    UPDATE platform_account_bindings
                    SET identity_state = 'VERIFIED',
                        verified_identity_revision = ?, state = 'ACTIVE',
                        revision = revision + 1, updated_at = ?
                    WHERE tenant_id = ? AND id = ? AND binding_generation = ?
                      AND revision = ? AND state = 'PENDING'
                      AND identity_state = 'PENDING'
                      AND verified_identity_revision IS NULL
                    """,
                    (
                        verified_identity_revision,
                        current,
                        tenant_id,
                        evidence["binding_id"],
                        int(evidence["binding_generation"]),
                        int(evidence["binding_revision"]),
                    ),
                )
                if (
                    shop_update.rowcount != 1
                    or principal_update.rowcount != 1
                    or binding_update.rowcount != 1
                ):
                    raise OperationsConflict("identity_confirmation_cas_raced")

                connection.execute(
                    """
                    UPDATE connector_route_activations
                    SET state = 'REVOKED', revision = revision + 1, updated_at = ?
                    WHERE tenant_id = ? AND connector_id = ? AND profile_id = ?
                      AND binding_id = ? AND state = 'ISSUED'
                    """,
                    (
                        current,
                        tenant_id,
                        evidence["connector_id"],
                        evidence["profile_id"],
                        evidence["binding_id"],
                    ),
                )

                final_shop_revision = int(evidence["shop_revision"]) + 1
                final_principal_revision = int(evidence["principal_revision"]) + 1
                final_binding_revision = int(evidence["binding_revision"]) + 1
                connection.execute(
                    """
                    INSERT INTO identity_confirmations(
                        confirmation_id, tenant_id, merchant_id, evidence_id,
                        command_id, command_idempotency_key, command_sha256,
                        account_id, user_id, membership_id, membership_revision,
                        evidence_sha256, evidence_expires_at, shop_id, principal_id,
                        connector_id, profile_id, profile_generation, binding_id,
                        binding_generation, platform, adapter_id, adapter_version,
                        runtime_kind, verified_identity_revision, shop_revision,
                        principal_revision, binding_revision, result, confirmed_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, 'CONFIRMED', ?
                    )
                    """,
                    (
                        command["confirmation_id"],
                        tenant_id,
                        evidence["merchant_id"],
                        evidence["evidence_id"],
                        envelope["command_id"],
                        idempotency_key,
                        parsed["command_sha256"],
                        account_id,
                        user_id,
                        command["membership_id"],
                        command["membership_revision"],
                        evidence["evidence_sha256"],
                        int(evidence["expires_at"]),
                        evidence["shop_id"],
                        evidence["principal_id"],
                        evidence["connector_id"],
                        evidence["profile_id"],
                        int(evidence["profile_generation"]),
                        evidence["binding_id"],
                        int(evidence["binding_generation"]),
                        evidence["platform"],
                        evidence["adapter_id"],
                        evidence["adapter_version"],
                        evidence["runtime_kind"],
                        verified_identity_revision,
                        final_shop_revision,
                        final_principal_revision,
                        final_binding_revision,
                        current,
                    ),
                )

                route_authority = {
                    "tenant_id": tenant_id,
                    "merchant_id": evidence["merchant_id"],
                    "shop_id": evidence["shop_id"],
                    "principal_id": evidence["principal_id"],
                    "connector_id": evidence["connector_id"],
                    "profile_id": evidence["profile_id"],
                    "profile_generation": int(evidence["profile_generation"]),
                    "binding_id": evidence["binding_id"],
                    "binding_generation": int(evidence["binding_generation"]),
                    "platform": PDD_ROUTE.platform_to_public(evidence["platform"]),
                    "adapter_id": PDD_ROUTE.adapter_to_public(evidence["adapter_id"]),
                    "adapter_version": evidence["adapter_version"],
                    "runtime_kind": PDD_ROUTE.require_runtime(
                        evidence["runtime_kind"]
                    ),
                    "shop_revision": final_shop_revision,
                    "principal_revision": final_principal_revision,
                    "binding_revision": final_binding_revision,
                    "binding_lifecycle_state": "ACTIVE",
                    "identity_state": "VERIFIED",
                    "verified_identity_revision": verified_identity_revision,
                    "fresh_route_activation_required": True,
                }
                result = {
                    "confirmation": self._identity_confirmation_result(
                        parsed,
                        result="CONFIRMED",
                        confirmed_at=current,
                        error=None,
                    ),
                    "route_authority": route_authority,
                }
                self._save_receipt(
                    connection,
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                    operation_kind="identity.confirm",
                    account_id=account_id,
                    command_sha256=parsed["command_sha256"],
                    result=result,
                    current=current,
                )
                self._audit(
                    connection,
                    account_id,
                    "pdd_identity_confirmed",
                    True,
                    {
                        "confirmation_id": command["confirmation_id"],
                        "tenant_id": tenant_id,
                        "merchant_id": evidence["merchant_id"],
                        "shop_id": evidence["shop_id"],
                        "principal_id": evidence["principal_id"],
                        "connector_id": evidence["connector_id"],
                        "profile_id": evidence["profile_id"],
                        "profile_generation": int(evidence["profile_generation"]),
                        "binding_id": evidence["binding_id"],
                        "binding_generation": int(evidence["binding_generation"]),
                        "evidence_id": evidence["evidence_id"],
                        "evidence_sha256": evidence["evidence_sha256"],
                        "shop_revision": final_shop_revision,
                        "principal_revision": final_principal_revision,
                        "binding_revision": final_binding_revision,
                        "verified_identity_revision": verified_identity_revision,
                    },
                )
                return OperationOutcome(result)
        except IntegrityError as exc:
            raise OperationsConflict("identity_confirmation_conflict") from exc

    def issue_route_activation(
        self, account_id: str, user_id: str, command: dict[str, Any]
    ) -> OperationOutcome:
        command = _closed_object(
            command,
            required={"tenant_id", "connector_id", "profile_id", "binding_id"},
            field="route_activation_issue",
        )
        tenant_id = _identifier(command["tenant_id"], "tenant_id")
        connector_id = _identifier(command["connector_id"], "connector_id")
        profile_id = _identifier(command["profile_id"], "profile_id")
        binding_id = _identifier(command["binding_id"], "binding_id")
        try:
            if str(uuid.UUID(profile_id)) != profile_id:
                raise ValueError("profile_id_not_canonical_uuid")
        except ValueError as exc:
            raise SecurityError("route_activation_profile_id_invalid") from exc

        current = now_seconds()
        code = b64url_encode(current.to_bytes(8, "big") + secrets.token_bytes(32))
        code_sha256 = token_hash(code)
        activation_id = str(uuid.uuid4())
        expires_at = current + self.settings.route_activation_ttl_seconds
        try:
            with self.database.transaction(immediate=True) as connection:
                self._require_actor(connection, account_id, user_id)
                self._require_tenant_member(connection, account_id, user_id, tenant_id)
                route = connection.execute(
                    """
                    SELECT b.*, c.connector_installation_id, c.registered_key_id,
                           c.registered_public_key_spki_sha256,
                           cp.state AS profile_state, c.state AS connector_state,
                           s.provider_shop_id AS expected_provider_shop_id,
                           s.identity_state AS shop_identity_state,
                           s.revision AS shop_revision,
                           p.provider_principal_id AS expected_provider_principal_id,
                           p.identity_state AS principal_identity_state,
                           p.revision AS principal_revision,
                           b.revision AS binding_revision,
                           b.state AS binding_lifecycle_state,
                           b.identity_state AS binding_identity_state,
                           b.verified_identity_revision AS expected_verified_identity_revision
                    FROM platform_account_bindings b
                    JOIN merchants m
                      ON m.tenant_id = b.tenant_id AND m.id = b.merchant_id
                    JOIN platform_shops s
                      ON s.tenant_id = b.tenant_id AND s.merchant_id = b.merchant_id
                     AND s.platform = b.platform AND s.id = b.shop_id
                    JOIN platform_principals p
                      ON p.tenant_id = b.tenant_id AND p.merchant_id = b.merchant_id
                     AND p.platform = b.platform AND p.id = b.principal_id
                    JOIN connector_profiles cp
                      ON cp.tenant_id = b.tenant_id AND cp.connector_id = b.connector_id
                     AND cp.id = b.profile_id
                     AND cp.profile_generation = b.profile_generation
                    JOIN web_connectors c
                      ON c.tenant_id = b.tenant_id AND c.id = b.connector_id
                    WHERE b.tenant_id = ? AND b.connector_id = ?
                      AND b.profile_id = ? AND b.id = ?
                      AND b.state IN ('PENDING', 'ACTIVE')
                      AND b.identity_state <> 'MISMATCH'
                      AND b.platform = ?
                      AND m.state = 'ACTIVE'
                      AND s.state = 'ACTIVE' AND s.identity_state <> 'MISMATCH'
                      AND p.state = 'ACTIVE' AND p.identity_state <> 'MISMATCH'
                      AND cp.state IN ('CREATED', 'ACTIVE')
                      AND c.state = 'ACTIVE'
                    """,
                    (
                        tenant_id,
                        connector_id,
                        profile_id,
                        binding_id,
                        PDD_ROUTE.storage_platform,
                    ),
                ).fetchone()
                if route is None:
                    raise OperationsNotFound("route_activation_route_not_found")
                self._require_merchant_member(
                    connection, account_id, user_id, tenant_id, route["merchant_id"]
                )
                capabilities = json.loads(route["capabilities_json"])
                if (
                    route["runtime_kind"] != "windows_web"
                    or route["adapter_id"] != PDD_ROUTE.storage_adapter
                    or capabilities != ["message.observe"]
                ):
                    raise OperationsNotFound("route_activation_route_not_found")
                self._expected_platform_identity(route)

                connection.execute(
                    """
                    UPDATE connector_route_activations
                    SET state = 'EXPIRED', revision = revision + 1, updated_at = ?
                    WHERE tenant_id = ? AND connector_id = ? AND profile_id = ?
                      AND state = 'ISSUED' AND expires_at <= ?
                    """,
                    (current, tenant_id, connector_id, profile_id, current),
                )
                connection.execute(
                    """
                    INSERT INTO connector_route_activations(
                        id, code_sha256, account_id, user_id, tenant_id, connector_id,
                        connector_installation_id, registered_key_id,
                        registered_public_key_spki_sha256, profile_id, profile_generation,
                        binding_id, binding_generation, state, issued_at, expires_at,
                        consumed_at, consumed_command_sha256, accepted_json,
                        revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ISSUED', ?, ?,
                              NULL, NULL, NULL, 1, ?, ?)
                    """,
                    (
                        activation_id, code_sha256, account_id, user_id, tenant_id,
                        connector_id, route["connector_installation_id"],
                        route["registered_key_id"],
                        route["registered_public_key_spki_sha256"], profile_id,
                        int(route["profile_generation"]), binding_id,
                        int(route["binding_generation"]), current, expires_at,
                        current, current,
                    ),
                )
                self._audit(
                    connection,
                    account_id,
                    "connector_route_activation_issued",
                    True,
                    {
                        "activation_id": activation_id,
                        "tenant_id": tenant_id,
                        "connector_id": connector_id,
                        "profile_id": profile_id,
                        "profile_generation": int(route["profile_generation"]),
                        "binding_id": binding_id,
                        "binding_generation": int(route["binding_generation"]),
                    },
                )
                return OperationOutcome(
                    {
                        "activation": {
                            "activation_id": activation_id,
                            "activation_code": code,
                            "expires_at": _utc_text(expires_at),
                        }
                    }
                )
        except IntegrityError as exc:
            raise OperationsConflict("route_activation_already_issued") from exc

    def consume_route_activation(self, frame: dict[str, Any]) -> dict[str, Any]:
        frame = _closed_object(
            frame,
            required={
                "contract", "activation_code", "connector_installation_id",
                "issued_at", "nonce", "proof",
            },
            field="route_activation_consume",
        )
        if frame["contract"] != {"name": "oin.route_activation", "version": 2}:
            raise SecurityError("route_activation_contract_invalid")
        installation_id = _identifier(
            frame["connector_installation_id"], "connector_installation_id"
        )
        issued_at = _positive_integer(frame["issued_at"], "issued_at")
        activation_code = frame["activation_code"]
        if not isinstance(activation_code, str):
            raise SecurityError("route_activation_code_invalid")
        try:
            decoded_code = b64url_decode(activation_code, max_bytes=40)
            if len(decoded_code) != 40:
                raise SecurityError("route_activation_code_invalid")
            if int.from_bytes(decoded_code[:8], "big") != issued_at:
                raise SecurityError("route_activation_issued_at_invalid")
            expected_nonce = b64url_encode(
                hashlib.sha256(
                    ROUTE_ACTIVATION_NONCE_DOMAIN + activation_code.encode("ascii")
                ).digest()[:24]
            )
            if frame["nonce"] != expected_nonce:
                raise SecurityError("route_activation_nonce_invalid")
        except SecurityError as exc:
            raise OperationsAuthentication("route_activation_proof_invalid") from exc
        proof = _closed_object(
            frame["proof"],
            required={"algorithm", "key_id", "signed_payload_sha256", "signature"},
            field="route_activation_proof",
        )
        if proof["algorithm"] != "ECDSA_P256_SHA256":
            raise OperationsAuthentication("route_activation_proof_invalid")
        _bounded_text(proof["key_id"], field="key_id", maximum=96)
        if (
            not isinstance(proof["signed_payload_sha256"], str)
            or not HEX_SHA256_PATTERN.fullmatch(proof["signed_payload_sha256"])
        ):
            raise SecurityError("route_activation_signed_payload_invalid")

        unsigned = {key: frame[key] for key in (
            "activation_code", "connector_installation_id", "contract", "issued_at", "nonce"
        )}
        signed_payload = ROUTE_ACTIVATION_DOMAIN + canonical_jcs_bytes(unsigned)
        if hashlib.sha256(signed_payload).hexdigest() != proof["signed_payload_sha256"]:
            raise OperationsAuthentication("route_activation_proof_invalid")
        command_sha256 = canonical_jcs_sha256(unsigned)
        code_sha256 = token_hash(activation_code)
        current = now_seconds()

        with self.database.transaction(immediate=True) as connection:
            activation = connection.execute(
                "SELECT * FROM connector_route_activations WHERE code_sha256 = ?",
                (code_sha256,),
            ).fetchone()
            if activation is None:
                raise OperationsAuthentication("route_activation_invalid")
            connector = connection.execute(
                """
                SELECT * FROM web_connectors
                WHERE tenant_id = ? AND id = ?
                """,
                (activation["tenant_id"], activation["connector_id"]),
            ).fetchone()
            if (
                connector is None
                or connector["connector_installation_id"] != installation_id
                or activation["connector_installation_id"] != installation_id
                or connector["registered_key_id"] != activation["registered_key_id"]
                or connector["registered_public_key_spki_sha256"]
                != activation["registered_public_key_spki_sha256"]
                or proof["key_id"] != connector["registered_key_id"]
                or not verify_p256_p1363_signature(
                    bytes(connector["registered_public_key_spki"]),
                    signed_payload,
                    proof["signature"],
                )
            ):
                raise OperationsAuthentication("route_activation_proof_invalid")

            if activation["state"] == "CONSUMED":
                if activation["consumed_command_sha256"] != command_sha256:
                    raise OperationsConflict("route_activation_already_consumed")
                return json.loads(activation["accepted_json"])
            if activation["state"] != "ISSUED" or int(activation["expires_at"]) <= current:
                raise OperationsAuthentication("route_activation_expired")
            if int(activation["issued_at"]) != issued_at:
                raise OperationsAuthentication("route_activation_request_stale")
            if issued_at > current + 30 or issued_at < current - 300:
                raise OperationsAuthentication("route_activation_request_stale")

            route = connection.execute(
                """
                SELECT b.*, cp.state AS profile_state, c.state AS connector_state,
                       s.provider_shop_id AS expected_provider_shop_id,
                       s.identity_state AS shop_identity_state,
                       s.revision AS shop_revision,
                       p.provider_principal_id AS expected_provider_principal_id,
                       p.identity_state AS principal_identity_state,
                       p.revision AS principal_revision,
                       b.revision AS binding_revision,
                       b.state AS binding_lifecycle_state,
                       b.identity_state AS binding_identity_state,
                       b.verified_identity_revision AS expected_verified_identity_revision
                FROM platform_account_bindings b
                JOIN merchants m
                  ON m.tenant_id = b.tenant_id AND m.id = b.merchant_id
                JOIN platform_shops s
                  ON s.tenant_id = b.tenant_id AND s.merchant_id = b.merchant_id
                 AND s.platform = b.platform AND s.id = b.shop_id
                JOIN platform_principals p
                  ON p.tenant_id = b.tenant_id AND p.merchant_id = b.merchant_id
                 AND p.platform = b.platform AND p.id = b.principal_id
                JOIN connector_profiles cp
                  ON cp.tenant_id = b.tenant_id AND cp.connector_id = b.connector_id
                 AND cp.id = b.profile_id
                 AND cp.profile_generation = b.profile_generation
                JOIN web_connectors c
                  ON c.tenant_id = b.tenant_id AND c.id = b.connector_id
                WHERE b.tenant_id = ? AND b.connector_id = ?
                  AND b.profile_id = ? AND b.profile_generation = ?
                  AND b.id = ? AND b.binding_generation = ?
                  AND b.state IN ('PENDING', 'ACTIVE')
                  AND b.identity_state <> 'MISMATCH'
                  AND b.platform = ?
                  AND m.state = 'ACTIVE'
                  AND s.state = 'ACTIVE' AND s.identity_state <> 'MISMATCH'
                  AND p.state = 'ACTIVE' AND p.identity_state <> 'MISMATCH'
                  AND cp.state IN ('CREATED', 'ACTIVE') AND c.state = 'ACTIVE'
                """,
                (
                    activation["tenant_id"], activation["connector_id"],
                    activation["profile_id"], int(activation["profile_generation"]),
                    activation["binding_id"], int(activation["binding_generation"]),
                    PDD_ROUTE.storage_platform,
                ),
            ).fetchone()
            if route is None:
                raise OperationsConflict("route_activation_route_stale")
            capabilities = json.loads(route["capabilities_json"])
            if (
                route["runtime_kind"] != "windows_web"
                or route["adapter_id"] != PDD_ROUTE.storage_adapter
                or capabilities != ["message.observe"]
            ):
                raise OperationsConflict("route_activation_route_stale")

            expected_platform_identity = self._expected_platform_identity(route)

            accepted = {
                "contract": {"name": "oin.route_activation", "version": 2},
                "activation_id": activation["id"],
                "connector_installation_id": installation_id,
                "tenant_id": activation["tenant_id"],
                "connector_id": activation["connector_id"],
                "profile_id": activation["profile_id"],
                "profile_generation": int(activation["profile_generation"]),
                "binding_id": activation["binding_id"],
                "binding_generation": int(activation["binding_generation"]),
                "target_url": PDD_CHAT_TARGET_URL,
                "capabilities": ["message.observe"],
                "expected_platform_identity": expected_platform_identity,
                "consumed_at": _utc_text(current),
            }
            changed = connection.execute(
                """
                UPDATE connector_route_activations
                SET state = 'CONSUMED', consumed_at = ?, consumed_command_sha256 = ?,
                    accepted_json = ?, revision = revision + 1, updated_at = ?
                WHERE id = ? AND state = 'ISSUED' AND expires_at > ?
                """,
                (
                    current, command_sha256, canonical_json(accepted), current,
                    activation["id"], current,
                ),
            )
            if changed.rowcount != 1:
                raise OperationsConflict("route_activation_consume_raced")
            self._audit(
                connection,
                activation["account_id"],
                "connector_route_activation_consumed",
                True,
                {
                    "activation_id": activation["id"],
                    "tenant_id": activation["tenant_id"],
                    "connector_id": activation["connector_id"],
                    "profile_id": activation["profile_id"],
                    "profile_generation": int(activation["profile_generation"]),
                    "binding_id": activation["binding_id"],
                    "binding_generation": int(activation["binding_generation"]),
                },
            )
            return accepted

    def get_shop(
        self, account_id: str, user_id: str, tenant_id: str, merchant_id: str, shop_id: str
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            self._require_actor(connection, account_id, user_id)
            self._require_merchant_member(connection, account_id, user_id, tenant_id, merchant_id)
            row = connection.execute(
                "SELECT * FROM platform_shops WHERE tenant_id = ? AND merchant_id = ? AND id = ?",
                (tenant_id, merchant_id, shop_id),
            ).fetchone()
            if row is None:
                raise OperationsNotFound("shop_route_not_found")
            return {"shop": self._shop(row)}

    def get_principal(
        self, account_id: str, user_id: str, tenant_id: str, merchant_id: str, principal_id: str
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            self._require_actor(connection, account_id, user_id)
            self._require_merchant_member(connection, account_id, user_id, tenant_id, merchant_id)
            row = connection.execute(
                "SELECT * FROM platform_principals WHERE tenant_id = ? AND merchant_id = ? AND id = ?",
                (tenant_id, merchant_id, principal_id),
            ).fetchone()
            if row is None:
                raise OperationsNotFound("principal_route_not_found")
            shops = connection.execute(
                "SELECT shop_id FROM platform_principal_shops WHERE tenant_id = ? AND principal_id = ? ORDER BY shop_id",
                (tenant_id, principal_id),
            ).fetchall()
            return {"principal": self._principal(row, [item["shop_id"] for item in shops])}

    def list_routes(self, account_id: str, user_id: str, tenant_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            self._require_actor(connection, account_id, user_id)
            self._require_tenant_member(connection, account_id, user_id, tenant_id)
            tenant = connection.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
            if tenant is None:
                raise OperationsNotFound("tenant_route_not_found")
            memberships = connection.execute(
                """
                SELECT * FROM memberships
                WHERE tenant_id = ? AND account_id = ? AND user_id = ? AND state = 'ACTIVE'
                ORDER BY scope_kind, merchant_id, id
                """,
                (tenant_id, account_id, user_id),
            ).fetchall()
            merchant_ids = [row["merchant_id"] for row in memberships if row["merchant_id"] is not None]
            if merchant_ids:
                placeholders = ",".join("?" for _ in merchant_ids)
                merchants = connection.execute(
                    f"SELECT * FROM merchants WHERE tenant_id = ? AND id IN ({placeholders}) ORDER BY id",
                    (tenant_id, *merchant_ids),
                ).fetchall()
                shops = connection.execute(
                    f"SELECT * FROM platform_shops WHERE tenant_id = ? AND merchant_id IN ({placeholders}) ORDER BY merchant_id, id",
                    (tenant_id, *merchant_ids),
                ).fetchall()
                principals = connection.execute(
                    f"SELECT * FROM platform_principals WHERE tenant_id = ? AND merchant_id IN ({placeholders}) ORDER BY merchant_id, id",
                    (tenant_id, *merchant_ids),
                ).fetchall()
                bindings = connection.execute(
                    f"SELECT * FROM platform_account_bindings WHERE tenant_id = ? AND merchant_id IN ({placeholders}) ORDER BY merchant_id, id, binding_generation",
                    (tenant_id, *merchant_ids),
                ).fetchall()
            else:
                merchants = shops = principals = bindings = []
            principal_shop_rows = connection.execute(
                "SELECT principal_id, shop_id FROM platform_principal_shops WHERE tenant_id = ? ORDER BY principal_id, shop_id",
                (tenant_id,),
            ).fetchall()
            shops_by_principal: dict[str, list[str]] = {}
            for row in principal_shop_rows:
                shops_by_principal.setdefault(row["principal_id"], []).append(row["shop_id"])
            connectors = connection.execute(
                "SELECT * FROM web_connectors WHERE tenant_id = ? ORDER BY id",
                (tenant_id,),
            ).fetchall()
            profiles = connection.execute(
                "SELECT * FROM connector_profiles WHERE tenant_id = ? ORDER BY connector_id, id, profile_generation",
                (tenant_id,),
            ).fetchall()
            return {
                "tenant": self._tenant(tenant),
                "memberships": [self._membership(row) for row in memberships],
                "merchants": [self._merchant(row) for row in merchants],
                "shops": [self._shop(row) for row in shops],
                "principals": [self._principal(row, shops_by_principal.get(row["id"], [])) for row in principals],
                "connectors": [self._connector(row) for row in connectors],
                "profiles": [self._profile(row) for row in profiles],
                "bindings": [self._binding(row) for row in bindings],
            }

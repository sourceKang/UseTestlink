from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


CONTRACT_SCHEMA_VERSION = "1.0"
VALID_ENVIRONMENTS = {"corp", "sandbox"}
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
_FORBIDDEN_KEY_FRAGMENTS = {
    "apikey",
    "authorization",
    "devkey",
    "password",
    "secret",
    "token",
}


class ContractError(ValueError):
    """Raised when an MCP handoff violates the shared contract."""


def canonical_json(value: Any) -> str:
    """Return stable UTF-8 JSON text used to bind preview and write payloads."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ContractError(f"Contract payload is not canonical JSON: {exc}") from exc


def payload_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _normalized_key(value: Any) -> str:
    return str(value).casefold().replace("_", "").replace("-", "")


def assert_safe_contract(value: Any, *, path: str = "$") -> None:
    """Reject secret-bearing field names before a payload crosses an MCP boundary."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = _normalized_key(key)
            if any(fragment in normalized for fragment in _FORBIDDEN_KEY_FRAGMENTS):
                raise ContractError(f"Secret-bearing contract field is not allowed: {path}.{key}")
            assert_safe_contract(child, path=f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            assert_safe_contract(child, path=f"{path}[{index}]")


def _validate_identity(field: str, value: Any) -> str:
    text = str(value or "").strip()
    if not _IDENTITY_RE.fullmatch(text):
        raise ContractError(f"{field} must be 8-128 safe identity characters.")
    return text


def validate_operation_context(value: Mapping[str, Any]) -> dict[str, Any]:
    assert_safe_contract(value)
    if value.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        raise ContractError(
            f"Unsupported schema_version: {value.get('schema_version')!r}; "
            f"expected {CONTRACT_SCHEMA_VERSION!r}."
        )
    operation_id = _validate_identity("operation_id", value.get("operation_id"))
    correlation_id = _validate_identity("correlation_id", value.get("correlation_id"))
    environment = str(value.get("environment") or "").strip().casefold()
    if environment not in VALID_ENVIRONMENTS:
        raise ContractError(f"Unsupported environment: {environment or '<missing>'}.")
    return {
        **dict(value),
        "operation_id": operation_id,
        "correlation_id": correlation_id,
        "environment": environment,
    }


def validate_preview_digest(planned_payload: Any, preview_digest: str) -> str:
    """Require a write payload to match the digest returned by its reviewed preview."""

    assert_safe_contract(planned_payload)
    supplied = str(preview_digest or "").strip().casefold()
    if not _DIGEST_RE.fullmatch(supplied):
        raise ContractError("preview_digest must be a 64-character lowercase SHA-256 digest.")
    expected = payload_digest(planned_payload)
    if not hmac.compare_digest(expected, supplied):
        raise ContractError("Write payload does not match the confirmed preview_digest.")
    return supplied

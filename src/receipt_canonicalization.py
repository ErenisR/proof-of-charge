"""Versioned canonical receipt serialization profiles."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

CANONICALIZATION_PROFILE_V1 = "poc-c14n-v1"
HASH_ALGORITHM = "sha-256"

TOP_LEVEL_FIELDS = frozenset({
    "version", "schema_version", "canonicalization_profile", "hash_algorithm",
    "session_type", "session_id", "user_id", "evse_id", "ocpp_tx_id",
    "start_ts", "end_ts", "energy_kwh", "energy_summary", "pricing",
    "settlement", "merkle_root", "stream_hash_alg",
})
ENERGY_SUMMARY_FIELDS = frozenset({"import_kwh", "export_kwh", "net_kwh"})
PRICING_FIELDS = frozenset({"currency", "model", "components", "import_components", "export_components"})
PRICING_COMPONENT_FIELDS = frozenset({"from", "to", "price_per_kwh"})
SETTLEMENT_FIELDS = frozenset({"gross_import_cost", "gross_export_credit", "net_amount", "currency"})

ENERGY_PATHS = {
    ("energy_kwh",),
    ("energy_summary", "import_kwh"),
    ("energy_summary", "export_kwh"),
    ("energy_summary", "net_kwh"),
}
MONEY_PATHS = {
    ("settlement", "gross_import_cost"),
    ("settlement", "gross_export_credit"),
    ("settlement", "net_amount"),
}
TIMESTAMP_NAMES = {"start_ts", "end_ts", "from", "to"}


class CanonicalizationError(ValueError):
    pass


def canonicalize_receipt(receipt: Mapping[str, Any], profile: str) -> bytes:
    """Return the exact UTF-8 hash input for a supported profile."""
    if profile != CANONICALIZATION_PROFILE_V1:
        raise CanonicalizationError(f"Unknown canonicalization profile: {profile}")
    if not isinstance(receipt, Mapping):
        raise CanonicalizationError("Receipt must be an object")
    _validate_profile_shape(receipt)
    if receipt.get("canonicalization_profile") != profile:
        raise CanonicalizationError("Receipt canonicalization_profile does not match requested profile")
    if receipt.get("hash_algorithm") != HASH_ALGORITHM:
        raise CanonicalizationError(f"hash_algorithm must be {HASH_ALGORITHM}")
    normalized = _normalize(dict(receipt), ())
    text = json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CanonicalizationError("Strings must not contain lone Unicode surrogates") from exc


def hash_canonical_receipt(receipt: Mapping[str, Any], profile: str) -> str:
    return "0x" + hashlib.sha256(canonicalize_receipt(receipt, profile)).hexdigest()


def parse_json_strict(payload: str | bytes) -> Any:
    """Parse JSON while rejecting duplicate object keys and non-finite constants."""
    if isinstance(payload, bytes):
        if payload.startswith(b"\xef\xbb\xbf"):
            raise CanonicalizationError("UTF-8 BOM is not allowed")
        payload = payload.decode("utf-8")

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CanonicalizationError(f"Duplicate JSON object key: {key}")
            result[key] = value
        return result

    def reject_constant(value):
        raise CanonicalizationError(f"Non-finite JSON number is not allowed: {value}")

    return json.loads(payload, object_pairs_hook=object_pairs, parse_constant=reject_constant)


def _validate_profile_shape(receipt: Mapping[str, Any]) -> None:
    _exact_fields(receipt, TOP_LEVEL_FIELDS, "receipt")
    _object_with_fields(receipt["energy_summary"], ENERGY_SUMMARY_FIELDS, "energy_summary")
    pricing = _object_with_fields(receipt["pricing"], PRICING_FIELDS, "pricing")
    for list_name in ("components", "import_components", "export_components"):
        components = pricing[list_name]
        if not isinstance(components, list):
            raise CanonicalizationError(f"pricing.{list_name} must be an array")
        for index, component in enumerate(components):
            _object_with_fields(component, PRICING_COMPONENT_FIELDS, f"pricing.{list_name}[{index}]")
    _object_with_fields(receipt["settlement"], SETTLEMENT_FIELDS, "settlement")


def _object_with_fields(value: Any, fields: frozenset[str], path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CanonicalizationError(f"{path} must be an object")
    _exact_fields(value, fields, path)
    return value


def _exact_fields(value: Mapping[str, Any], fields: frozenset[str], path: str) -> None:
    actual = set(value)
    missing = sorted(fields - actual)
    extra = sorted(actual - fields)
    if missing:
        raise CanonicalizationError(f"{path} missing required fields: {', '.join(missing)}")
    if extra:
        raise CanonicalizationError(f"{path} contains fields not defined by {CANONICALIZATION_PROFILE_V1}: {', '.join(extra)}")
    nulls = sorted(key for key, item in value.items() if item is None)
    if nulls:
        raise CanonicalizationError(f"{path} fields are not nullable: {', '.join(nulls)}")


def _normalize(value: Any, path: tuple[str, ...]) -> Any:
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"Object key must be a string at {_path(path)}")
            normalized_key = _normalize_string(key)
            if normalized_key in result:
                raise CanonicalizationError(f"Unicode-normalized duplicate key: {normalized_key}")
            result[normalized_key] = _normalize(item, path + (key,))
        return result
    if isinstance(value, list):
        return [_normalize(item, path + ("[]",)) for item in value]
    if value is None:
        raise CanonicalizationError(f"Null is not allowed at {_path(path)}")
    if path in ENERGY_PATHS or path in MONEY_PATHS:
        return _fixed_decimal(value, 3, path)
    if len(path) >= 3 and path[-1] == "price_per_kwh" and path[-2] == "[]":
        return _fixed_decimal(value, 3, path)
    if path and path[-1] in TIMESTAMP_NAMES:
        return _timestamp(value, path)
    if isinstance(value, str):
        return _normalize_string(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        if isinstance(value, float) and not math.isfinite(value):
            raise CanonicalizationError(f"Non-finite number at {_path(path)}")
        raise CanonicalizationError(f"Unscaled numeric field is not allowed at {_path(path)}")
    raise CanonicalizationError(f"Unsupported value type at {_path(path)}: {type(value).__name__}")


def _fixed_decimal(value: Any, scale: int, path: tuple[str, ...]) -> str:
    if not isinstance(value, str):
        raise CanonicalizationError(f"Canonical decimal must be a string at {_path(path)}")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", value):
        raise CanonicalizationError(f"Invalid decimal at {_path(path)}: {value}")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CanonicalizationError(f"Invalid decimal at {_path(path)}: {value}") from exc
    if not decimal_value.is_finite():
        raise CanonicalizationError(f"Non-finite number at {_path(path)}")
    quantum = Decimal(1).scaleb(-scale)
    rounded = decimal_value.quantize(quantum, rounding=ROUND_HALF_UP)
    if rounded == 0:
        rounded = abs(rounded)
    return format(rounded, f".{scale}f")


def _timestamp(value: Any, path: tuple[str, ...]) -> str:
    if not isinstance(value, str):
        raise CanonicalizationError(f"Timestamp must be a string at {_path(path)}")
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|z|[+-]\d{2}:\d{2})",
        value,
    ):
        raise CanonicalizationError(f"Invalid RFC 3339 timestamp at {_path(path)}: {value}")
    raw = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise CanonicalizationError(f"Invalid RFC 3339 timestamp at {_path(path)}: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CanonicalizationError(f"Naive timestamp is not allowed at {_path(path)}")
    utc = parsed.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _normalize_string(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    if any(0xD800 <= ord(char) <= 0xDFFF for char in normalized):
        raise CanonicalizationError("Strings must not contain lone Unicode surrogates")
    return normalized


def _path(path: tuple[str, ...]) -> str:
    return ".".join(path).replace(".[]", "[]") or "receipt"

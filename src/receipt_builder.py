# src/receipt_builder.py
import json
import hashlib
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, List
from .merkle import merkle_root
from .receipt_schema import (
    DEFAULT_SCHEMA_VERSION,
    RECEIPT_FORMAT_VERSION,
    SESSION_TYPES,
    validate_receipt_model,
)
from .receipt_canonicalization import (
    CANONICALIZATION_PROFILE_V1,
    HASH_ALGORITHM,
    hash_canonical_receipt,
)
from .performance_timing import TimingRecorder
from contextlib import nullcontext


def _to_float(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except Exception as exc:
        raise ValueError(f"Invalid {field_name}: {value}") from exc


def _parse_ts(ts: str) -> datetime:
    if ts.endswith("Z"):
        ts = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(ts)


def _coerce_meter_value(mv: Dict[str, Any]) -> Dict[str, Any]:
    has_import = "import_kwh" in mv
    has_export = "export_kwh" in mv
    has_energy = "energy_kwh" in mv

    if not (has_import or has_export or has_energy):
        raise ValueError("Each meter value must include import_kwh/export_kwh or energy_kwh")

    if has_import or has_export:
        import_kwh = _to_float(mv.get("import_kwh", 0.0), "import_kwh") if has_import else 0.0
        export_kwh = _to_float(mv.get("export_kwh", 0.0), "export_kwh") if has_export else 0.0
        # Derive net energy from directional counters for deterministic behavior.
        energy_kwh = import_kwh - export_kwh
    else:
        energy_kwh = _to_float(mv["energy_kwh"], "energy_kwh")
        import_kwh = energy_kwh
        export_kwh = 0.0

    return {
        "ts": mv["ts"],
        "import_kwh": round(import_kwh, 3),
        "export_kwh": round(export_kwh, 3),
        "energy_kwh": round(energy_kwh, 3),
    }


def _normalize_meter_values(mvs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Sort by timestamp and validate monotone cumulative counters.
    """
    mvs_sorted = [_coerce_meter_value(mv) for mv in sorted(mvs, key=lambda mv: mv["ts"])]
    if len(mvs_sorted) < 2:
        return mvs_sorted

    for i in range(len(mvs_sorted) - 1):
        if mvs_sorted[i]["import_kwh"] > mvs_sorted[i + 1]["import_kwh"] + 1e-9:
            raise ValueError("Non-monotone import_kwh – possible data error")
        if mvs_sorted[i]["export_kwh"] > mvs_sorted[i + 1]["export_kwh"] + 1e-9:
            raise ValueError("Non-monotone export_kwh – possible data error")

    return mvs_sorted


def meter_leaf_bytes(meter_value: Dict[str, Any]) -> bytes:
    """Canonical meter leaf encoding shared by receipt construction and DB audit."""
    mv = _coerce_meter_value(meter_value)
    return f'{mv["ts"]}|{mv["import_kwh"]}|{mv["export_kwh"]}'.encode("utf-8")


def _pricing_components(pricing: Dict[str, Any]) -> List[Dict[str, Any]]:
    return (
        pricing.get("import_components")
        or pricing.get("components")
        or []
    )


def _export_components(pricing: Dict[str, Any]) -> List[Dict[str, Any]]:
    return (
        pricing.get("export_components")
        or pricing.get("components")
        or []
    )


def _validate_pricing_components(components: List[Dict[str, Any]], field_name: str) -> None:
    for index, component in enumerate(components):
        prefix = f"{field_name}[{index}]"
        if "from" not in component:
            raise ValueError(f"{prefix}.from is required")
        if "to" not in component:
            raise ValueError(f"{prefix}.to is required")
        try:
            _parse_ts(component["from"])
        except Exception as exc:
            raise ValueError(f"Invalid {prefix}.from: {component['from']}") from exc
        try:
            _parse_ts(component["to"])
        except Exception as exc:
            raise ValueError(f"Invalid {prefix}.to: {component['to']}") from exc
        _to_float(component.get("price_per_kwh"), f"{prefix}.price_per_kwh")


def _sum_weighted_cost(
    start_ts: str,
    end_ts: str,
    energy_kwh: float,
    components: List[Dict[str, Any]],
    field_name: str,
) -> float:
    if not components:
        return 0.0

    _validate_pricing_components(components, field_name)
    if energy_kwh == 0:
        return 0.0

    session_start = _parse_ts(start_ts)
    session_end = _parse_ts(end_ts)
    session_seconds = (session_end - session_start).total_seconds()
    if session_seconds <= 0:
        return 0.0

    total = 0.0
    for c in components:
        c_start = _parse_ts(c["from"])
        c_end = _parse_ts(c["to"])
        overlap_start = max(session_start, c_start)
        overlap_end = min(session_end, c_end)
        overlap_seconds = (overlap_end - overlap_start).total_seconds()
        if overlap_seconds <= 0:
            continue
        share = overlap_seconds / session_seconds
        total += energy_kwh * share * _to_float(c["price_per_kwh"], "price_per_kwh")
    return round(total, 3)


def _derive_session_type(session_type: str | None, import_kwh: float, export_kwh: float) -> str:
    if session_type in SESSION_TYPES:
        return session_type
    if import_kwh > 0 and export_kwh > 0:
        return "bidirectional"
    if export_kwh > 0 and import_kwh <= 0:
        return "discharge_only"
    return "charge_only"


def _profile_decimal(value: Any) -> str:
    rounded = Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    if rounded == 0:
        rounded = abs(rounded)
    return format(rounded, ".3f")


def _profile_components(components: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "from": component["from"],
            "to": component["to"],
            "price_per_kwh": _profile_decimal(component["price_per_kwh"]),
        }
        for component in components
    ]


def build_receipt(
    session: Dict[str, Any],
    canonicalization_profile: str | None = CANONICALIZATION_PROFILE_V1,
    timing_recorder: TimingRecorder | None = None,
    timing_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Build a minimal receipt from session dict.
    """
    timing_context = timing_context or {}
    session_id = timing_context.get("session_id", session.get("session_id"))
    measure = lambda stage: timing_recorder.measure(stage, session_id=session_id) if timing_recorder else nullcontext()
    with measure("receipt_construction_total"):
        return _build_receipt_impl(session, canonicalization_profile, timing_recorder, session_id)


def _build_receipt_impl(session: Dict[str, Any], canonicalization_profile: str | None,
                        timing_recorder: TimingRecorder | None, session_id: str | None) -> Dict[str, Any]:
    measure = lambda stage: timing_recorder.measure(stage, session_id=session_id) if timing_recorder else nullcontext()
    with measure("meter_normalization"):
        mvs = _normalize_meter_values(session["meter_values"])
        if len(mvs) < 2:
            raise ValueError("Need at least 2 meter values")

    # Build Merkle tree leaves as H(ts|import_kwh|export_kwh)
    with measure("meter_leaf_encoding"):
        leaves = [meter_leaf_bytes(mv) for mv in mvs]
    with measure("meter_merkle_construction"):
        root = merkle_root(leaves)
    with measure("receipt_energy_pricing_assembly"):
        first = mvs[0]
        last = mvs[-1]
        import_kwh = round(last["import_kwh"] - first["import_kwh"], 3)
        export_kwh = round(last["export_kwh"] - first["export_kwh"], 3)
        net_kwh = round(import_kwh - export_kwh, 3)
        pricing = session.get("pricing", {})
        import_components = _pricing_components(pricing)
        export_components = _export_components(pricing)
        import_price = _sum_weighted_cost(session["start_ts"], session["end_ts"], import_kwh,
                                          import_components, "pricing.import_components")
        export_price = _sum_weighted_cost(session["start_ts"], session["end_ts"], export_kwh,
                                          export_components, "pricing.export_components")
        gross_import_cost = round(import_price, 3)
        gross_export_credit = round(export_price, 3)
        net_amount = round(gross_import_cost - gross_export_credit, 3)
        profiled = canonicalization_profile is not None
        receipt = {
            "version": RECEIPT_FORMAT_VERSION,
            "schema_version": session.get("schema_version", DEFAULT_SCHEMA_VERSION),
            "session_type": _derive_session_type(session.get("session_type"), import_kwh, export_kwh),
            "session_id": session["session_id"], "user_id": session["user_id"],
            "evse_id": session["evse_id"], "ocpp_tx_id": session["ocpp_tx_id"],
            "start_ts": session["start_ts"], "end_ts": session["end_ts"],
            "energy_kwh": _profile_decimal(net_kwh) if profiled else net_kwh,
            "energy_summary": {
                "import_kwh": _profile_decimal(import_kwh) if profiled else import_kwh,
                "export_kwh": _profile_decimal(export_kwh) if profiled else export_kwh,
                "net_kwh": _profile_decimal(net_kwh) if profiled else net_kwh,
            },
            "pricing": {
                "currency": pricing.get("currency", "EUR"), "model": pricing.get("model", "TOU"),
                "components": _profile_components(pricing.get("components", [])) if profiled else pricing.get("components", []),
                "import_components": _profile_components(import_components) if profiled else import_components,
                "export_components": _profile_components(export_components) if profiled else export_components,
            },
            "settlement": {
                "gross_import_cost": _profile_decimal(gross_import_cost) if profiled else gross_import_cost,
                "gross_export_credit": _profile_decimal(gross_export_credit) if profiled else gross_export_credit,
                "net_amount": _profile_decimal(net_amount) if profiled else net_amount,
                "currency": pricing.get("currency", "EUR"),
            },
            "merkle_root": "0x" + root.hex(), "stream_hash_alg": "sha256",
        }
        if canonicalization_profile is not None:
            if canonicalization_profile != CANONICALIZATION_PROFILE_V1:
                raise ValueError(f"Unknown canonicalization profile: {canonicalization_profile}")
            receipt["canonicalization_profile"] = canonicalization_profile
            receipt["hash_algorithm"] = HASH_ALGORITHM
    with measure("receipt_schema_validation"):
        validate_receipt_model(receipt)
    return receipt


def hash_receipt(receipt: Dict[str, Any]) -> str:
    """
    Deterministic hash of the receipt JSON.
    This is what you'll pin to IPFS and anchor later.
    """
    profile = receipt.get("canonicalization_profile")
    if profile is not None:
        return hash_canonical_receipt(receipt, str(profile))
    # Historical receipts predate explicit profiles. Preserve their exact byte
    # and hash behavior; never reinterpret them as poc-c14n-v1.
    payload = json.dumps(receipt, sort_keys=True).encode("utf-8")
    h = hashlib.sha256(payload).hexdigest()
    return "0x" + h

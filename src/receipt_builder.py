# src/receipt_builder.py
import json
import hashlib
from datetime import datetime
from typing import Dict, Any, List
from .merkle import merkle_root


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


def _sum_weighted_cost(start_ts: str, end_ts: str, energy_kwh: float, components: List[Dict[str, Any]]) -> float:
    if not components or energy_kwh == 0:
        return 0.0

    session_start = _parse_ts(start_ts)
    session_end = _parse_ts(end_ts)
    session_seconds = (session_end - session_start).total_seconds()
    if session_seconds <= 0:
        return 0.0

    total = 0.0
    for c in components:
        c_from = c.get("from")
        c_to = c.get("to")
        price = c.get("price_per_kwh", 0.0)
        if not c_from or not c_to:
            continue
        try:
            c_start = _parse_ts(c_from)
            c_end = _parse_ts(c_to)
            overlap_start = max(session_start, c_start)
            overlap_end = min(session_end, c_end)
            overlap_seconds = (overlap_end - overlap_start).total_seconds()
            if overlap_seconds <= 0:
                continue
            share = overlap_seconds / session_seconds
            total += energy_kwh * share * _to_float(price, "price_per_kwh")
        except Exception:
            continue
    return round(total, 3)


def _derive_session_type(session_type: str | None, import_kwh: float, export_kwh: float) -> str:
    if session_type in {"charge_only", "discharge_only", "bidirectional"}:
        return session_type
    if import_kwh > 0 and export_kwh > 0:
        return "bidirectional"
    if export_kwh > 0 and import_kwh <= 0:
        return "discharge_only"
    return "charge_only"


def build_receipt(session: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a minimal receipt from session dict.
    """
    mvs = _normalize_meter_values(session["meter_values"])
    if len(mvs) < 2:
        raise ValueError("Need at least 2 meter values")

    # Build Merkle tree leaves as H(ts|import_kwh|export_kwh)
    leaves = []
    for mv in mvs:
        leaf_str = f'{mv["ts"]}|{mv["import_kwh"]}|{mv["export_kwh"]}'
        leaves.append(leaf_str.encode("utf-8"))

    root = merkle_root(leaves)
    first = mvs[0]
    last = mvs[-1]

    import_kwh = round(last["import_kwh"] - first["import_kwh"], 3)
    export_kwh = round(last["export_kwh"] - first["export_kwh"], 3)
    net_kwh = round(import_kwh - export_kwh, 3)

    pricing = session.get("pricing", {})
    import_components = _pricing_components(pricing)
    export_components = _export_components(pricing)
    import_price = _sum_weighted_cost(session["start_ts"], session["end_ts"], import_kwh, import_components)
    export_price = _sum_weighted_cost(session["start_ts"], session["end_ts"], export_kwh, export_components)

    gross_import_cost = round(import_price, 3)
    gross_export_credit = round(export_price, 3)
    net_amount = round(gross_import_cost - gross_export_credit, 3)

    receipt = {
        "version": "1.0",
        "schema_version": session.get("schema_version", "v1"),
        "session_type": _derive_session_type(session.get("session_type"), import_kwh, export_kwh),
        "session_id": session["session_id"],
        "user_id": session["user_id"],
        "evse_id": session["evse_id"],
        "ocpp_tx_id": session["ocpp_tx_id"],
        "start_ts": session["start_ts"],
        "end_ts": session["end_ts"],
        "energy_kwh": net_kwh,
        "energy_summary": {
            "import_kwh": import_kwh,
            "export_kwh": export_kwh,
            "net_kwh": net_kwh,
        },
        "pricing": {
            "currency": (pricing or {}).get("currency", "EUR"),
            "model": (pricing or {}).get("model", "TOU"),
            "components": (pricing or {}).get("components", []),
            "import_components": import_components,
            "export_components": export_components,
        },
        "settlement": {
            "gross_import_cost": gross_import_cost,
            "gross_export_credit": gross_export_credit,
            "net_amount": net_amount,
            "currency": (pricing or {}).get("currency", "EUR"),
        },
        "merkle_root": "0x" + root.hex(),
        "stream_hash_alg": "sha256",
    }
    return receipt


def hash_receipt(receipt: Dict[str, Any]) -> str:
    """
    Deterministic hash of the receipt JSON.
    This is what you'll pin to IPFS and anchor later.
    """
    payload = json.dumps(receipt, sort_keys=True).encode("utf-8")
    h = hashlib.sha256(payload).hexdigest()
    return "0x" + h

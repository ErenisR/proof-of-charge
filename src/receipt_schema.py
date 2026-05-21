from typing import Any, Dict

RECEIPT_FORMAT_VERSION = "1.0"
DEFAULT_SCHEMA_VERSION = "v2g-v1"

DEFAULT_SESSION_TYPE = "charge_only"
SESSION_TYPE_ORDER = ("charge_only", "discharge_only", "bidirectional")
SESSION_TYPES = set(SESSION_TYPE_ORDER)

REQUIRED_RECEIPT_FIELDS = {
    "version",
    "schema_version",
    "session_type",
    "session_id",
    "user_id",
    "evse_id",
    "ocpp_tx_id",
    "start_ts",
    "end_ts",
    "energy_kwh",
    "energy_summary",
    "pricing",
    "settlement",
    "merkle_root",
    "stream_hash_alg",
}

REQUIRED_ENERGY_SUMMARY_FIELDS = {"import_kwh", "export_kwh", "net_kwh"}
REQUIRED_PRICING_FIELDS = {"currency", "model", "components", "import_components", "export_components"}
REQUIRED_SETTLEMENT_FIELDS = {
    "gross_import_cost",
    "gross_export_credit",
    "net_amount",
    "currency",
}


def validate_receipt_model(receipt: Dict[str, Any]) -> None:
    missing = sorted(REQUIRED_RECEIPT_FIELDS - set(receipt))
    if missing:
        raise ValueError(f"Receipt missing required fields: {', '.join(missing)}")

    if receipt["version"] != RECEIPT_FORMAT_VERSION:
        raise ValueError(f"Unsupported receipt version: {receipt['version']}")

    if receipt["schema_version"] != DEFAULT_SCHEMA_VERSION:
        raise ValueError(f"Unsupported schema_version: {receipt['schema_version']}")

    if receipt["session_type"] not in SESSION_TYPES:
        raise ValueError(f"Unsupported session_type: {receipt['session_type']}")

    _require_nested_fields(receipt, "energy_summary", REQUIRED_ENERGY_SUMMARY_FIELDS)
    _require_nested_fields(receipt, "pricing", REQUIRED_PRICING_FIELDS)
    _require_nested_fields(receipt, "settlement", REQUIRED_SETTLEMENT_FIELDS)

    summary = receipt["energy_summary"]
    import_kwh = _to_float(summary["import_kwh"], "energy_summary.import_kwh")
    export_kwh = _to_float(summary["export_kwh"], "energy_summary.export_kwh")
    net_kwh = _to_float(summary["net_kwh"], "energy_summary.net_kwh")
    energy_kwh = _to_float(receipt["energy_kwh"], "energy_kwh")

    if round(import_kwh - export_kwh, 3) != round(net_kwh, 3):
        raise ValueError("energy_summary.net_kwh must equal import_kwh - export_kwh")
    if round(energy_kwh, 3) != round(net_kwh, 3):
        raise ValueError("energy_kwh must equal energy_summary.net_kwh")

    settlement = receipt["settlement"]
    gross_import_cost = _to_float(settlement["gross_import_cost"], "settlement.gross_import_cost")
    gross_export_credit = _to_float(settlement["gross_export_credit"], "settlement.gross_export_credit")
    net_amount = _to_float(settlement["net_amount"], "settlement.net_amount")
    if round(gross_import_cost - gross_export_credit, 3) != round(net_amount, 3):
        raise ValueError("settlement.net_amount must equal gross_import_cost - gross_export_credit")

    if not str(receipt["merkle_root"]).startswith("0x"):
        raise ValueError("merkle_root must be a 0x-prefixed hex string")
    if receipt["stream_hash_alg"] != "sha256":
        raise ValueError("stream_hash_alg must be sha256")


def _require_nested_fields(receipt: Dict[str, Any], key: str, required: set[str]) -> None:
    value = receipt.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Receipt field {key} must be an object")
    missing = sorted(required - set(value))
    if missing:
        raise ValueError(f"Receipt field {key} missing required fields: {', '.join(missing)}")


def _to_float(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except Exception as exc:
        raise ValueError(f"Invalid {field_name}: {value}") from exc

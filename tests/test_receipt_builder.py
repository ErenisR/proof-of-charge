import copy

import pytest

from src.receipt_builder import build_receipt, hash_receipt
from src.receipt_schema import DEFAULT_SCHEMA_VERSION, validate_receipt_model


def sample_session() -> dict:
    return {
        "schema_version": DEFAULT_SCHEMA_VERSION,
        "session_id": "test-session-001",
        "user_id": "user-001",
        "evse_id": "EVSE-001",
        "ocpp_tx_id": "tx-001",
        "session_type": "bidirectional",
        "start_ts": "2026-03-07T10:00:00Z",
        "end_ts": "2026-03-07T10:10:00Z",
        "meter_values": [
            {
                "ts": "2026-03-07T10:10:00Z",
                "import_kwh": 4.0,
                "export_kwh": 1.0,
                "energy_kwh": 3.0,
            },
            {
                "ts": "2026-03-07T10:00:00Z",
                "import_kwh": 1.0,
                "export_kwh": 0.25,
                "energy_kwh": 0.75,
            },
        ],
        "pricing": {
            "currency": "EUR",
            "model": "TOU",
            "import_components": [
                {
                    "from": "2026-03-07T10:00:00Z",
                    "to": "2026-03-07T10:10:00Z",
                    "price_per_kwh": 0.30,
                }
            ],
            "export_components": [
                {
                    "from": "2026-03-07T10:00:00Z",
                    "to": "2026-03-07T10:10:00Z",
                    "price_per_kwh": 0.10,
                }
            ],
        },
    }


def test_build_receipt_uses_canonical_schema_and_energy_math():
    receipt = build_receipt(sample_session())

    assert receipt["version"] == "1.0"
    assert receipt["schema_version"] == DEFAULT_SCHEMA_VERSION
    assert receipt["session_type"] == "bidirectional"
    assert receipt["energy_summary"] == {
        "import_kwh": 3.0,
        "export_kwh": 0.75,
        "net_kwh": 2.25,
    }
    assert receipt["energy_kwh"] == 2.25
    assert receipt["settlement"] == {
        "gross_import_cost": 0.9,
        "gross_export_credit": 0.075,
        "net_amount": 0.825,
        "currency": "EUR",
    }
    assert receipt["merkle_root"].startswith("0x")


def test_hash_receipt_is_deterministic_for_same_receipt_content():
    receipt = build_receipt(sample_session())
    reordered = {
        "stream_hash_alg": receipt["stream_hash_alg"],
        "merkle_root": receipt["merkle_root"],
        **{key: value for key, value in receipt.items() if key not in {"stream_hash_alg", "merkle_root"}},
    }

    assert hash_receipt(receipt) == hash_receipt(reordered)


def test_non_monotone_directional_meter_values_are_rejected():
    session = sample_session()
    session["meter_values"] = [
        {
            "ts": "2026-03-07T10:00:00Z",
            "import_kwh": 2.0,
            "export_kwh": 0.0,
        },
        {
            "ts": "2026-03-07T10:05:00Z",
            "import_kwh": 1.0,
            "export_kwh": 0.0,
        },
    ]

    with pytest.raises(ValueError, match="Non-monotone import_kwh"):
        build_receipt(session)


def test_invalid_pricing_component_is_rejected():
    session = sample_session()
    session["pricing"]["import_components"][0]["price_per_kwh"] = "not-a-price"

    with pytest.raises(ValueError, match=r"pricing\.import_components\[0\]\.price_per_kwh"):
        build_receipt(session)


def test_pricing_component_missing_time_window_is_rejected():
    session = sample_session()
    del session["pricing"]["import_components"][0]["to"]

    with pytest.raises(ValueError, match=r"pricing\.import_components\[0\]\.to is required"):
        build_receipt(session)


def test_receipt_schema_rejects_missing_required_fields():
    receipt = build_receipt(sample_session())
    invalid = copy.deepcopy(receipt)
    del invalid["energy_summary"]["net_kwh"]

    with pytest.raises(ValueError, match="energy_summary missing required fields"):
        validate_receipt_model(invalid)

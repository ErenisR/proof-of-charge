import csv
import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "analyze_blockchain_cost.py"
    spec = importlib.util.spec_from_file_location("analyze_blockchain_cost", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_blockchain_cost_report(tmp_path):
    module = _load_module()
    summary_path = tmp_path / "summary.csv"
    rows = [
        {
            "run_id": "matrix_charge_only_10",
            "session_type_mode": "charge_only",
            "sessions": "10",
            "chain_gas_used": "142000",
            "chain_transaction_fee_wei": "142000000000000",
            "chain_root_match": "True",
            "chain_tx": "0xabc",
        },
        {
            "run_id": "matrix_charge_only_100",
            "session_type_mode": "charge_only",
            "sessions": "100",
            "chain_gas_used": "142500",
            "chain_transaction_fee_wei": "142500000000000",
            "chain_root_match": "True",
            "chain_tx": "0xdef",
        },
    ]

    with summary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    payload = module.build_report(tmp_path)

    assert payload["summary"]["anchored_runs"] == 2
    assert payload["summary"]["gas_used_min"] == 142000
    assert payload["summary"]["gas_used_max"] == 142500
    assert payload["rows"][0]["gas_per_session"] == 14200
    assert payload["rows"][1]["gas_per_session"] == 1425
    assert (tmp_path / "blockchain_cost_summary.csv").exists()
    assert (tmp_path / "blockchain_cost_summary.json").exists()
    assert (tmp_path / "blockchain_cost_interpretation.md").exists()

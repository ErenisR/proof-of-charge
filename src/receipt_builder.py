# src/receipt_builder.py
import json
import hashlib
from typing import Dict, Any, List
from .merkle import merkle_root

def _normalize_meter_values(mvs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Sort by timestamp and ensure energy is monotone non-decreasing.
    """
    mvs_sorted = sorted(mvs, key=lambda mv: mv["ts"])
    energies = [mv["energy_kwh"] for mv in mvs_sorted]

    for i in range(len(energies) - 1):
        if energies[i] > energies[i + 1]:
            raise ValueError("Non-monotone energy_kwh – possible data error")

    return mvs_sorted

def build_receipt(session: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a minimal receipt JSON from a session object.
    Expected fields:
      session_id, evse_id, ocpp_tx_id, start_ts, end_ts,
      meter_values: [{ts, energy_kwh}], pricing: {...}
    """
    mvs = _normalize_meter_values(session["meter_values"])
    if len(mvs) < 2:
        raise ValueError("Need at least 2 meter values")

    # Build Merkle tree leaves as H(ts|energy)
    leaves = []
    for mv in mvs:
        leaf_str = f'{mv["ts"]}|{mv["energy_kwh"]}'
        leaves.append(leaf_str.encode("utf-8"))

    root = merkle_root(leaves)
    energies = [mv["energy_kwh"] for mv in mvs]
    energy_total = energies[-1] - energies[0]

    receipt = {
        "version": "1.0",
        "session_id": session["session_id"],
        "evse_id": session["evse_id"],
        "ocpp_tx_id": session["ocpp_tx_id"],
        "start_ts": session["start_ts"],
        "end_ts": session["end_ts"],
        "energy_kwh": round(float(energy_total), 3),
        "pricing": session.get("pricing", {}),
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

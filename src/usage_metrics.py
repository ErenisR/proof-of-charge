# src/usage_metrics.py
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, DefaultDict
from collections import defaultdict

from .storage import load_index


def _parse_iso(ts: str) -> datetime:
    """
    Parse ISO timestamp used in receipts: 'YYYY-MM-DDTHH:MM:SSZ' or with +00:00.
    """
    if ts.endswith("Z"):
        ts = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(ts)


def _init_stats() -> Dict[str, float]:
    return {
        "sessions": 0.0,
        "total_energy_kwh": 0.0,
        "total_duration_h": 0.0,
    }


def compute_usage_metrics() -> Dict[str, Any]:
    """
    Read all receipts via index.json and compute per-user and per-EVSE usage:

      per_user[user_id] = {
        sessions,
        total_energy_kwh,
        total_duration_h,
        avg_energy_kwh,
        avg_duration_h,
      }

      per_evse[evse_id] = same fields
    """
    index = load_index()

    per_user: DefaultDict[str, Dict[str, float]] = defaultdict(_init_stats)
    per_evse: DefaultDict[str, Dict[str, float]] = defaultdict(_init_stats)

    for session_id, entry in index.items():
        path = Path(entry["file"])
        if not path.exists():
            # Could also log this, but we just skip
            continue

        payload = json.loads(path.read_text())

        # Prefer the receipt object (which is what is anchored)
        receipt: Dict[str, Any] = payload.get("receipt") or {}
        session_meta: Dict[str, Any] = payload.get("session") or {}

        # Try to read user_id/evse_id from receipt first, fall back to session_meta
        user_id = (
            receipt.get("user_id")
            or session_meta.get("user_id")
            or "unknown_user"
        )
        evse_id = (
            receipt.get("evse_id")
            or session_meta.get("evse_id")
            or "unknown_evse"
        )

        # Time & energy
        start_ts = receipt.get("start_ts") or session_meta.get("start_ts")
        end_ts = receipt.get("end_ts") or session_meta.get("end_ts")
        if not start_ts or not end_ts:
            # malformed or partial, skip
            continue

        try:
            start = _parse_iso(start_ts)
            end = _parse_iso(end_ts)
        except Exception:
            # bad timestamps, skip this session
            continue

        duration_h = max((end - start).total_seconds() / 3600.0, 0.0)

        try:
            energy_kwh = float(receipt.get("energy_kwh", 0.0))
        except Exception:
            energy_kwh = 0.0

        # ---- aggregate per user ----
        u = per_user[user_id]
        u["sessions"] += 1.0
        u["total_energy_kwh"] += energy_kwh
        u["total_duration_h"] += duration_h

        # ---- aggregate per EVSE ----
        e = per_evse[evse_id]
        e["sessions"] += 1.0
        e["total_energy_kwh"] += energy_kwh
        e["total_duration_h"] += duration_h

    # Derive averages
    for stats in per_user.values():
        if stats["sessions"] > 0:
            stats["avg_energy_kwh"] = stats["total_energy_kwh"] / stats["sessions"]
            stats["avg_duration_h"] = stats["total_duration_h"] / stats["sessions"]
        else:
            stats["avg_energy_kwh"] = 0.0
            stats["avg_duration_h"] = 0.0

    for stats in per_evse.values():
        if stats["sessions"] > 0:
            stats["avg_energy_kwh"] = stats["total_energy_kwh"] / stats["sessions"]
            stats["avg_duration_h"] = stats["total_duration_h"] / stats["sessions"]
        else:
            stats["avg_energy_kwh"] = 0.0
            stats["avg_duration_h"] = 0.0

    return {
        "per_user": dict(per_user),
        "per_evse": dict(per_evse),
    }


if __name__ == "__main__":
    metrics = compute_usage_metrics()

    per_user = metrics["per_user"]
    per_evse = metrics["per_evse"]

    print("=== Per-user usage ===")
    for user_id, stats in sorted(per_user.items()):
        print(
            f"{user_id}: "
            f"sessions={int(stats['sessions'])}, "
            f"total_kWh={stats['total_energy_kwh']:.1f}, "
            f"avg_kWh={stats.get('avg_energy_kwh', 0.0):.1f}, "
            f"avg_duration_h={stats.get('avg_duration_h', 0.0):.2f}"
        )

    print("\n=== Per-EVSE usage ===")
    for evse_id, stats in sorted(per_evse.items()):
        print(
            f"{evse_id}: "
            f"sessions={int(stats['sessions'])}, "
            f"total_kWh={stats['total_energy_kwh']:.1f}, "
            f"avg_kWh={stats.get('avg_energy_kwh', 0.0):.1f}, "
            f"avg_duration_h={stats.get('avg_duration_h', 0.0):.2f}"
        )

    print(
        f"\nSummary: {len(per_user)} users, {len(per_evse)} EVSEs "
        f"seen in receipts."
    )

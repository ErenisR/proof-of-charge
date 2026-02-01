# src/synthetic_sessions.py
import argparse
import csv
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any

import requests

API_URL = "http://127.0.0.1:8000/v1/receipts/finalize"

def generate_meter_values(
    start_ts: datetime,
    duration_minutes: int,
    interval_minutes: int,
    avg_power_kw: float
) -> List[Dict[str, Any]]:
    """
    Generate synthetic meter values with monotone energy_kwh.
    """
    num_points = duration_minutes // interval_minutes + 1
    mvs = []
    energy = 0.0
    ts = start_ts

    for i in range(num_points):
        if i > 0:
            # add random variation around avg_power
            power_kw = max(0.0, random.gauss(avg_power_kw, avg_power_kw * 0.1))
            hours = interval_minutes / 60.0
            energy += power_kw * hours

        mvs.append({
            "ts": ts.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
            "energy_kwh": round(energy, 3)
        })
        ts += timedelta(minutes=interval_minutes)

    return mvs

def _load_registry(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _pick_evse(registry: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    if not registry:
        return None
    return random.choice(registry)


def generate_session(i: int, registry: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    """
    Generate a single synthetic session matching SessionInput schema.
    """
    now = datetime.now(timezone.utc)
    # Spread sessions over the last 24h
    start_ts = now - timedelta(hours=random.uniform(0, 24))
    duration = random.choice([20, 30, 40, 60, 75])  # minutes
    interval = 5  # meter value every 5 minutes
    avg_power_kw = random.choice([7.2, 11.0, 22.0])  # typical AC chargers
    evse_row = _pick_evse(registry or [])
    if evse_row:
        try:
            avg_power_kw = float(evse_row.get("power_kw") or avg_power_kw)
        except Exception:
            pass

    mvs = generate_meter_values(start_ts, duration, interval, avg_power_kw)
    session_id = f"synth-{i:04d}"
    evse_id = evse_row.get("evse_id") if evse_row else f"EVSE-{random.randint(1, 50):03d}"
    ocpp_tx_id = str(uuid.uuid4())

    session = {
        "session_id": session_id,
        "evse_id": evse_id,
        "ocpp_tx_id": ocpp_tx_id,
        "start_ts": mvs[0]["ts"],
        "end_ts": mvs[-1]["ts"],
        "meter_values": mvs,
        "pricing": {
            "currency": "EUR",
            "model": "TOU",
            "components": [
                {
                    "from": mvs[0]["ts"],
                    "to": mvs[-1]["ts"],
                    "price_per_kwh": round(random.uniform(0.15, 0.35), 3)
                }
            ]
        }
    }
    return session

def send_session(session: Dict[str, Any]) -> None:
    resp = requests.post(API_URL, json=session, timeout=10)
    if resp.status_code != 200:
        print(f"[ERROR] {session['session_id']}: {resp.status_code} {resp.text}")
    else:
        data = resp.json()
        print(f"[OK] {session['session_id']} -> hash={data['hash']}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic charging sessions.")
    parser.add_argument("num_sessions", type=int, help="Number of sessions to generate")
    parser.add_argument(
        "--registry",
        type=Path,
        help="Path to exports/evse_registry.csv to pick real EVSE IDs",
    )
    args = parser.parse_args()

    registry = _load_registry(args.registry) if args.registry else []
    t0 = time.perf_counter()

    for i in range(1, args.num_sessions + 1):
        sess = generate_session(i, registry=registry)
        send_session(sess)

    t1 = time.perf_counter()
    elapsed = t1 - t0
    print(
        f"\nGenerated and finalized {args.num_sessions} sessions in {elapsed:.3f} s "
        f"(avg {elapsed / args.num_sessions:.6f} s per session)"
    )

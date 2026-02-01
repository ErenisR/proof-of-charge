# src/synthetic_sessions.py
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any
import time

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

def generate_session(i: int) -> Dict[str, Any]:
    """
    Generate a single synthetic session matching SessionInput schema.
    """
    now = datetime.now(timezone.utc)
    # Spread sessions over the last 24h
    start_ts = now - timedelta(hours=random.uniform(0, 24))
    duration = random.choice([20, 30, 40, 60, 75])  # minutes
    interval = 5  # meter value every 5 minutes
    avg_power_kw = random.choice([7.2, 11.0, 22.0])  # typical AC chargers

    mvs = generate_meter_values(start_ts, duration, interval, avg_power_kw)
    session_id = f"synth-{i:04d}"
    evse_id = f"EVSE-{random.randint(1, 50):03d}"
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
    import sys

    if len(sys.argv) != 2:
        print("Usage: python -m src.synthetic_sessions <num_sessions>")
        sys.exit(1)

    n = int(sys.argv[1])
    t0 = time.perf_counter()

    for i in range(1, n + 1):
        sess = generate_session(i)
        send_session(sess)

    t1 = time.perf_counter()
    elapsed = t1 - t0
    print(f"\nGenerated and finalized {n} sessions in {elapsed:.3f} s "
          f"(avg {elapsed / n:.6f} s per session)")

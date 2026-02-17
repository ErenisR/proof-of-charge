# src/synthetic_sessions.py
import argparse
import csv
import json
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = "http://127.0.0.1:8000/v1/receipts/finalize"

USERS: List[str] = [f"user-{i:03d}" for i in range(1, 11)]


def generate_meter_values(
    start_ts: datetime,
    duration_minutes: int,
    interval_minutes: int,
    avg_power_kw: float,
    rng: random.Random | None = None,
) -> List[Dict[str, Any]]:
    """
    Generate synthetic meter values with monotone energy_kwh.
    """
    rng = rng or random
    num_points = duration_minutes // interval_minutes + 1
    mvs = []
    energy = 0.0
    ts = start_ts

    for i in range(num_points):
        if i > 0:
            # add random variation around avg_power
            power_kw = max(0.0, rng.gauss(avg_power_kw, avg_power_kw * 0.1))
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


def _pick_evse(
    registry: List[Dict[str, Any]],
    rng: random.Random | None = None,
) -> Dict[str, Any] | None:
    if not registry:
        return None
    rng = rng or random
    return rng.choice(registry)


def generate_session(
    i: int,
    registry: List[Dict[str, Any]] | None = None,
    rng: random.Random | None = None,
    base_now: datetime | None = None,
    session_prefix: str = "synth",
    deterministic_tx: bool = False,
) -> Dict[str, Any]:
    """
    Generate a single synthetic session matching SessionInput schema.
    """
    rng = rng or random
    user_id = rng.choice(USERS)
    now = base_now or datetime.now(timezone.utc)
    # Spread sessions over the last 24h
    start_ts = now - timedelta(hours=rng.uniform(0, 24))
    duration = rng.choice([20, 30, 40, 60, 75])  # minutes
    interval = 5  # meter value every 5 minutes
    avg_power_kw = rng.choice([7.2, 11.0, 22.0])  # typical AC chargers
    evse_row = _pick_evse(registry or [], rng=rng)
    if evse_row:
        try:
            avg_power_kw = float(evse_row.get("power_kw") or avg_power_kw)
        except Exception:
            pass

    mvs = generate_meter_values(start_ts, duration, interval, avg_power_kw, rng=rng)
    session_id = f"{session_prefix}-{i:04d}"
    evse_id = evse_row.get("evse_id") if evse_row else f"EVSE-{rng.randint(1, 50):03d}"
    if deterministic_tx:
        ocpp_tx_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{session_id}:{start_ts.isoformat()}"))
    else:
        ocpp_tx_id = str(uuid.uuid4())

    session = {
        "session_id": session_id,
        "user_id": user_id,
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
                    "price_per_kwh": round(rng.uniform(0.15, 0.35), 3)
                }
            ]
        }
    }
    return session

def send_session(session: Dict[str, Any]) -> None:
    body = json.dumps(session).encode("utf-8")
    req = Request(
        API_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            print(f"[OK] {session['session_id']} -> hash={data.get('hash')}")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"[ERROR] {session['session_id']}: {exc.code} {detail}")
    except URLError as exc:
        print(f"[ERROR] {session['session_id']}: {exc.reason}")
        
def run_synthetic_sessions(
    num_sessions: int,
    registry: List[Dict[str, Any]] | None = None,
    rng: random.Random | None = None,
) -> dict:
    t0 = time.perf_counter()
    rng = rng or random
    for i in range(1, num_sessions + 1):
        sess = generate_session(i, registry=registry, rng=rng)
        send_session(sess)
    t1 = time.perf_counter()
    elapsed = t1 - t0
    return {
        "num_sessions": num_sessions,
        "t_finalize_total": elapsed,
        "t_finalize_avg": elapsed / num_sessions if num_sessions > 0 else 0
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic charging sessions.")
    parser.add_argument("num_sessions", type=int, help="Number of sessions to generate")
    parser.add_argument(
        "--registry",
        type=Path,
        help="Path to EVSE registry CSV used to sample real EVSE IDs",
    )
    args = parser.parse_args()

    registry = _load_registry(args.registry) if args.registry else None
    stats = run_synthetic_sessions(args.num_sessions, registry=registry)
    print(
        f"\nGenerated and finalized {stats['num_sessions']} sessions "
        f"in {stats['t_finalize_total']:.3f} s "
        f"(avg {stats['t_finalize_avg']:.6f} s per session)"
    )

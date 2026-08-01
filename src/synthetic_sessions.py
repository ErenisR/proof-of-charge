# src/synthetic_sessions.py
import argparse
import csv
import json
import random
import time
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .receipt_schema import DEFAULT_SCHEMA_VERSION, SESSION_TYPE_ORDER, SESSION_TYPES

API_URL = "http://127.0.0.1:8000/v1/receipts/finalize"

USERS: List[str] = [f"user-{i:03d}" for i in range(1, 11)]


def _resolve_session_type(
    i: int,
    rng: random.Random,
    session_mode: str = "auto",
    bidirectional_ratio: float = 0.30,
    discharge_ratio: float = 0.15,
) -> str:
    if session_mode == "all":
        return SESSION_TYPE_ORDER[(i - 1) % len(SESSION_TYPE_ORDER)]

    if session_mode in SESSION_TYPES:
        return session_mode

    p = rng.random()
    if p < bidirectional_ratio:
        return "bidirectional"
    if p < bidirectional_ratio + discharge_ratio:
        return "discharge_only"
    return "charge_only"


def generate_meter_values(
    start_ts: datetime,
    duration_minutes: int,
    interval_minutes: int,
    avg_power_kw: float,
    session_type: str = "charge_only",
    rng: random.Random | None = None,
) -> List[Dict[str, Any]]:
    """
    Generate synthetic meter values as V2G-ready cumulative import/export counters.
    """
    rng = rng or random
    num_points = duration_minutes // interval_minutes + 1
    mvs = []
    import_kwh = 0.0
    export_kwh = 0.0
    ts = start_ts
    discharge_points = 0
    discharge_start = 0

    if session_type == "bidirectional" and num_points > 2:
        max_discharge_points = max(1, num_points - 2)
        discharge_points = rng.randint(1, max_discharge_points // 2 + 1)
        if discharge_points >= max_discharge_points:
            discharge_points = max_discharge_points
        discharge_start = rng.randint(0, max_discharge_points - discharge_points)

    for i in range(num_points):
        if i > 0:
            # add random variation around avg_power
            power_kw = max(0.0, rng.gauss(avg_power_kw, avg_power_kw * 0.1))
            hours = interval_minutes / 60.0
            delta_kwh = power_kw * hours

            if session_type == "charge_only":
                import_kwh += delta_kwh
            elif session_type == "discharge_only":
                export_kwh += delta_kwh
            else:
                in_discharge_window = discharge_start <= (i - 1) < (discharge_start + discharge_points)
                if in_discharge_window:
                    export_kwh += delta_kwh
                else:
                    import_kwh += delta_kwh

        net_kwh = import_kwh - export_kwh
        mvs.append({
            "ts": ts.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
            "import_kwh": round(import_kwh, 3),
            "export_kwh": round(export_kwh, 3),
            "energy_kwh": round(net_kwh, 3),
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
    session_mode: str = "auto",
    bidirectional_ratio: float = 0.30,
    discharge_ratio: float = 0.15,
    target_day: str | date | None = None,
) -> Dict[str, Any]:
    """
    Generate a single synthetic session using a V2G-ready schema.
    Session type is determined by session_mode.
    """
    rng = rng or random
    user_id = rng.choice(USERS)
    now = base_now or datetime.now(timezone.utc)
    if target_day is not None:
        parsed_day = date.fromisoformat(target_day) if isinstance(target_day, str) else target_day
        window_start = datetime.combine(parsed_day, time.min, tzinfo=timezone.utc)
        # random() is in [0, 1), so next-day start is never selected.
        start_ts = window_start + timedelta(seconds=rng.random() * 86400.0)
    else:
        start_ts = now - timedelta(hours=rng.uniform(0, 24))
    duration = rng.choice([20, 30, 40, 60, 75])
    interval = 5
    avg_power_kw = rng.choice([7.2, 11.0, 22.0])

    evse_row = _pick_evse(registry or [], rng=rng)
    if evse_row:
        try:
            avg_power_kw = float(evse_row.get("power_kw") or avg_power_kw)
        except Exception:
            pass

    session_type = _resolve_session_type(
        i=i,
        rng=rng,
        session_mode=session_mode or "auto",
        bidirectional_ratio=bidirectional_ratio,
        discharge_ratio=discharge_ratio,
    )

    mvs = generate_meter_values(
        start_ts,
        duration,
        interval,
        avg_power_kw,
        session_type=session_type,
        rng=rng,
    )

    session_id = f"{session_prefix}-{i:04d}"
    evse_id = evse_row.get("evse_id") if evse_row else f"EVSE-{rng.randint(1, 50):03d}"

    if deterministic_tx:
        ocpp_tx_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{session_id}:{start_ts.isoformat()}"))
    else:
        ocpp_tx_id = str(uuid.uuid4())

    import_kwh = round(float(mvs[-1]["import_kwh"]) - float(mvs[0]["import_kwh"]), 3)
    export_kwh = round(float(mvs[-1]["export_kwh"]) - float(mvs[0]["export_kwh"]), 3)
    net_kwh = round(import_kwh - export_kwh, 3)

    import_price = round(rng.uniform(0.15, 0.35), 3)
    export_price = round(rng.uniform(0.05, 0.20), 3)

    gross_import_cost = round(import_kwh * import_price, 3)
    gross_export_credit = round(export_kwh * export_price, 3)
    net_amount = round(gross_import_cost - gross_export_credit, 3)

    session = {
        "schema_version": DEFAULT_SCHEMA_VERSION,
        "session_id": session_id,
        "user_id": user_id,
        "evse_id": evse_id,
        "ocpp_tx_id": ocpp_tx_id,
        "session_type": session_type,
        "start_ts": mvs[0]["ts"],
        "end_ts": mvs[-1]["ts"],
        "meter_values": mvs,
        "energy_summary": {
            "import_kwh": import_kwh,
            "export_kwh": export_kwh,
            "net_kwh": net_kwh,
        },
        "pricing": {
            "currency": "EUR",
            "model": "TOU",
            "import_components": [
                {
                    "from": mvs[0]["ts"],
                    "to": mvs[-1]["ts"],
                    "price_per_kwh": import_price,
                }
            ],
            "export_components": [
                {
                    "from": mvs[0]["ts"],
                    "to": mvs[-1]["ts"],
                    "price_per_kwh": export_price,
                }
            ],
        },
        "settlement": {
            "gross_import_cost": gross_import_cost,
            "gross_export_credit": gross_export_credit,
            "net_amount": net_amount,
            "currency": "EUR",
        },
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
    session_type: str = "auto",
    bidirectional_ratio: float = 0.30,
    discharge_ratio: float = 0.15,
) -> dict:
    t0 = time.perf_counter()
    rng = rng or random
    if session_type not in {"auto", "all", *SESSION_TYPES}:
        raise ValueError(f"Unsupported session_type: {session_type}")
    if not (0.0 <= bidirectional_ratio <= 1.0 and 0.0 <= discharge_ratio <= 1.0):
        raise ValueError("Ratios must be between 0.0 and 1.0")
    if bidirectional_ratio + discharge_ratio > 1.0:
        raise ValueError("bidirectional_ratio + discharge_ratio must not exceed 1.0")
    for i in range(1, num_sessions + 1):
        sess = generate_session(
            i,
            registry=registry,
            rng=rng,
            session_mode=session_type,
            bidirectional_ratio=bidirectional_ratio,
            discharge_ratio=discharge_ratio,
        )
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
    parser.add_argument(
        "--session-type",
        default="auto",
        choices=["auto", "all", *SESSION_TYPE_ORDER],
        help="Session type mode. `all` cycles through charge/discharge/bidirectional.",
    )
    parser.add_argument(
        "--bidirectional-ratio",
        type=float,
        default=0.30,
        help="Ratio for bidirectional when session_type=auto",
    )
    parser.add_argument(
        "--discharge-ratio",
        type=float,
        default=0.15,
        help="Ratio for discharge_only when session_type=auto",
    )
    args = parser.parse_args()

    registry = _load_registry(args.registry) if args.registry else None
    stats = run_synthetic_sessions(
        args.num_sessions,
        registry=registry,
        session_type=args.session_type,
        bidirectional_ratio=args.bidirectional_ratio,
        discharge_ratio=args.discharge_ratio,
    )
    print(
        f"\nGenerated and finalized {stats['num_sessions']} sessions "
        f"in {stats['t_finalize_total']:.3f} s "
        f"(avg {stats['t_finalize_avg']:.6f} s per session)"
    )

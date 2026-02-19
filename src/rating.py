from __future__ import annotations

from typing import Dict, Any, Tuple, List
import json
from pathlib import Path

from .usage_metrics import compute_usage_metrics

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def _score_user(stats: Dict[str, float]) -> float:
    """
    Very simple scoring heuristic for users (0–100):
      - base 50 points
      - + up to +20 for activity (sessions)
      - - up to -20 for lots of tiny sessions (avg_kWh < 2)
      - - up to -30 for low energy/hour (overstay-ish behaviour)
    """
    s = stats.get("sessions", 0.0)
    total_kWh = stats.get("total_energy_kwh", 0.0)
    total_h = stats.get("total_duration_h", 0.0)
    avg_kWh = stats.get("avg_energy_kwh", 0.0)
    
    # sessions-based bonus: up to +20 (for >= 20 sessions)
    activity_bonus = min(s, 20.0)
    
    # tiny sessions penalty: if avg_kWh < 2, penalise linearly up to -20
    if avg_kWh >= 2.0:
        tiny_penalty = 0.0
    else:
        tiny_penalty = (2.0 - avg_kWh) / 2.0 * 20.0
        
    # overstay penalty: use energy per hour
    energy_per_h = (total_kWh / total_h) if total_h > 0 else 0.0
    
    # assume "healthy" ~7 kW; if below that, penalise down to -30
    if energy_per_h >= 7.0:
        overstay_penalty = 0.0
    else:
        overstay_penalty = (7.0 - energy_per_h) / 7.0 * 30.0
        overstay_penalty = _clamp(overstay_penalty, 0.0, 30.0)
        
    raw = 50.0 + activity_bonus - tiny_penalty - overstay_penalty
    return _clamp(raw, 0.0, 100.0)

def _score_evse(stats: Dict[str, float]) -> float:
    """
    Simple scoring for EVSEs:
      - base 50
      - + up to +25 for sessions
      - + up to +25 for total energy
      - - up to -20 if avg_kWh per session is very low (< 3)
    """
    s = stats.get("sessions", 0.0)
    total_kwh = stats.get("total_energy_kwh", 0.0)
    avg_kwh = stats.get("avg_energy_kwh", 0.0)

    # up to +25 for number of sessions (>= 25)
    sessions_bonus = min(s, 25.0)

    # up to +25 for total energy (>= 250 kWh)
    energy_bonus = min(total_kwh / 10.0, 25.0)  # 10 kWh -> +1 point, capped

    # penalty for super tiny avg sessions (could indicate failed charges)
    if avg_kwh >= 3.0:
        tiny_penalty = 0.0
    else:
        tiny_penalty = (3.0 - avg_kwh) / 3.0 * 20.0

    raw = 50.0 + sessions_bonus + energy_bonus - tiny_penalty
    return _clamp(raw, 0.0, 100.0)

def compute_ratings() -> Dict[str, Any]:
    """
    Compute ratings for users and EVSEs based on usage metrics.
    Returns:
      {
        "users": { user_id: { ..., "score": float } },
        "evses": { evse_id: { ..., "score": float } },
      }
    """
    metrics = compute_usage_metrics()
    per_user = metrics["per_user"]
    per_evse = metrics["per_evse"]

    user_ratings: Dict[str, Dict[str, Any]] = {}
    for user_id, stats in per_user.items():
        score = _score_user(stats)
        user_ratings[user_id] = {**stats, "score": score}

    evse_ratings: Dict[str, Dict[str, Any]] = {}
    for evse_id, stats in per_evse.items():
        score = _score_evse(stats)
        evse_ratings[evse_id] = {**stats, "score": score}

    return {
        "users": user_ratings,
        "evses": evse_ratings,
    }


def _top_n_items(mapping: Dict[str, Dict[str, Any]], n: int = 5) -> List[Tuple[str, Dict[str, Any]]]:
    return sorted(mapping.items(), key=lambda kv: kv[1].get("score", 0.0), reverse=True)[:n]


if __name__ == "__main__":
    ratings = compute_ratings()
    user_ratings = ratings["users"]
    evse_ratings = ratings["evses"]

    print("=== Top users by score ===")
    for user_id, stats in _top_n_items(user_ratings, n=5):
        print(
            f"{user_id}: "
            f"score={stats['score']:.1f}, "
            f"sessions={int(stats['sessions'])}, "
            f"total_kWh={stats['total_energy_kwh']:.1f}, "
            f"avg_kWh={stats.get('avg_energy_kwh', 0.0):.1f}, "
            f"avg_duration_h={stats.get('avg_duration_h', 0.0):.2f}"
        )

    print("\n=== Top EVSEs by score ===")
    for evse_id, stats in _top_n_items(evse_ratings, n=5):
        print(
            f"{evse_id}: "
            f"score={stats['score']:.1f}, "
            f"sessions={int(stats['sessions'])}, "
            f"total_kWh={stats['total_energy_kwh']:.1f}, "
            f"avg_kWh={stats.get('avg_energy_kwh', 0.0):.1f}, "
            f"avg_duration_h={stats.get('avg_duration_h', 0.0):.2f}"
        )

    # Optionally dump to JSON for later use
    out_path = Path("results/ratings.json")
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(ratings, indent=2))
    print(f"\nRatings written to {out_path}")
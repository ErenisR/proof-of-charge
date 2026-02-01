# src/charts.py
import csv
import json
import math
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from .receipt_builder import hash_receipt
from .storage import BASE_DIR, load_index

FIGURES_DIR = BASE_DIR / "exports" / "figures"
CAPTIONS_PATH = FIGURES_DIR / "captions.md"
REGISTRY_PATH = BASE_DIR / "exports" / "evse_registry.csv"


def _load_receipt_payload(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


def _safe_import_matplotlib():
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except Exception as exc:
        print(f"[ERROR] matplotlib is not available: {exc}")
        print("Install it with: pip install matplotlib")
        return None
    return plt


def _energy_distribution(energies: List[float], plt) -> None:
    plt.figure(figsize=(6, 4))
    plt.hist(energies, bins=20, color="#4c78a8", edgecolor="white")
    plt.title("Energy per Session (kWh)")
    plt.xlabel("kWh")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / "energy_distribution.png", dpi=150)
    plt.close()


def _receipt_size_vs_samples(sizes: List[int], counts: List[int], plt) -> None:
    plt.figure(figsize=(6, 4))
    plt.scatter(counts, sizes, alpha=0.7, color="#f58518")
    plt.title("Receipt Size vs Meter Sample Count")
    plt.xlabel("Meter sample count")
    plt.ylabel("Receipt JSON size (bytes)")
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / "receipt_size_vs_samples.png", dpi=150)
    plt.close()


def _verification_rate(matches: List[bool], plt) -> None:
    ok = sum(1 for m in matches if m)
    bad = len(matches) - ok
    plt.figure(figsize=(4, 4))
    plt.bar(["Valid", "Tampered"], [ok, bad], color=["#54a24b", "#e45756"])
    plt.title("Receipt Hash Verification")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / "verification_rate.png", dpi=150)
    plt.close()


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _average_consumption_over_time(series: List[List[Dict[str, Any]]], plt) -> None:
    if not series:
        return
    interval_minutes = 5
    max_minutes = 0
    parsed_series = []
    for mvs in series:
        if len(mvs) < 2:
            continue
        start = _parse_ts(mvs[0]["ts"])
        if not start:
            continue
        points = []
        for mv in mvs:
            ts = _parse_ts(mv.get("ts"))
            if not ts:
                continue
            minutes = (ts - start).total_seconds() / 60.0
            points.append((minutes, mv.get("energy_kwh")))
        if points:
            max_minutes = max(max_minutes, points[-1][0])
            parsed_series.append(points)

    if not parsed_series:
        return

    bins = list(range(0, int(max_minutes) + interval_minutes, interval_minutes))
    avg = []
    stds = []
    for b in bins:
        values = []
        for points in parsed_series:
            last = None
            for t, e in points:
                if t <= b:
                    last = e
                else:
                    break
            if last is not None:
                values.append(last)
        if values:
            mean = sum(values) / len(values)
            var = sum((v - mean) ** 2 for v in values) / len(values)
            avg.append((b, mean))
            stds.append((b, math.sqrt(var)))

    if not avg:
        return

    xs = [x for x, _ in avg]
    ys = [y for _, y in avg]
    sigmas = [s for _, s in stds]
    plt.figure(figsize=(7, 4))
    plt.plot(xs, ys, color="#4c78a8", label="Mean")
    plt.fill_between(
        xs,
        [y - s for y, s in zip(ys, sigmas)],
        [y + s for y, s in zip(ys, sigmas)],
        color="#4c78a8",
        alpha=0.2,
        label="±1σ",
    )
    plt.title("Average Consumption Over Time (±1σ)")
    plt.xlabel("Minutes since session start")
    plt.ylabel("Cumulative energy (kWh)")
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / "consumption_over_time.png", dpi=150)
    plt.close()
    _consumption_bar_chart(xs, ys, plt)


def _average_price_over_time(price_series: List[List[Dict[str, Any]]], plt) -> None:
    if not price_series:
        return
    interval_minutes = 5
    max_minutes = 0
    parsed_series = []

    for components in price_series:
        if not components:
            continue
        start = _parse_ts(components[0].get("from"))
        if not start:
            continue
        segments = []
        for comp in components:
            from_ts = _parse_ts(comp.get("from"))
            to_ts = _parse_ts(comp.get("to"))
            price = comp.get("price_per_kwh")
            if not (from_ts and to_ts):
                continue
            if price is None:
                continue
            start_min = (from_ts - start).total_seconds() / 60.0
            end_min = (to_ts - start).total_seconds() / 60.0
            segments.append((start_min, end_min, float(price)))
            max_minutes = max(max_minutes, end_min)
        if segments:
            parsed_series.append(segments)

    if not parsed_series:
        return

    bins = list(range(0, int(max_minutes) + interval_minutes, interval_minutes))
    avg = []
    stds = []
    for b in bins:
        values = []
        for segments in parsed_series:
            price = None
            for start_min, end_min, p in segments:
                if start_min <= b < end_min:
                    price = p
                    break
            if price is not None:
                values.append(price)
        if values:
            mean = sum(values) / len(values)
            var = sum((v - mean) ** 2 for v in values) / len(values)
            avg.append((b, mean))
            stds.append((b, math.sqrt(var)))

    if not avg:
        return

    xs = [x for x, _ in avg]
    ys = [y for _, y in avg]
    sigmas = [s for _, s in stds]
    plt.figure(figsize=(7, 4))
    plt.step(xs, ys, where="post", color="#f58518", label="Mean")
    plt.fill_between(
        xs,
        [y - s for y, s in zip(ys, sigmas)],
        [y + s for y, s in zip(ys, sigmas)],
        color="#f58518",
        alpha=0.2,
        step="post",
        label="±1σ",
    )
    plt.title("Average Price Over Time (±1σ)")
    plt.xlabel("Minutes since session start")
    plt.ylabel("Price per kWh")
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / "price_over_time.png", dpi=150)
    plt.close()
    _price_bar_chart(xs, ys, plt)


def _consumption_bar_chart(xs: List[float], ys: List[float], plt) -> None:
    plt.figure(figsize=(7, 4))
    plt.bar(xs, ys, width=4.0, color="#4c78a8")
    plt.title("Average Consumption by Time Bin")
    plt.xlabel("Minutes since session start")
    plt.ylabel("Cumulative energy (kWh)")
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / "consumption_over_time_bar.png", dpi=150)
    plt.close()


def _price_bar_chart(xs: List[float], ys: List[float], plt) -> None:
    plt.figure(figsize=(7, 4))
    plt.bar(xs, ys, width=4.0, color="#f58518")
    plt.title("Average Price by Time Bin")
    plt.xlabel("Minutes since session start")
    plt.ylabel("Price per kWh")
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / "price_over_time_bar.png", dpi=150)
    plt.close()


def _load_registry_rows() -> List[Dict[str, Any]]:
    if not REGISTRY_PATH.exists():
        return []
    with REGISTRY_PATH.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _power_distribution(powers: List[float], plt) -> None:
    plt.figure(figsize=(6, 4))
    plt.hist(powers, bins=20, color="#72b7b2", edgecolor="white")
    plt.title("EVSE Power Distribution (kW)")
    plt.xlabel("kW")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / "evse_power_distribution.png", dpi=150)
    plt.close()


def _connector_mix(counts: Dict[str, int], plt) -> None:
    if not counts:
        return
    labels = list(counts.keys())
    values = [counts[k] for k in labels]
    plt.figure(figsize=(7, 4))
    plt.bar(labels, values, color="#4c78a8")
    plt.title("EVSE Connector Type Mix")
    plt.xlabel("Connector type")
    plt.ylabel("Count")
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / "evse_connector_mix.png", dpi=150)
    plt.close()


def generate_charts() -> None:
    plt = _safe_import_matplotlib()
    if plt is None:
        return

    index = load_index()
    energies: List[float] = []
    sizes: List[int] = []
    counts: List[int] = []
    matches: List[bool] = []
    meter_series: List[List[Dict[str, Any]]] = []
    price_series: List[List[Dict[str, Any]]] = []

    for session_id, entry in index.items():
        path = Path(entry["file"])
        if not path.exists():
            continue
        payload = _load_receipt_payload(path)
        receipt = payload.get("receipt", {})
        if "energy_kwh" in receipt:
            try:
                energies.append(float(receipt["energy_kwh"]))
            except Exception:
                pass

        receipt_json = json.dumps(receipt, sort_keys=True)
        sizes.append(len(receipt_json.encode("utf-8")))

        session = payload.get("session", {})
        mvs = session.get("meter_values", []) or []
        counts.append(len(mvs))
        if mvs:
            meter_series.append(mvs)

        pricing = session.get("pricing") or {}
        components = pricing.get("components") or []
        if components:
            price_series.append(components)

        expected = payload.get("hash")
        computed = hash_receipt(receipt)
        matches.append(bool(expected == computed))

    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    if energies:
        _energy_distribution(energies, plt)
    if sizes and counts:
        _receipt_size_vs_samples(sizes, counts, plt)
    if matches:
        _verification_rate(matches, plt)
    if meter_series:
        _average_consumption_over_time(meter_series, plt)
    if price_series:
        _average_price_over_time(price_series, plt)

    registry_rows = _load_registry_rows()
    registry_captions = []
    if registry_rows:
        powers: List[float] = []
        connector_counts: Dict[str, int] = {}
        for row in registry_rows:
            power = row.get("power_kw")
            if power:
                try:
                    powers.append(float(power))
                except Exception:
                    pass
            connector = row.get("connector_type") or "unknown"
            connector_counts[connector] = connector_counts.get(connector, 0) + 1

        if powers:
            _power_distribution(powers, plt)
            registry_captions.append(
                "- evse_power_distribution.png: Distribution of EVSE nominal power (kW) from registry."
            )
        if connector_counts:
            _connector_mix(connector_counts, plt)
            registry_captions.append(
                "- evse_connector_mix.png: Connector type mix from EVSE registry."
            )

    captions = [
        "# Figure Captions",
        "",
        "- energy_distribution.png: Histogram of session energy (kWh) across all receipts.",
        "- receipt_size_vs_samples.png: Receipt JSON size as a function of meter sample count.",
        "- verification_rate.png: Count of valid vs tampered receipts based on hash verification.",
        "- consumption_over_time.png: Average cumulative energy vs time across sessions with ±1σ band.",
        "- consumption_over_time_bar.png: Average cumulative energy by time bin (bar chart).",
        "- price_over_time.png: Average price per kWh over time across sessions with ±1σ band.",
        "- price_over_time_bar.png: Average price per kWh by time bin (bar chart).",
        *registry_captions,
        "",
        "Note: Figures are generated from locally stored receipts and may be synthetic.",
    ]
    CAPTIONS_PATH.write_text("\\n".join(captions), encoding="utf-8")

    print(f"[OK] Figures written to {FIGURES_DIR}")


if __name__ == "__main__":
    generate_charts()

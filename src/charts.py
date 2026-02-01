# src/charts.py
import json
from pathlib import Path
from typing import List, Dict, Any

from .receipt_builder import hash_receipt
from .storage import BASE_DIR, load_index

FIGURES_DIR = BASE_DIR / "exports" / "figures"
CAPTIONS_PATH = FIGURES_DIR / "captions.md"


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


def generate_charts() -> None:
    plt = _safe_import_matplotlib()
    if plt is None:
        return

    index = load_index()
    energies: List[float] = []
    sizes: List[int] = []
    counts: List[int] = []
    matches: List[bool] = []

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

    captions = [
        "# Figure Captions",
        "",
        "- energy_distribution.png: Histogram of session energy (kWh) across all receipts.",
        "- receipt_size_vs_samples.png: Receipt JSON size as a function of meter sample count.",
        "- verification_rate.png: Count of valid vs tampered receipts based on hash verification.",
        "",
        "Note: Figures are generated from locally stored receipts and may be synthetic.",
    ]
    CAPTIONS_PATH.write_text("\\n".join(captions), encoding="utf-8")

    print(f"[OK] Figures written to {FIGURES_DIR}")


if __name__ == "__main__":
    generate_charts()

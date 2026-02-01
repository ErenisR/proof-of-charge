# src/import_irve.py
import csv
import json
from pathlib import Path
from typing import Dict, Any, Iterable

from .storage import BASE_DIR

EXPORT_DIR = BASE_DIR / "exports"
REGISTRY_PATH = EXPORT_DIR / "evse_registry.csv"


def _iter_geojson_features(path: Path) -> Iterable[Dict[str, Any]]:
    data = json.loads(path.read_text())
    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates") or []
        lon = coords[0] if len(coords) > 0 else None
        lat = coords[1] if len(coords) > 1 else None
        props["longitude"] = lon
        props["latitude"] = lat
        yield props


def _iter_csv_rows(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    def pick(*keys: str) -> str | None:
        for key in keys:
            val = row.get(key)
            if val not in (None, ""):
                return str(val)
        return None

    return {
        "evse_id": pick("id_pdc_itinerance", "id_pdc_local"),
        "station_id": pick("id_station_itinerance", "id_station_local"),
        "operator": pick("nom_operateur", "nom_amenageur"),
        "station_name": pick("nom_station"),
        "address": pick("adresse_station"),
        "postcode": pick("code_postal"),
        "city": pick("commune"),
        "longitude": pick("longitude"),
        "latitude": pick("latitude"),
        "power_kw": pick("puissance_nominale"),
        "connector_type": pick("type_prise"),
        "access": pick("accessibilite_pmr"),
    }


def import_irve(path: Path) -> int:
    if not path.exists():
        raise FileNotFoundError(path)

    if path.suffix.lower() == ".geojson":
        rows = _iter_geojson_features(path)
    else:
        rows = _iter_csv_rows(path)

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    with REGISTRY_PATH.open("w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "evse_id",
            "station_id",
            "operator",
            "station_name",
            "address",
            "postcode",
            "city",
            "longitude",
            "latitude",
            "power_kw",
            "connector_type",
            "access",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            normalized = _normalize_row(row)
            if not normalized["evse_id"]:
                continue
            writer.writerow(normalized)
            count += 1

    return count


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python -m src.import_irve <path-to-csv-or-geojson>")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    n = import_irve(input_path)
    print(f"[OK] Imported {n} EVSE rows to {REGISTRY_PATH}")

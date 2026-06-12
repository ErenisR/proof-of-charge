import argparse
import csv
import json
import random
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from .batch_anchoring import anchor_day
from .blockchain.publisher import publish_batch_anchor
from .blockchain.verifier import verify_on_chain_anchor
from .charts import generate_charts
from . import db
from .export import export_all
from .models import BatchAnchor, BatchAnchorReceipt, ChargingSession, MeterValue, Receipt
from .repository import persist_finalized_session
from .receipt_builder import build_receipt, hash_receipt
from .receipt_schema import SESSION_TYPE_ORDER
from .storage import BASE_DIR, load_index, save_receipt, write_local_receipts_enabled
from .synthetic_sessions import _load_registry, generate_session
from .verifier_batch import verify_day

RESULTS_DIR = BASE_DIR / "results"
EXPORTS_DIR = BASE_DIR / "exports"
FIGURES_DIR = EXPORTS_DIR / "figures"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _default_run_id() -> str:
    return _now_utc().strftime("run_%Y%m%d_%H%M%S")


def _day_end(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T23:59:00+00:00")


def _to_json(value: Any, path: Path) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _load_payload(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _parse_ts(ts: str) -> datetime:
    if ts.endswith("Z"):
        ts = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(ts)


def _isoformat(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _coerce_components(pricing: Dict[str, Any], keys: list[str]) -> List[Dict[str, Any]]:
    for key in keys:
        comps = pricing.get(key)
        if isinstance(comps, list):
            return comps
    return []


def _sum_component_cost(
    start_ts: str,
    end_ts: str,
    energy_kwh: float,
    components: List[Dict[str, Any]],
) -> float:
    if not start_ts or not end_ts:
        return 0.0
    if not components or energy_kwh == 0:
        return 0.0
    try:
        session_start = _parse_ts(start_ts)
        session_end = _parse_ts(end_ts)
        session_seconds = (session_end - session_start).total_seconds()
    except Exception:
        return 0.0
    if session_seconds <= 0:
        return 0.0

    total = 0.0
    for comp in components:
        c_from = comp.get("from")
        c_to = comp.get("to")
        if not c_from or not c_to:
            continue
        try:
            c_start = _parse_ts(c_from)
            c_end = _parse_ts(c_to)
            overlap_start = max(session_start, c_start)
            overlap_end = min(session_end, c_end)
            overlap_seconds = (overlap_end - overlap_start).total_seconds()
            if overlap_seconds <= 0:
                continue
            share = overlap_seconds / session_seconds
            price = _to_float(comp.get("price_per_kwh"), 0.0)
            total += energy_kwh * share * price
        except Exception:
            continue
    return round(total, 3)


def _meter_delta(meter_values: List[Dict[str, Any]]) -> tuple[float, float]:
    if not meter_values or len(meter_values) < 2:
        return 0.0, 0.0
    first = meter_values[0]
    last = meter_values[-1]
    import_start = _to_float(first.get("import_kwh"), 0.0)
    export_start = _to_float(first.get("export_kwh"), 0.0)
    import_end = _to_float(last.get("import_kwh"), 0.0)
    export_end = _to_float(last.get("export_kwh"), 0.0)
    return round(import_end - import_start, 3), round(export_end - export_start, 3)


def _session_audit(session: Dict[str, Any], receipt: Dict[str, Any]) -> Dict[str, Any]:
    meter_values = session.get("meter_values", []) or []
    measured_import_kwh, measured_export_kwh = _meter_delta(meter_values)
    measured_net_kwh = round(measured_import_kwh - measured_export_kwh, 3)

    summary = session.get("energy_summary") or {}
    summary_import_kwh = round(_to_float(summary.get("import_kwh"), measured_import_kwh), 3)
    summary_export_kwh = round(_to_float(summary.get("export_kwh"), measured_export_kwh), 3)
    summary_net_kwh = round(_to_float(summary.get("net_kwh"), measured_net_kwh), 3)

    receipt_summary = receipt.get("energy_summary", {})
    receipt_import_kwh = round(_to_float(receipt_summary.get("import_kwh"), measured_import_kwh), 3)
    receipt_export_kwh = round(_to_float(receipt_summary.get("export_kwh"), measured_export_kwh), 3)
    receipt_net_kwh = round(_to_float(receipt_summary.get("net_kwh"), measured_net_kwh), 3)

    pricing = session.get("pricing", {})
    start_ts = session.get("start_ts")
    end_ts = session.get("end_ts")
    import_components = _coerce_components(pricing, ["import_components", "components"])
    export_components = _coerce_components(pricing, ["export_components", "components"])

    expected_import_cost = _sum_component_cost(start_ts, end_ts, receipt_import_kwh, import_components)
    expected_export_credit = _sum_component_cost(start_ts, end_ts, receipt_export_kwh, export_components)
    expected_net_amount = round(expected_import_cost - expected_export_credit, 3)
    settlement = receipt.get("settlement", {})
    actual_net_amount = round(_to_float(settlement.get("net_amount")), 3)

    import_ok = measured_import_kwh == receipt_import_kwh == summary_import_kwh
    export_ok = measured_export_kwh == receipt_export_kwh == summary_export_kwh
    net_ok = measured_net_kwh == receipt_net_kwh == summary_net_kwh
    settlement_ok = actual_net_amount == expected_net_amount
    audit_ok = all([import_ok, export_ok, net_ok, settlement_ok])

    reasons = []
    if not import_ok:
        reasons.append("import_kwh mismatch")
    if not export_ok:
        reasons.append("export_kwh mismatch")
    if not net_ok:
        reasons.append("net_kwh mismatch")
    if not settlement_ok:
        reasons.append("settlement mismatch")

    return {
        "import_consistent": str(import_ok),
        "export_consistent": str(export_ok),
        "net_consistent": str(net_ok),
        "settlement_consistent": str(settlement_ok),
        "audit_ok": str(audit_ok),
        "audit_reason": "; ".join(reasons),
    }


def _write_csv(path: Path, rows: Iterable[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def _finalize_synthetic_sessions(
    num_sessions: int,
    day: str,
    session_prefix: str,
    seed: int,
    registry_path: Path | None = None,
    session_type: str = "auto",
    bidirectional_ratio: float = 0.30,
    discharge_ratio: float = 0.15,
) -> Dict[str, Any]:
    registry = _load_registry(registry_path) if registry_path else None
    rng = random.Random(seed)
    base_now = _day_end(day)
    session_ids: List[str] = []

    t0 = time.perf_counter()
    for i in range(1, num_sessions + 1):
        session = generate_session(
            i,
            registry=registry,
            rng=rng,
            base_now=base_now,
            session_prefix=session_prefix,
            deterministic_tx=True,
            session_mode=session_type,
            bidirectional_ratio=bidirectional_ratio,
            discharge_ratio=discharge_ratio,
        )
        receipt = build_receipt(session)
        receipt_hash = hash_receipt(receipt)
        db_enabled = db.database_enabled()
        if db_enabled:
            persist_finalized_session(session, receipt, receipt_hash)
        if not db_enabled or write_local_receipts_enabled():
            save_receipt(session["session_id"], receipt, receipt_hash, session)
        session_ids.append(session["session_id"])
    t1 = time.perf_counter()

    return {
        "session_ids": session_ids,
        "t_finalize_total": t1 - t0,
        "t_finalize_avg": (t1 - t0) / num_sessions if num_sessions else 0,
    }


def _build_run_exports(
    run_dir: Path,
    session_prefix: str,
    day: str,
    batch_root: str,
) -> Dict[str, int]:
    if db.database_enabled():
        session = db.session_scope()
        try:
            return _build_run_exports_from_db(
                db_session=session,
                run_dir=run_dir,
                session_prefix=session_prefix,
                day=day,
                batch_root=batch_root,
            )
        finally:
            session.close()
    return _build_run_exports_from_files(
        run_dir=run_dir,
        session_prefix=session_prefix,
        day=day,
        batch_root=batch_root,
    )


def _build_run_exports_from_files(
    run_dir: Path,
    session_prefix: str,
    day: str,
    batch_root: str,
) -> Dict[str, int]:
    index = load_index()
    receipts_rows: List[Dict[str, Any]] = []
    sessions_rows: List[Dict[str, Any]] = []
    meter_rows: List[Dict[str, Any]] = []
    verify_rows: List[Dict[str, Any]] = []

    for session_id in sorted(index.keys()):
        if not session_id.startswith(f"{session_prefix}-"):
            continue
        entry = index[session_id]
        payload = _load_payload(Path(entry["file"]))
        receipt = payload.get("receipt", {})
        session = payload.get("session", {})
        expected_hash = payload.get("hash")
        computed_hash = hash_receipt(receipt)

        receipts_rows.append(
            {
                "session_id": session_id,
                "user_id": receipt.get("user_id"),
                "receipt_hash": expected_hash,
                "merkle_root": receipt.get("merkle_root"),
                "energy_kwh": receipt.get("energy_kwh"),
                "import_kwh": (receipt.get("energy_summary") or {}).get("import_kwh"),
                "export_kwh": (receipt.get("energy_summary") or {}).get("export_kwh"),
                "net_kwh": (receipt.get("energy_summary") or {}).get("net_kwh"),
                "start_ts": receipt.get("start_ts"),
                "end_ts": receipt.get("end_ts"),
                "batch_day": entry.get("batch_day"),
                "batch_root": entry.get("batch_root"),
            }
        )
        sessions_rows.append(
            {
                "session_id": session_id,
                "user_id": session.get("user_id"),
                "evse_id": session.get("evse_id"),
                "start_ts": session.get("start_ts"),
                "end_ts": session.get("end_ts"),
                "session_type": session.get("session_type"),
                "tariff_model": (session.get("pricing") or {}).get("model"),
            }
        )
        for mv in session.get("meter_values", []) or []:
            meter_rows.append(
                {
                    "session_id": session_id,
                    "ts": mv.get("ts"),
                    "energy_kwh": mv.get("energy_kwh"),
                    "import_kwh": mv.get("import_kwh"),
                    "export_kwh": mv.get("export_kwh"),
                }
            )
        audit = _session_audit(session, receipt)
        verify_rows.append(
            {
                "session_id": session_id,
                "expected_hash": expected_hash,
                "computed_hash": computed_hash,
                **audit,
                "match": str(expected_hash == computed_hash),
                "batch_day": day,
                "batch_root": batch_root,
            }
        )

    _write_csv(
        run_dir / "datasets" / "receipts.csv",
        receipts_rows,
        [
            "session_id",
            "user_id",
            "energy_kwh",
            "import_kwh",
            "export_kwh",
            "net_kwh",
            "start_ts",
            "end_ts",
            "batch_day",
            "receipt_hash",
            "merkle_root",
            "batch_root",
        ],
    )
    _write_csv(
        run_dir / "datasets" / "sessions.csv",
        sessions_rows,
        ["session_id", "user_id", "evse_id", "start_ts", "end_ts", "session_type", "tariff_model"],
    )
    _write_csv(
        run_dir / "datasets" / "meter_values.csv",
        meter_rows,
        ["session_id", "ts", "energy_kwh", "import_kwh", "export_kwh"],
    )
    _write_csv(
        run_dir / "datasets" / "verifications.csv",
        verify_rows,
        [
            "session_id",
            "match",
            "import_consistent",
            "export_consistent",
            "net_consistent",
            "settlement_consistent",
            "audit_ok",
            "audit_reason",
            "batch_day",
            "expected_hash",
            "computed_hash",
            "batch_root",
        ],
    )
    _write_csv(
        run_dir / "datasets" / "anchors.csv",
        [
            {
                "day": day,
                "session_prefix": session_prefix,
                "batch_root": batch_root,
                "receipt_count": len(receipts_rows),
            }
        ],
        ["day", "session_prefix", "receipt_count", "batch_root"],
    )

    return {
        "sessions": len(sessions_rows),
        "meter_values": len(meter_rows),
        "receipts": len(receipts_rows),
        "verifications": len(verify_rows),
    }


def _build_run_exports_from_db(
    db_session: Session,
    run_dir: Path,
    session_prefix: str,
    day: str,
    batch_root: str,
) -> Dict[str, int]:
    session_ids = _run_session_ids(db_session, session_prefix)
    receipts = {
        receipt.session_id: receipt
        for receipt in db_session.scalars(
            select(Receipt)
            .where(Receipt.session_id.in_(session_ids))
            .order_by(Receipt.session_id)
        )
    }
    sessions = {
        session.session_id: session
        for session in db_session.scalars(
            select(ChargingSession)
            .where(ChargingSession.session_id.in_(session_ids))
            .order_by(ChargingSession.session_id)
        )
    }
    meter_values = list(
        db_session.scalars(
            select(MeterValue)
            .where(MeterValue.session_id.in_(session_ids))
            .order_by(MeterValue.session_id, MeterValue.sample_index)
        )
    )
    anchor_lookup = _run_anchor_lookup(db_session, session_prefix)

    receipts_rows: List[Dict[str, Any]] = []
    sessions_rows: List[Dict[str, Any]] = []
    meter_rows: List[Dict[str, Any]] = []
    verify_rows: List[Dict[str, Any]] = []

    for session_id in session_ids:
        receipt_row = receipts.get(session_id)
        session_row = sessions.get(session_id)
        if not receipt_row or not session_row:
            continue
        receipt = receipt_row.receipt_json or {}
        session_payload = session_row.session_json or {}
        expected_hash = receipt_row.receipt_hash
        computed_hash = hash_receipt(receipt)
        anchor = anchor_lookup.get(session_id, {"batch_day": day, "batch_root": batch_root})

        receipts_rows.append(
            {
                "session_id": session_id,
                "user_id": receipt.get("user_id"),
                "energy_kwh": receipt_row.energy_kwh,
                "import_kwh": receipt_row.import_kwh,
                "export_kwh": receipt_row.export_kwh,
                "net_kwh": receipt_row.net_kwh,
                "start_ts": _isoformat(receipt_row.start_ts),
                "end_ts": _isoformat(receipt_row.end_ts),
                "batch_day": anchor.get("batch_day"),
                "receipt_hash": expected_hash,
                "merkle_root": receipt_row.merkle_root,
                "batch_root": anchor.get("batch_root"),
            }
        )
        sessions_rows.append(
            {
                "session_id": session_id,
                "user_id": session_row.user_id,
                "evse_id": session_row.evse_id,
                "start_ts": _isoformat(session_row.start_ts),
                "end_ts": _isoformat(session_row.end_ts),
                "session_type": session_row.session_type,
                "tariff_model": (session_payload.get("pricing") or {}).get("model"),
            }
        )
        audit = _session_audit(session_payload, receipt)
        verify_rows.append(
            {
                "session_id": session_id,
                "expected_hash": expected_hash,
                "computed_hash": computed_hash,
                **audit,
                "match": str(expected_hash == computed_hash),
                "batch_day": anchor.get("batch_day"),
                "batch_root": anchor.get("batch_root"),
            }
        )

    for mv in meter_values:
        meter_rows.append(
            {
                "session_id": mv.session_id,
                "ts": _isoformat(mv.ts),
                "energy_kwh": mv.energy_kwh,
                "import_kwh": mv.import_kwh,
                "export_kwh": mv.export_kwh,
            }
        )

    return _write_run_dataset_csvs(
        run_dir=run_dir,
        day=day,
        session_prefix=session_prefix,
        batch_root=batch_root,
        receipts_rows=receipts_rows,
        sessions_rows=sessions_rows,
        meter_rows=meter_rows,
        verify_rows=verify_rows,
    )


def _write_run_dataset_csvs(
    run_dir: Path,
    day: str,
    session_prefix: str,
    batch_root: str,
    receipts_rows: List[Dict[str, Any]],
    sessions_rows: List[Dict[str, Any]],
    meter_rows: List[Dict[str, Any]],
    verify_rows: List[Dict[str, Any]],
) -> Dict[str, int]:
    _write_csv(
        run_dir / "datasets" / "receipts.csv",
        receipts_rows,
        [
            "session_id",
            "user_id",
            "energy_kwh",
            "import_kwh",
            "export_kwh",
            "net_kwh",
            "start_ts",
            "end_ts",
            "batch_day",
            "receipt_hash",
            "merkle_root",
            "batch_root",
        ],
    )
    _write_csv(
        run_dir / "datasets" / "sessions.csv",
        sessions_rows,
        ["session_id", "user_id", "evse_id", "start_ts", "end_ts", "session_type", "tariff_model"],
    )
    _write_csv(
        run_dir / "datasets" / "meter_values.csv",
        meter_rows,
        ["session_id", "ts", "energy_kwh", "import_kwh", "export_kwh"],
    )
    _write_csv(
        run_dir / "datasets" / "verifications.csv",
        verify_rows,
        [
            "session_id",
            "match",
            "import_consistent",
            "export_consistent",
            "net_consistent",
            "settlement_consistent",
            "audit_ok",
            "audit_reason",
            "batch_day",
            "expected_hash",
            "computed_hash",
            "batch_root",
        ],
    )
    _write_csv(
        run_dir / "datasets" / "anchors.csv",
        [
            {
                "day": day,
                "session_prefix": session_prefix,
                "batch_root": batch_root,
                "receipt_count": len(receipts_rows),
            }
        ],
        ["day", "session_prefix", "receipt_count", "batch_root"],
    )

    return {
        "sessions": len(sessions_rows),
        "meter_values": len(meter_rows),
        "receipts": len(receipts_rows),
        "verifications": len(verify_rows),
    }


def _run_session_ids(db_session: Session, session_prefix: str) -> List[str]:
    return list(
        db_session.scalars(
            select(ChargingSession.session_id)
            .where(ChargingSession.session_id.like(f"{session_prefix}-%"))
            .order_by(ChargingSession.session_id)
        )
    )


def _run_anchor_lookup(db_session: Session, session_prefix: str) -> Dict[str, Dict[str, str]]:
    anchors = list(
        db_session.scalars(
            select(BatchAnchor)
            .where(BatchAnchor.session_prefix == session_prefix)
            .order_by(BatchAnchor.id)
        )
    )
    if not anchors:
        return {}
    anchors_by_id = {anchor.id: anchor for anchor in anchors}
    memberships = list(
        db_session.scalars(
            select(BatchAnchorReceipt)
            .where(BatchAnchorReceipt.anchor_id.in_(anchors_by_id.keys()))
            .order_by(BatchAnchorReceipt.anchor_id, BatchAnchorReceipt.leaf_index)
        )
    )
    return {
        membership.session_id: {
            "batch_day": anchors_by_id[membership.anchor_id].day,
            "batch_root": anchors_by_id[membership.anchor_id].batch_root,
        }
        for membership in memberships
        if membership.anchor_id in anchors_by_id
    }


def _copy_figures(run_dir: Path) -> None:
    target = run_dir / "figures"
    target.mkdir(parents=True, exist_ok=True)
    if not FIGURES_DIR.exists():
        return
    for src in FIGURES_DIR.glob("*.png"):
        shutil.copy2(src, target / src.name)
    captions = FIGURES_DIR / "captions.md"
    if captions.exists():
        shutil.copy2(captions, target / "captions.md")


def run_experiment(
    num_sessions: int,
    day: str,
    seed: int = 42,
    run_id: str | None = None,
    registry_path: Path | None = None,
    session_type: str = "auto",
    bidirectional_ratio: float = 0.30,
    discharge_ratio: float = 0.15,
    skip_figures: bool = False,
    publish_chain: bool = False,
) -> Dict[str, Any]:
    run_id = run_id or _default_run_id()
    run_dir = RESULTS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    synth = _finalize_synthetic_sessions(
        num_sessions=num_sessions,
        day=day,
        session_prefix=run_id,
        seed=seed,
        registry_path=registry_path,
        session_type=session_type,
        bidirectional_ratio=bidirectional_ratio,
        discharge_ratio=discharge_ratio,
    )

    batch_root, anchored_count = anchor_day(day, session_prefix=run_id)
    verify = verify_day(day, session_prefix=run_id)
    chain_publish = None
    chain_verify = None
    if publish_chain:
        chain_publish = publish_batch_anchor(day, session_prefix=run_id)
        chain_verify = verify_on_chain_anchor(day, session_prefix=run_id)

    # Keep global exports up to date, then optionally render run-scoped charts.
    export_all()
    if not skip_figures:
        generate_charts(session_prefix=run_id)
        _copy_figures(run_dir)

    dataset_counts = _build_run_exports(
        run_dir=run_dir,
        session_prefix=run_id,
        day=day,
        batch_root=batch_root,
    )

    metrics = {
        "run_id": run_id,
        "day": day,
        "seed": seed,
        "num_sessions_requested": num_sessions,
        "num_sessions_anchored": anchored_count,
        "t_finalize_total_sec": round(synth["t_finalize_total"], 6),
        "t_finalize_avg_sec": round(synth["t_finalize_avg"], 6),
        "batch_root": batch_root,
        "batch_root_match": verify["match"],
        "receipt_count_verified": verify["receipt_count"],
        "datasets": dataset_counts,
    }
    if chain_publish and chain_verify:
        metrics.update(
            {
                "chain_tx": chain_publish["chain_tx"],
                "chain_block_number": chain_publish["chain_block_number"],
                "chain_block_timestamp": chain_publish["chain_block_timestamp"],
                "chain_gas_used": chain_publish["chain_gas_used"],
                "chain_effective_gas_price_wei": chain_publish["chain_effective_gas_price_wei"],
                "chain_transaction_fee_wei": chain_publish["chain_transaction_fee_wei"],
                "chain_status": chain_publish["chain_status"],
                "chain_root_match": chain_verify["match"],
                "chain_on_chain_receipt_count": chain_verify["on_chain_receipt_count"],
            }
        )
    _to_json(
        {
            "run_id": run_id,
            "created_at_utc": _now_utc().isoformat().replace("+00:00", "Z"),
            "config": {
                "num_sessions": num_sessions,
                "day": day,
                "seed": seed,
                "session_type": session_type,
                "bidirectional_ratio": bidirectional_ratio,
                "discharge_ratio": discharge_ratio,
                "registry_path": str(registry_path) if registry_path else None,
                "skip_figures": skip_figures,
                "publish_chain": publish_chain,
            },
            "metrics": metrics,
            "session_ids": synth["session_ids"],
        },
        run_dir / "manifest.json",
    )
    _to_json(metrics, run_dir / "metrics.json")

    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run a reproducible end-to-end experiment and snapshot results."
    )
    parser.add_argument("num_sessions", type=int, help="Number of synthetic sessions to generate")
    parser.add_argument(
        "--day",
        default=_now_utc().date().isoformat(),
        help="Anchor day in YYYY-MM-DD format (default: UTC today)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
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
    parser.add_argument(
        "--run-id",
        help="Optional run ID used as session prefix and result folder name",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        help="Optional EVSE registry CSV path to enrich synthetic sessions",
    )
    parser.add_argument(
        "--skip-figures",
        action="store_true",
        help="Skip chart generation and figure snapshot copying.",
    )
    parser.add_argument(
        "--publish-chain",
        action="store_true",
        help="Publish and verify the generated batch anchor on the configured blockchain.",
    )
    args = parser.parse_args()

    result = run_experiment(
        num_sessions=args.num_sessions,
        day=args.day,
        seed=args.seed,
        run_id=args.run_id,
        registry_path=args.registry,
        session_type=args.session_type,
        bidirectional_ratio=args.bidirectional_ratio,
        discharge_ratio=args.discharge_ratio,
        skip_figures=args.skip_figures,
        publish_chain=args.publish_chain,
    )
    print(json.dumps(result, indent=2, sort_keys=True))

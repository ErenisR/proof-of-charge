#!/usr/bin/env python3
"""Run the controlled, repeated Reviewer 3 performance benchmark."""

from __future__ import annotations

import argparse, csv, json, os, shutil, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from src import db
from src.performance_benchmark import (aggregate, collect_environment, randomized_execution_order,
                                       sha256_file, write_csv)
from src.performance_timing import TimingRecorder
from src.run_experiment import run_experiment


def utc_now() -> str: return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
def git(*args: str) -> str | None:
    try: return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except Exception: return None


def reset_database() -> None:
    session = db.session_scope()
    try:
        session.execute(text("TRUNCATE TABLE verifications, batch_anchor_receipts, batch_anchors, meter_values, receipts, sessions RESTART IDENTITY CASCADE"))
        session.commit()
        counts = session.execute(text("SELECT (SELECT count(*) FROM sessions), (SELECT count(*) FROM receipts), (SELECT count(*) FROM batch_anchors)" )).one()
        if any(counts): raise RuntimeError(f"Benchmark database reset failed: {tuple(counts)}")
    finally: session.close()


def database_counts() -> dict[str, int]:
    session = db.session_scope()
    try:
        row = session.execute(text("SELECT (SELECT count(*) FROM sessions), (SELECT count(*) FROM meter_values), (SELECT count(*) FROM receipts), (SELECT count(*) FROM batch_anchors), (SELECT count(*) FROM batch_anchor_receipts), (SELECT count(*) FROM verifications)" )).one()
        return dict(zip(("sessions","meter_values","receipts","batch_anchors","memberships","verifications"), map(int,row)))
    finally: session.close()


def postgres_server_version() -> str | None:
    session = db.session_scope()
    try: return str(session.execute(text("SHOW server_version")).scalar())
    except Exception: return None
    finally: session.close()


def make_figures(summary: list[dict], stage_summary: list[dict], output: Path) -> None:
    import matplotlib.pyplot as plt
    figures = output / "figures"; figures.mkdir(exist_ok=True)
    sizes = [r["workload_size"] for r in summary]
    def line(name, ylabel, ys, low=None, high=None):
        fig, ax = plt.subplots(figsize=(7,4)); errors = None
        if low: errors = [[y-l for y,l in zip(ys,low)], [h-y for y,h in zip(ys,high)]]
        ax.errorbar(sizes, ys, yerr=errors, marker="o", capsize=4); ax.set(xlabel="Reconciled receipts", ylabel=ylabel)
        ax.grid(alpha=.25); fig.tight_layout(); fig.savefig(figures/name, dpi=160); plt.close(fig)
    line("receipt_pipeline_ci95.png", "Mean per-receipt pipeline latency (ms)", [r["receipt_pipeline_mean_ms"] for r in summary], [r["receipt_pipeline_ci95_low_ms"] for r in summary], [r["receipt_pipeline_ci95_high_ms"] for r in summary])
    line("receipt_throughput_ci95.png", "Receipt throughput (receipts/s)", [r["throughput_mean_receipts_per_sec"] for r in summary], [r["throughput_ci95_low"] for r in summary], [r["throughput_ci95_high"] for r in summary])
    fig, ax = plt.subplots(figsize=(7,4))
    for field,label in [("receipt_pipeline_p50_ms","p50"),("receipt_pipeline_p95_ms","p95"),("receipt_pipeline_p99_ms","p99")]: ax.plot(sizes,[r[field] for r in summary],marker="o",label=label)
    ax.set(xlabel="Reconciled receipts",ylabel="Pooled per-receipt latency (ms)"); ax.legend(); ax.grid(alpha=.25); fig.tight_layout(); fig.savefig(figures/"receipt_pipeline_percentiles.png",dpi=160); plt.close(fig)
    stages = ["receipt_construction_total","database_persistence","meter_merkle_construction"]
    fig, ax = plt.subplots(figsize=(7,4)); width=.24
    for offset,stage in enumerate(stages):
        vals=[]
        for size in sizes:
            match=next((r for r in stage_summary if r["workload_size"]==size and r["stage"]==stage),None); vals.append(match["mean"]/size if match else 0)
        ax.bar([i+(offset-1)*width for i in range(len(sizes))],vals,width,label=stage)
    ax.set_xticks(range(len(sizes)),sizes); ax.set(xlabel="Reconciled receipts",ylabel="Mean per-receipt stage latency (ms)"); ax.legend(fontsize=7); fig.tight_layout(); fig.savefig(figures/"receipt_stage_separation.png",dpi=160); plt.close(fig)
    for filename, selected in [("batch_stages.png",["batch_merkle_construction","batch_verification_total"]),("blockchain_stages.png",["chain_send_command","chain_receipt_query","chain_read_verification"])]:
        fig,ax=plt.subplots(figsize=(7,4))
        for stage in selected:
            vals=[next((r["mean"] for r in stage_summary if r["workload_size"]==size and r["stage"]==stage),0) for size in sizes]; ax.plot(sizes,vals,marker="o",label=stage)
        ax.set(xlabel="Reconciled receipts",ylabel="Run-level stage time (ms)"); ax.legend(fontsize=8); ax.grid(alpha=.25); fig.tight_layout(); fig.savefig(figures/filename,dpi=160); plt.close(fig)


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--day",required=True); parser.add_argument("--sizes",nargs="+",type=int,required=True)
    parser.add_argument("--seeds",nargs="+",type=int,required=True); parser.add_argument("--session-type",default="all")
    parser.add_argument("--warmup-runs",type=int,default=1); parser.add_argument("--publish-chain",action="store_true")
    parser.add_argument("--reset-database-between-runs",action="store_true"); parser.add_argument("--orchestration-seed",type=int,default=20260801)
    parser.add_argument("--output-dir",type=Path,required=True); parser.add_argument("--benchmark-id",default="reviewer3_performance")
    args=parser.parse_args(); command=" ".join(sys.argv)
    if not db.database_enabled(): raise RuntimeError("PostgreSQL DATABASE_URL is required")
    if not args.reset_database_between_runs: raise RuntimeError("Primary benchmark requires --reset-database-between-runs")
    if len(args.seeds) < 2: raise RuntimeError("Repeated benchmark requires multiple seeds")
    output=args.output_dir.resolve(); output.mkdir(parents=True,exist_ok=False); os.environ["WRITE_LOCAL_RECEIPTS"]="0"
    sha=git("rev-parse","HEAD"); dirty=bool(git("status","--porcelain")); dbcheck=db.check_database()
    environment=collect_environment(command=command,source_sha=sha,dirty=dirty,migration_revision=dbcheck["current_revision"])
    environment["postgresql_client_version"] = environment.get("postgresql_version")
    environment["postgresql_version"] = postgres_server_version()
    environment["database_container_configuration"] = {
        "image": "postgres:16", "host_port": 5433, "container_port": 5432,
        "persistence": "named Docker volume postgres_data",
    }
    (output/"environment.json").write_text(json.dumps(environment,indent=2,sort_keys=True)+"\n")
    order=randomized_execution_order(args.sizes,args.seeds,args.orchestration_seed)
    write_csv(output/"execution_order.csv",[{"execution_index":i+1,"workload_size":s,"repetition":r,"seed":seed} for i,(s,r,seed) in enumerate(order)])
    observations=[]; runs=[]; warmups=[]
    jobs=[(s,warmup_index + 1,args.seeds[0],True) for s in args.sizes for warmup_index in range(args.warmup_runs)]+[(s,r,seed,False) for s,r,seed in order]
    for execution_index,(size,repetition,seed,warmup) in enumerate(jobs,1):
        reset_database(); before=database_counts()
        run_id=(f"{args.benchmark_id}_warmup_n{size:04d}_w{repetition:02d}"
                if warmup else f"{args.benchmark_id}_n{size:04d}_r{repetition:02d}_seed{seed}")
        existing=ROOT/"results"/run_id
        if existing.exists(): shutil.rmtree(existing)
        recorder=TimingRecorder(benchmark_id=args.benchmark_id,run_id=run_id,repetition_index=None if warmup else repetition,seed=seed,workload_size=size,session_mode=args.session_type)
        started=time.perf_counter_ns(); started_at=utc_now(); failure=None
        try: metrics=run_experiment(size,args.day,seed=seed,run_id=run_id,session_type=args.session_type,skip_figures=True,publish_chain=args.publish_chain,timing_recorder=recorder)
        except Exception as exc:
            failure=f"{type(exc).__name__}: {exc}"; metrics={}
        elapsed=time.perf_counter_ns()-started; after=database_counts()
        obs=recorder.as_dicts()
        for item in obs: item["warmup"]=warmup; item["duration_ms"]=item["duration_ns"]/1e6
        observations.extend(obs)
        pipeline=[o["duration_ns"] for o in obs if o["stage"]=="receipt_pipeline_total"]
        pipeline_total=sum(pipeline); valid=not failure and bool(metrics.get("count_reconciliation_ok")) and bool(metrics.get("batch_root_match")) and (not args.publish_chain or (metrics.get("chain_root_match") and metrics.get("chain_receipt_count_match") and metrics.get("chain_status")==1))
        row={"execution_index":execution_index,"run_id":run_id,"warmup":warmup,"workload_size":size,"repetition":repetition,"seed":seed,"started_at_utc":started_at,"ended_at_utc":utc_now(),"process_id":os.getpid(),"load_average":list(os.getloadavg()) if hasattr(os,"getloadavg") else None,"database_counts_before":before,"database_counts_after":after,"end_to_end_duration_ns":elapsed,"valid":valid,"failure":failure,"count_reconciliation_ok":bool(metrics.get("count_reconciliation_ok")),"batch_root_match":bool(metrics.get("batch_root_match")),"chain_root_match":bool(metrics.get("chain_root_match")) if args.publish_chain else None,"chain_receipt_count_match":bool(metrics.get("chain_receipt_count_match")) if args.publish_chain else None,"chain_status":metrics.get("chain_status"),"receipt_pipeline_total_ns":pipeline_total,"receipt_pipeline_mean_ms":pipeline_total/size/1e6 if size else 0,"receipt_throughput_per_sec":size/(pipeline_total/1e9) if pipeline_total else 0,"num_meter_values":after["meter_values"],"session_type_composition":metrics.get("session_type_counts"),"chain_gas_used":metrics.get("chain_gas_used"),"chain_transaction_fee_wei":metrics.get("chain_transaction_fee_wei"),"chain_tx":metrics.get("chain_tx"),"chain_block_number":metrics.get("chain_block_number")}
        (warmups if warmup else runs).append(row)
        write_csv(output/"warmup_runs.csv",warmups); write_csv(output/"measured_runs.csv",runs)
        if not valid: raise RuntimeError(f"Benchmark correctness gate failed for {run_id}: {failure or row}")
    receipt_stages={"meter_normalization","meter_leaf_encoding","meter_merkle_construction","receipt_energy_pricing_assembly","receipt_schema_validation","receipt_construction_total","receipt_canonical_hashing","receipt_validation","database_persistence","local_file_persistence","receipt_pipeline_total"}
    batch_stages={s for s in [o["stage"] for o in observations] if s.startswith("batch_")}; chain_stages={s for s in [o["stage"] for o in observations] if s.startswith("chain_")}
    write_csv(output/"receipt_stage_observations.csv",[o for o in observations if o["stage"] in receipt_stages]); write_csv(output/"batch_stage_observations.csv",[o for o in observations if o["stage"] in batch_stages]); write_csv(output/"blockchain_stage_observations.csv",[o for o in observations if o["stage"] in chain_stages]); write_csv(output/"run_level_metrics.csv",runs)
    summary,stage_summary=aggregate(observations,runs); write_csv(output/"summary_by_workload.csv",summary); write_csv(output/"summary_by_stage.csv",stage_summary); make_figures(summary,stage_summary,output)
    report=["# Reviewer 3 performance validation","",f"Executed {len(runs)} measured runs and {len(warmups)} warm-ups on one controlled local environment. No outliers were removed.","","| Workload | Runs | Pipeline mean ± SD (ms/receipt) | 95% CI | p50 / p95 / p99 (ms) | Throughput (receipts/s) |","|---:|---:|---:|---:|---:|---:|"]
    for r in summary: report.append(f"| {r['workload_size']} | {r['n_runs']} | {r['receipt_pipeline_mean_ms']:.3f} ± {r['receipt_pipeline_sd_ms']:.3f} | [{r['receipt_pipeline_ci95_low_ms']:.3f}, {r['receipt_pipeline_ci95_high_ms']:.3f}] | {r['receipt_pipeline_p50_ms']:.3f} / {r['receipt_pipeline_p95_ms']:.3f} / {r['receipt_pipeline_p99_ms']:.3f} | {r['throughput_mean_receipts_per_sec']:.2f} |")
    report += ["","Synthetic generation, export, reconciliation, and figure generation are excluded from primary receipt latency. Receipt construction, canonical hashing, validation, and actual per-receipt database persistence are separately observed. Confidence intervals use independent run-level means, sample SD (n−1), and Student-t critical values; percentiles use pooled receipt observations with linear interpolation on `(n−1)q`.","","Local Anvil uses automatic local mining. `chain_send_command` describes the installed `cast send` behavior and is not a public-network confirmation-time estimate. Repetitions quantify within-environment variation, not cross-hardware generalization or universal performance."]
    (output/"reviewer3_performance_report.md").write_text("\n".join(report)+"\n")
    files=[p for p in output.rglob("*") if p.is_file() and p.name!="benchmark_manifest.json"]
    manifest={"implementation_source_commit_sha":sha,"artifact_commit_sha":None,"dirty_worktree":dirty,"benchmark_command":command,"workload_sizes":args.sizes,"seeds":args.seeds,"repetitions_per_workload":len(args.seeds),"warmup_count_per_workload":args.warmup_runs,"orchestration_seed":args.orchestration_seed,"randomized_execution_order":[{"workload":s,"repetition":r,"seed":seed} for s,r,seed in order],"environment":environment,"expected_measured_runs":len(args.sizes)*len(args.seeds),"completed_measured_runs":len(runs),"failed_runs":0,"validation_status":"pass","artifact_sha256":{str(p.relative_to(output)):sha256_file(p) for p in files}}
    (output/"benchmark_manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n"); return 0

if __name__=="__main__": raise SystemExit(main())

#!/usr/bin/env python3
"""Focused PostgreSQL and in-memory validation of poc-batch-merkle-v1."""

from __future__ import annotations

import argparse, csv, hashlib, json, platform, random, subprocess, sys, time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from sqlalchemy import select, text

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from src import db
from src.batch_merkle import PROFILE_V1, BatchContext, BatchLeafRecord, DuplicateBatchReceiptHash, build_batch_commitment, generate_membership_proof, verify_membership_proof
from src.batch_service import audit_batch_membership
from src.models import BatchAnchor
from src.receipt_builder import build_receipt, hash_receipt
from src.repository import persist_finalized_session
from src.run_experiment import run_experiment
from src.synthetic_sessions import generate_session
from src.verifier_batch import verify_anchor_from_db

OUTPUT=ROOT/"results/reviewer3_batch_merkle"

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument("--day",default="2026-07-20"); p.add_argument("--seed",type=int,default=42); p.add_argument("--run-id"); p.add_argument("--output-dir",type=Path,default=OUTPUT); a=p.parse_args()
    run_id=a.run_id or f"reviewer3_batch_merkle_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    metrics=run_experiment(5,a.day,a.seed,run_id,session_type="all",skip_figures=True)
    s=db.session_scope(); cases=[]; proofs=[]
    try:
        anchor=s.scalar(select(BatchAnchor).where(BatchAnchor.day==a.day,BatchAnchor.session_prefix==run_id,BatchAnchor.commitment_profile==PROFILE_V1))
        verification=verify_anchor_from_db(anchor.id,s,False)
        for membership in anchor.receipts:
            start=time.perf_counter(); from src.batch_service import generate_anchor_membership_proof
            proof=generate_anchor_membership_proof(anchor.id,membership.session_id,s); gen=time.perf_counter()-start
            start=time.perf_counter(); valid=verify_membership_proof(proof); verify_time=time.perf_counter()-start
            proofs.append((membership.session_id,proof)); cases.append(case("proof",membership.session_id,valid,{"siblings":len(proof["siblings"]),"size_bytes":len(json.dumps(proof,separators=(",",":")).encode()),"generation_seconds":gen,"verification_seconds":verify_time}))
        modified=deepcopy(proofs[0][1]); modified["receipt_hash"]="0x"+"ff"*32
        cases.append(case("modified_proof_rejected",proofs[0][0],not verify_membership_proof(modified),{}))
        try:
            item=BatchLeafRecord("duplicate-a",f"{a.day}T00:00:00Z","0x"+"aa"*32)
            build_batch_commitment([item,BatchLeafRecord("duplicate-b",f"{a.day}T01:00:00Z",item.receipt_hash)],BatchContext.for_day(a.day)); duplicate_rejected=False
        except DuplicateBatchReceiptHash: duplicate_rejected=True
        cases.append(case("duplicate_receipt_hash_rejected",None,duplicate_rejected,{}))
        root_before=anchor.batch_root
        late=generate_session(6,rng=random.Random(a.seed+1000),target_day=a.day,session_prefix=run_id,deterministic_tx=True,session_mode="all")
        receipt=build_receipt(late); persist_finalized_session(late,receipt,hash_receipt(receipt),s); s.commit()
        audit=audit_batch_membership(anchor.id,s); historical=verify_anchor_from_db(anchor.id,s,False)
        cases.append(case("late_insertion_detected",late["session_id"],late["session_id"] in audit["late_or_unanchored_ids"],{"historical_root_unchanged":historical["computed_root"]==root_before,"snapshot_match":audit["membership_snapshot_match"]}))
        postgres_version=s.execute(text("show server_version")).scalar(); migration_revision=s.execute(text("select version_num from alembic_version")).scalar()
    finally:s.close()
    base=datetime.fromisoformat(a.day+"T00:00:00+00:00"); thousand=[BatchLeafRecord(f"proof-size-{i:04d}",(base+timedelta(seconds=i)).isoformat(),"0x"+hashlib.sha256(f"receipt-{i}".encode()).hexdigest()) for i in range(1000)]
    start=time.perf_counter(); large=build_batch_commitment(thousand,BatchContext.for_day(a.day)); build_time=time.perf_counter()-start
    start=time.perf_counter(); large_proof=generate_membership_proof(large,"proof-size-0500"); proof_gen=time.perf_counter()-start
    start=time.perf_counter(); large_valid=verify_membership_proof(large_proof); proof_verify=time.perf_counter()-start
    large_obs={"leaf_count":1000,"tree_depth":len(large.levels)-1,"proof_sibling_count":len(large_proof["siblings"]),"serialized_proof_size_bytes":len(json.dumps(large_proof,separators=(",",":")).encode()),"commitment_build_seconds":build_time,"proof_generation_seconds":proof_gen,"proof_verification_seconds":proof_verify,"proof_valid":large_valid}
    cases.append(case("1000_leaf_proof_sample","proof-size-0500",large_valid,large_obs))
    passed=all(row["passed"] for row in cases) and verification["match"] and all(verify_membership_proof(proof) for _,proof in proofs)
    summary={"profile":PROFILE_V1,"hash_algorithm":"sha-256","run_id":run_id,"day":a.day,"seed":a.seed,"anchor_id":anchor.id,"leaf_count":5,"batch_root":root_before,"temporal_ordering_match":verification["ordering_match"],"domain_separated_profile_match":verification["profile_match"],"proofs_generated":len(proofs),"proofs_verified":sum(verify_membership_proof(proof) for _,proof in proofs),"modified_proof_rejected":cases[5]["passed"],"duplicate_rejected":duplicate_rejected,"late_insertion_detected":late["session_id"] in audit["late_or_unanchored_ids"],"historical_root_unchanged_after_late_insertion":historical["computed_root"]==root_before,"membership_snapshot_match_after_late_insertion":audit["membership_snapshot_match"],"membership_audit":audit,"large_proof_sample":large_obs,"validation_passed":passed}
    out=a.output_dir.resolve(); proof_dir=out/"membership_proofs"; proof_dir.mkdir(parents=True,exist_ok=True)
    for sid,proof in proofs: write_json(proof_dir/f"{sid}.json",proof)
    write_json(proof_dir/"modified_invalid_proof.json",modified); write_json(out/"batch_merkle_summary.json",summary); write_csv(out/"batch_merkle_cases.csv",cases); (out/"batch_merkle_report.md").write_text(report(summary,cases)+"\n",encoding="utf-8")
    manifest={"source_commit_sha":cmd(["git","rev-parse","HEAD"]),"dirty_worktree_flag":bool(cmd(["git","status","--porcelain"])),"python_version":platform.python_version(),"postgresql_version":postgres_version,"operating_system":platform.platform(),"profile_identifier":PROFILE_V1,"hash_algorithm":"sha-256","migration_revision":migration_revision,"command_used":" ".join(sys.argv),"implementation_files":hashes([ROOT/"src/batch_merkle.py",ROOT/"src/batch_anchoring.py",ROOT/"src/verifier_batch.py",ROOT/"src/batch_service.py"]),"generated_artifacts":hashes([out/"batch_merkle_summary.json",out/"batch_merkle_cases.csv",out/"batch_merkle_report.md",*[path for path in sorted(proof_dir.glob("*.json"))]]),"validation_passed":passed}
    write_json(out/"manifest.json",manifest); print(json.dumps(summary,indent=2,sort_keys=True)); return 0 if passed else 1

def case(name,session_id,passed,details): return {"case":name,"session_id":session_id,"passed":passed,"details":json.dumps(details,sort_keys=True)}
def write_json(path,value): path.write_text(json.dumps(value,indent=2,sort_keys=True,default=str)+"\n",encoding="utf-8")
def write_csv(path,rows):
    with path.open("w",newline="",encoding="utf-8") as f: w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def hashes(paths): return {str(path.relative_to(ROOT)):{"sha256":sha(path)} for path in paths}
def cmd(parts): return subprocess.run(parts,check=True,capture_output=True,text=True).stdout.strip()
def report(s,cases):
    lines=["# Reviewer 3 batch-Merkle focused validation","",f"Profile: `{s['profile']}`",f"Batch root: `{s['batch_root']}`","","| Case | Session | Passed |","|---|---|---|"]+[f"| {r['case']} | {r['session_id'] or '—'} | {r['passed']} |" for r in cases]
    o=s["large_proof_sample"]; lines += ["","## 1000-leaf proof-size sample","",f"- Tree depth: {o['tree_depth']}",f"- Sibling hashes: {o['proof_sibling_count']}",f"- Serialized proof: {o['serialized_proof_size_bytes']} bytes",f"- Build: {o['commitment_build_seconds']:.6f} s",f"- Proof generation: {o['proof_generation_seconds']:.6f} s",f"- Proof verification: {o['proof_verification_seconds']:.6f} s","","These are focused diagnostic timings, not final performance claims. Late insertion was detected by current-state audit while the historical cryptographic root remained valid. A never-recorded physical event remains undetectable without an external authoritative register."]
    return "\n".join(lines)
if __name__=="__main__": raise SystemExit(main())

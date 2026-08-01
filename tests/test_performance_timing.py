from copy import deepcopy

import pytest

from src.batch_merkle import BatchContext, BatchLeafRecord, build_batch_commitment
from src.performance_benchmark import aggregate, percentile, randomized_execution_order, summarize
from src.performance_timing import STAGE_NAMES, TimingRecorder
from src.receipt_builder import build_receipt, hash_receipt


def sample_session():
    return {"session_id":"timed-1","user_id":"u","evse_id":"e","ocpp_tx_id":"tx",
            "start_ts":"2026-07-25T01:00:00Z","end_ts":"2026-07-25T02:00:00Z",
            "meter_values":[{"ts":"2026-07-25T01:00:00Z","import_kwh":1,"export_kwh":0},
                            {"ts":"2026-07-25T02:00:00Z","import_kwh":2,"export_kwh":0}],
            "pricing":{"currency":"EUR","components":[],"import_components":[],"export_components":[]}}


def test_timing_does_not_change_receipt_hash_or_meter_root():
    session=sample_session(); plain=build_receipt(deepcopy(session)); recorder=TimingRecorder(benchmark_id="b",run_id="r")
    timed=build_receipt(deepcopy(session),timing_recorder=recorder)
    assert timed==plain; assert hash_receipt(timed)==hash_receipt(plain); assert timed["merkle_root"]==plain["merkle_root"]
    stages={o.stage for o in recorder.observations()}
    assert {"meter_normalization","meter_leaf_encoding","meter_merkle_construction","receipt_energy_pricing_assembly","receipt_schema_validation","receipt_construction_total"} <= stages
    assert all(isinstance(o.duration_ns,int) and o.duration_ns>=0 for o in recorder.observations())


def test_timing_recorder_rejects_unknown_and_bad_duration():
    recorder=TimingRecorder(benchmark_id="b",run_id="r")
    with pytest.raises(ValueError): recorder.add_duration_ns("unknown",1)
    with pytest.raises(ValueError): recorder.add_duration_ns("receipt_pipeline_total",-1)


def test_timed_and_untimed_batch_roots_are_identical():
    records=[BatchLeafRecord("a","2026-07-25T00:00:00Z","0x"+"01"*32),BatchLeafRecord("b","2026-07-25T01:00:00Z","0x"+"02"*32)]
    assert build_batch_commitment(records,BatchContext.for_day("2026-07-25")).batch_root == build_batch_commitment(list(reversed(records)),BatchContext.for_day("2026-07-25")).batch_root


def test_random_order_is_deterministic_and_records_multiple_seeds():
    first=randomized_execution_order([10,50],[42,43],20260801)
    assert first==randomized_execution_order([10,50],[42,43],20260801)
    assert {seed for _,_,seed in first}=={42,43}


def test_sample_sd_student_t_ci_known_vector():
    result=summarize([1,2,3,4,5,6,7,8,9,10])
    assert result["sample_sd"]==pytest.approx(3.0276503541)
    assert result["ci95_low"]==pytest.approx(3.334149,abs=1e-6)
    assert result["ci95_high"]==pytest.approx(7.665851,abs=1e-6)


def test_percentile_linear_known_values():
    values=[1,2,3,4,5]
    assert percentile(values,.5)==3; assert percentile(values,.95)==pytest.approx(4.8); assert percentile(values,.99)==pytest.approx(4.96)


def test_aggregation_rejects_invalid_measured_run_and_excludes_warmup():
    obs=[{"run_id":"warm","workload_size":10,"stage":"receipt_pipeline_total","duration_ns":99,"warmup":True}]
    invalid={"run_id":"bad","workload_size":10,"valid":False,"warmup":False,"receipt_pipeline_mean_ms":1,"receipt_throughput_per_sec":100,"count_reconciliation_ok":False,"batch_root_match":True,"chain_root_match":True}
    with pytest.raises(RuntimeError): aggregate(obs,[invalid])


def test_frozen_stages_separate_generation_export_and_chain():
    assert {"synthetic_session_generation","receipt_pipeline_total","dataset_export","count_reconciliation","chain_send_command","chain_receipt_query","chain_read_verification"} <= STAGE_NAMES
    assert "synthetic_session_generation" != "receipt_pipeline_total"

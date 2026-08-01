import hashlib
from copy import deepcopy

import pytest

from src.batch_anchoring import build_batch_root
from src.batch_merkle import (
    CONTEXT_PREFIX, INTERNAL_PREFIX, LEAF_PREFIX, ROOT_PREFIX,
    BatchContext, BatchLeafRecord, DuplicateBatchReceiptHash,
    DuplicateBatchSession, InvalidBatchReceiptHash, build_batch_commitment,
    canonical_context_bytes, generate_membership_proof, hash_context,
    hash_internal, hash_leaf, verify_membership_proof,
)


def records(count=5):
    return [
        BatchLeafRecord(f"session-{index:04d}", f"2026-07-15T{index:02d}:00:00Z", "0x" + f"{index + 1:064x}")
        for index in range(count)
    ]


def test_exact_domain_separated_formulas_and_context_binding():
    context = BatchContext.for_day("2026-07-15"); record = records(1)[0]
    context_bytes = canonical_context_bytes(context)
    expected_context = hashlib.sha256(CONTEXT_PREFIX + len(context_bytes).to_bytes(4, "big") + context_bytes).digest()
    assert hash_context(context) == expected_context
    sid = record.session_id.encode(); ts = "2026-07-15T00:00:00.000000Z".encode(); receipt = bytes.fromhex(record.receipt_hash[2:])
    assert hash_leaf(record, expected_context) == hashlib.sha256(LEAF_PREFIX + expected_context + len(sid).to_bytes(4,"big") + sid + len(ts).to_bytes(4,"big") + ts + receipt).digest()
    leaf = hash_leaf(record, expected_context)
    assert hash_internal(leaf, leaf) == hashlib.sha256(INTERNAL_PREFIX + leaf + leaf).digest()
    commitment = build_batch_commitment([record], context)
    assert commitment.batch_root == hashlib.sha256(ROOT_PREFIX + expected_context + (1).to_bytes(8,"big") + leaf).digest()
    assert build_batch_commitment([record], BatchContext.for_day("2026-07-16")).batch_root != commitment.batch_root


@pytest.mark.parametrize("count", [1, 2, 3, 5])
def test_one_two_three_five_leaf_roots_and_all_proofs(count):
    commitment = build_batch_commitment(records(count), BatchContext.for_day("2026-07-15"))
    assert len(commitment.batch_root) == 32
    for record in commitment.records:
        proof = generate_membership_proof(commitment, record.session_id)
        assert verify_membership_proof(proof)
    if count in (3, 5):
        final = generate_membership_proof(commitment, commitment.records[-1].session_id)
        assert any(item["odd_duplicate"] for item in final["siblings"])


def test_temporal_order_ties_and_input_query_order():
    unordered = [
        BatchLeafRecord("z", "2026-07-15T02:00:00Z", "0x" + "01" * 32),
        BatchLeafRecord("b", "2026-07-15T01:00:00Z", "0x" + "ff" * 32),
        BatchLeafRecord("a", "2026-07-15T01:00:00Z", "0x" + "fe" * 32),
    ]
    first = build_batch_commitment(unordered, BatchContext.for_day("2026-07-15"))
    second = build_batch_commitment(list(reversed(unordered)), BatchContext.for_day("2026-07-15"))
    assert first.batch_root == second.batch_root
    assert [record.session_id for record in first.records] == ["a", "b", "z"]
    assert first.records[0].receipt_hash > first.records[2].receipt_hash  # not hash-only order


def test_duplicates_and_malformed_hashes_rejected():
    item = records(1)[0]
    with pytest.raises(DuplicateBatchSession):
        build_batch_commitment([item, BatchLeafRecord(item.session_id, "2026-07-15T01:00:00Z", "0x"+"02"*32)], BatchContext.for_day("2026-07-15"))
    with pytest.raises(DuplicateBatchReceiptHash):
        build_batch_commitment([item, BatchLeafRecord("other", "2026-07-15T01:00:00Z", item.receipt_hash)], BatchContext.for_day("2026-07-15"))
    with pytest.raises(InvalidBatchReceiptHash):
        build_batch_commitment([BatchLeafRecord("bad", "2026-07-15T00:00:00Z", "0x12")], BatchContext.for_day("2026-07-15"))


@pytest.mark.parametrize("field", ["session_id", "normalized_start_ts", "receipt_hash", "leaf_count", "context", "sibling"])
def test_modified_proofs_fail(field):
    commitment = build_batch_commitment(records(5), BatchContext.for_day("2026-07-15"))
    proof = generate_membership_proof(commitment, commitment.records[2].session_id)
    changed = deepcopy(proof)
    if field == "session_id": changed[field] = "changed"
    elif field == "normalized_start_ts": changed[field] = "2026-07-15T04:00:00.000000Z"
    elif field == "receipt_hash": changed[field] = "0x" + "aa" * 32
    elif field == "leaf_count": changed[field] += 1
    elif field == "context": changed[field]["batch_day"] = "2026-07-16"
    else: changed["siblings"][0]["hash"] = "0x" + "bb" * 32
    assert not verify_membership_proof(changed)


def test_new_profile_root_differs_from_legacy_and_legacy_is_stable():
    items = records(3)
    hashes = [item.receipt_hash for item in items]
    legacy = build_batch_root(hashes)
    assert legacy == build_batch_root(list(reversed(hashes)))
    assert "0x" + build_batch_commitment(items, BatchContext.for_day("2026-07-15")).batch_root.hex() != legacy

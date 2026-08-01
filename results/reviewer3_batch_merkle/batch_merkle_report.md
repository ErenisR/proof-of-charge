# Reviewer 3 batch-Merkle focused validation

Profile: `poc-batch-merkle-v1`
Batch root: `0x2914c44d6070d5500f5df5f5abe2ec20efcbb7bd5da8d6470b8fa275ae73c3e8`

| Case | Session | Passed |
|---|---|---|
| proof | reviewer3_batch_merkle_validation_20260801_v3-0001 | True |
| proof | reviewer3_batch_merkle_validation_20260801_v3-0003 | True |
| proof | reviewer3_batch_merkle_validation_20260801_v3-0004 | True |
| proof | reviewer3_batch_merkle_validation_20260801_v3-0002 | True |
| proof | reviewer3_batch_merkle_validation_20260801_v3-0005 | True |
| modified_proof_rejected | reviewer3_batch_merkle_validation_20260801_v3-0001 | True |
| duplicate_receipt_hash_rejected | — | True |
| late_insertion_detected | reviewer3_batch_merkle_validation_20260801_v3-0006 | True |
| 1000_leaf_proof_sample | proof-size-0500 | True |

## 1000-leaf proof-size sample

- Tree depth: 10
- Sibling hashes: 10
- Serialized proof: 2064 bytes
- Build: 0.009607 s
- Proof generation: 0.000060 s
- Proof verification: 0.000057 s

These are focused diagnostic timings, not final performance claims. Late insertion was detected by current-state audit while the historical cryptographic root remained valid. A never-recorded physical event remains undetectable without an external authoritative register.

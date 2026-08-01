# `poc-batch-merkle-v1`

## Context

The frozen compact UTF-8 JSON context uses sorted keys and no insignificant
whitespace:

```json
{"batch_day":"YYYY-MM-DD","eligibility_rule":"normalized receipt start_ts in [window_start, window_end)","hash_algorithm":"sha-256","odd_node_rule":"duplicate-last","ordering_rule":"start_ts, session_id, receipt_hash","profile":"poc-batch-merkle-v1","timezone":"UTC","window_end":"NEXT-DAYT00:00:00.000000Z","window_start":"YYYY-MM-DDT00:00:00.000000Z"}
```

The context contains no execution timestamp. The window is the half-open UTC
interval for `batch_day`. Session timestamps are normalized to
`YYYY-MM-DDTHH:MM:SS.ffffffZ`.

## Encoding and domain separation

`uint32_be` and `uint64_be` are unsigned big-endian integers. Receipt hashes must
decode from optional-`0x` hexadecimal to exactly 32 bytes. Session IDs are NFC
Unicode encoded as valid UTF-8. Prefix bytes are fixed:

```text
context_hash = SHA-256(
  0x02 || uint32_be(len(context_bytes)) || context_bytes
)

leaf_hash = SHA-256(
  0x00 || context_hash ||
  uint32_be(len(session_id_utf8)) || session_id_utf8 ||
  uint32_be(len(start_ts_utf8)) || start_ts_utf8 ||
  receipt_hash_32_bytes
)

internal_hash = SHA-256(
  0x01 || left_child_32_bytes || right_child_32_bytes
)

batch_root = SHA-256(
  0x03 || context_hash || uint64_be(leaf_count) || tree_root_32_bytes
)
```

The final root is 32 bytes and can be stored by the existing blockchain contract.
The contract does not expose context metadata directly; the root binds it through
`context_hash`.

## Ordering, duplicates, and odd nodes

Leaves sort by normalized UTC `start_ts`, then NFC `session_id` in Unicode
code-point order, then normalized receipt-hash bytes. Input query order is
irrelevant. Duplicate session IDs, receipt hashes, leaf encodings, or indices are
invalid. Missing or malformed hashes are invalid.

At every odd-width level the final node is paired with itself:

```text
SHA-256(0x01 || node || node)
```

A one-leaf tree uses its leaf hash as `tree_root` and still passes through the
context/count final wrapper.

## Proof format

Stable JSON proofs contain `profile`, complete `context`, `context_hash`,
`leaf_count`, `session_id`, `normalized_start_ts`, `receipt_hash`, `leaf_index`,
`leaf_hash`, ordered `siblings` (`hash`, `direction`, `odd_duplicate`), and
`expected_batch_root`. Verification requires no database access.

```bash
python -m src.batch_merkle proof --anchor-id 1 --session-id example-0001 --output proof.json
python -m src.batch_merkle verify-proof proof.json
```

API equivalents are `GET /v1/anchors/{anchor_id}/proofs/{session_id}` and
`POST /v1/anchors/proofs/verify`.

## Closure and compatibility

The first close stores an immutable membership snapshot and temporal leaf indices.
An identical re-close is idempotent. A differing eligible set is never silently
replaced. Explicit revision/correction is outside this profile.

Historical anchors lacking new metadata are interpreted as
`legacy-hash-sort-v0` and verified with their original algorithm. They are never
forced through this profile. New-profile file-backed anchoring fails clearly;
PostgreSQL is required so snapshot metadata cannot diverge between backends.

# Reviewer 3 Comment 4 — batch Merkle semantics

## Historical diagnosis

Historical anchors use profile `legacy-hash-sort-v0`. Receipt hashes are
normalized by removing `0x`, lowercasing hexadecimal characters, and sorting the
hashes lexicographically. The generic `src/merkle.py` then computes leaf nodes as
`SHA-256(raw_leaf_bytes)`, internal nodes as `SHA-256(left || right)`, and
duplicates the final node at every odd-width level.

That root does not explicitly commit to the batch day, UTC interval, ordering
policy, session ID, session start timestamp, profile, or receipt count.
Membership proofs were not exposed. Reconstructing the same stored membership
snapshot could therefore produce a matching root even when that snapshot was
incomplete relative to an external expected set. Historical roots and the generic
meter-stream Merkle helper remain unchanged.

## New behavior

New PostgreSQL anchors use `poc-batch-merkle-v1`, specified in
`docs/specifications/poc_batch_merkle_v1.md`. Leaves are temporally ordered,
domain-separated, and bind the frozen batch context, session ID, normalized start
timestamp, and receipt hash. The final wrapper binds the context and receipt
count. Duplicate sessions, receipt hashes, and leaf encodings are rejected.

The membership snapshot is immutable after closure. Re-closing an unchanged set
is idempotent; a changed eligible set raises `ClosedBatchMembershipChanged` with
exact additions, removals, and hash changes. `audit_batch_membership()` separately
compares the valid historical snapshot with current database state and detects
late insertions, removals, changed hashes, and changed timestamps.

## Omission semantics and limitation

- Removing or altering an entry already in the stored snapshot is detected by
  snapshot verification.
- A receipt inserted after closure is reported as late or unanchored by the
  current-state membership audit; it does not retroactively alter the historical
  root.
- A physical charging event that never entered the platform cannot be detected
  from a Merkle root. Production completeness requires an authoritative external
  session register or signed charger/CPO source.
- Experiments can detect omissions relative to their known requested set through
  exact count and ID reconciliation.

The application profile and its integration/audit semantics are the contribution;
the underlying Merkle-tree construction is standard.

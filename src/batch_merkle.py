"""Versioned, domain-separated batch Merkle commitments and proofs."""

from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

PROFILE_V1 = "poc-batch-merkle-v1"
LEGACY_PROFILE = "legacy-hash-sort-v0"
HASH_ALGORITHM = "sha-256"
LEAF_PREFIX = b"\x00"
INTERNAL_PREFIX = b"\x01"
CONTEXT_PREFIX = b"\x02"
ROOT_PREFIX = b"\x03"
ORDERING_RULE = "start_ts, session_id, receipt_hash"
ODD_NODE_RULE = "duplicate-last"
ELIGIBILITY_RULE = "normalized receipt start_ts in [window_start, window_end)"


class BatchMerkleError(ValueError): pass
class DuplicateBatchSession(BatchMerkleError): pass
class DuplicateBatchReceiptHash(BatchMerkleError): pass
class DuplicateBatchLeaf(BatchMerkleError): pass
class InvalidBatchReceiptHash(BatchMerkleError): pass
class ClosedBatchMembershipChanged(BatchMerkleError):
    def __init__(self, details: dict[str, Any]):
        self.details = details
        super().__init__(f"Closed batch membership changed: added={details.get('added_ids', [])}, removed={details.get('removed_ids', [])}, changed={details.get('changed_ids', [])}")


@dataclass(frozen=True)
class BatchContext:
    profile: str
    batch_day: str
    window_start: str
    window_end: str
    timezone: str = "UTC"
    eligibility_rule: str = ELIGIBILITY_RULE
    ordering_rule: str = ORDERING_RULE
    odd_node_rule: str = ODD_NODE_RULE
    hash_algorithm: str = HASH_ALGORITHM

    @classmethod
    def for_day(cls, day: str) -> "BatchContext":
        parsed = date.fromisoformat(day)
        start = datetime.combine(parsed, time.min, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        return cls(PROFILE_V1, day, normalize_timestamp(start), normalize_timestamp(end))

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "BatchContext":
        return cls(**value)


@dataclass(frozen=True)
class BatchLeafRecord:
    session_id: str
    start_ts: str
    receipt_hash: str


@dataclass
class BatchCommitment:
    context: BatchContext
    context_bytes: bytes
    context_hash: bytes
    records: list[BatchLeafRecord]
    leaf_hashes: list[bytes]
    levels: list[list[bytes]]
    tree_root: bytes
    batch_root: bytes

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.context.profile, "context": asdict(self.context),
            "context_hash": hex32(self.context_hash), "leaf_count": len(self.records),
            "tree_root": hex32(self.tree_root), "batch_root": hex32(self.batch_root),
            "records": [
                {**asdict(record), "leaf_index": index, "leaf_hash": hex32(self.leaf_hashes[index])}
                for index, record in enumerate(self.records)
            ],
        }


def canonical_context_bytes(context: BatchContext) -> bytes:
    validate_context(context)
    return json.dumps(asdict(context), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def hash_context(context: BatchContext) -> bytes:
    payload = canonical_context_bytes(context)
    return sha256(CONTEXT_PREFIX + u32(len(payload)) + payload)


def hash_leaf(record: BatchLeafRecord, context_hash: bytes) -> bytes:
    session = normalize_session_id(record.session_id).encode("utf-8")
    timestamp = normalize_timestamp(record.start_ts).encode("utf-8")
    receipt_hash = decode_hash(record.receipt_hash)
    return sha256(LEAF_PREFIX + context_hash + u32(len(session)) + session + u32(len(timestamp)) + timestamp + receipt_hash)


def hash_internal(left: bytes, right: bytes) -> bytes:
    require32(left, "left child"); require32(right, "right child")
    return sha256(INTERNAL_PREFIX + left + right)


def build_batch_commitment(records: Sequence[BatchLeafRecord], context: BatchContext) -> BatchCommitment:
    validate_context(context)
    if not records: raise BatchMerkleError("Cannot build an empty batch commitment")
    normalized = [BatchLeafRecord(normalize_session_id(r.session_id), normalize_timestamp(r.start_ts), normalize_hash(r.receipt_hash)) for r in records]
    _reject_duplicates(normalized)
    ordered = sorted(normalized, key=lambda r: (parse_timestamp(r.start_ts), tuple(map(ord, r.session_id)), decode_hash(r.receipt_hash)))
    context_bytes = canonical_context_bytes(context); context_hash = hash_context(context)
    leaves = [hash_leaf(record, context_hash) for record in ordered]
    if len(set(leaves)) != len(leaves): raise DuplicateBatchLeaf("Duplicate batch leaf encoding")
    levels = [leaves]
    layer = leaves
    while len(layer) > 1:
        next_layer = [hash_internal(layer[i], layer[i + 1] if i + 1 < len(layer) else layer[i]) for i in range(0, len(layer), 2)]
        levels.append(next_layer); layer = next_layer
    tree_root = layer[0]
    batch_root = sha256(ROOT_PREFIX + context_hash + len(leaves).to_bytes(8, "big") + tree_root)
    return BatchCommitment(context, context_bytes, context_hash, ordered, leaves, levels, tree_root, batch_root)


def generate_membership_proof(commitment: BatchCommitment, session_id: str) -> dict[str, Any]:
    matches = [i for i, record in enumerate(commitment.records) if record.session_id == session_id]
    if len(matches) != 1: raise BatchMerkleError(f"Session not uniquely present in commitment: {session_id}")
    index = matches[0]; cursor = index; siblings = []
    for level in commitment.levels[:-1]:
        if cursor % 2 == 0:
            sibling_index = cursor + 1 if cursor + 1 < len(level) else cursor
            direction = "right"; odd_duplicate = sibling_index == cursor
        else:
            sibling_index = cursor - 1; direction = "left"; odd_duplicate = False
        siblings.append({"hash": hex32(level[sibling_index]), "direction": direction, "odd_duplicate": odd_duplicate})
        cursor //= 2
    record = commitment.records[index]
    return {
        "profile": commitment.context.profile, "context": asdict(commitment.context),
        "context_hash": hex32(commitment.context_hash), "leaf_count": len(commitment.records),
        "session_id": record.session_id, "normalized_start_ts": record.start_ts,
        "receipt_hash": record.receipt_hash, "leaf_index": index,
        "leaf_hash": hex32(commitment.leaf_hashes[index]), "siblings": siblings,
        "expected_batch_root": hex32(commitment.batch_root),
    }


def verify_membership_proof(proof: dict[str, Any], expected_root: str | None = None) -> bool:
    try:
        if proof.get("profile") != PROFILE_V1: return False
        context = BatchContext.from_dict(proof["context"])
        context_hash = hash_context(context)
        if hex32(context_hash) != proof["context_hash"]: return False
        record = BatchLeafRecord(proof["session_id"], proof["normalized_start_ts"], proof["receipt_hash"])
        node = hash_leaf(record, context_hash)
        if hex32(node) != proof["leaf_hash"]: return False
        cursor = int(proof["leaf_index"]); width = int(proof["leaf_count"])
        if cursor < 0 or cursor >= width or width < 1: return False
        for sibling in proof["siblings"]:
            sibling_hash = decode_hash(sibling["hash"])
            expected_odd = cursor % 2 == 0 and cursor + 1 >= width
            if bool(sibling.get("odd_duplicate")) != expected_odd: return False
            if expected_odd and sibling_hash != node: return False
            if sibling["direction"] == "left": node = hash_internal(sibling_hash, node)
            elif sibling["direction"] == "right": node = hash_internal(node, sibling_hash)
            else: return False
            cursor //= 2; width = (width + 1) // 2
        root = sha256(ROOT_PREFIX + context_hash + int(proof["leaf_count"]).to_bytes(8, "big") + node)
        return hex32(root) == normalize_hash(expected_root or proof["expected_batch_root"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def validate_context(context: BatchContext) -> None:
    if context.profile != PROFILE_V1: raise BatchMerkleError(f"Unknown batch profile: {context.profile}")
    if context.timezone != "UTC" or context.ordering_rule != ORDERING_RULE or context.odd_node_rule != ODD_NODE_RULE or context.hash_algorithm != HASH_ALGORITHM or context.eligibility_rule != ELIGIBILITY_RULE: raise BatchMerkleError("Batch context does not match frozen profile rules")
    expected = BatchContext.for_day(context.batch_day)
    if context.window_start != expected.window_start or context.window_end != expected.window_end: raise BatchMerkleError("Batch context window does not match batch_day")


def normalize_timestamp(value: str | datetime) -> str:
    parsed = value if isinstance(value, datetime) else parse_timestamp(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None: raise BatchMerkleError("Naive batch timestamp is not allowed")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str): raise BatchMerkleError("Batch timestamp must be a string")
    raw = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try: parsed = datetime.fromisoformat(raw)
    except ValueError as exc: raise BatchMerkleError(f"Invalid batch timestamp: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None: raise BatchMerkleError("Naive batch timestamp is not allowed")
    return parsed.astimezone(timezone.utc)


def normalize_session_id(value: str) -> str:
    if not isinstance(value, str): raise BatchMerkleError("session_id must be a string")
    try: normalized = unicodedata.normalize("NFC", value); normalized.encode("utf-8")
    except UnicodeError as exc: raise BatchMerkleError("session_id is not valid UTF-8") from exc
    return normalized


def normalize_hash(value: str) -> str: return "0x" + decode_hash(value).hex()
def decode_hash(value: str) -> bytes:
    if not isinstance(value, str): raise InvalidBatchReceiptHash("Receipt hash must be a string")
    raw = value[2:] if value.startswith("0x") else value
    if len(raw) != 64:
        raise InvalidBatchReceiptHash("Receipt hash must decode to exactly 32 bytes")
    try: result = bytes.fromhex(raw)
    except ValueError as exc: raise InvalidBatchReceiptHash("Receipt hash must be hexadecimal") from exc
    return result
def require32(value: bytes, name: str) -> None:
    if len(value) != 32: raise BatchMerkleError(f"{name} must be 32 bytes")
def sha256(value: bytes) -> bytes: return hashlib.sha256(value).digest()
def u32(value: int) -> bytes: return value.to_bytes(4, "big", signed=False)
def hex32(value: bytes) -> str: require32(value, "hash"); return "0x" + value.hex()


def _reject_duplicates(records: list[BatchLeafRecord]) -> None:
    sessions = set(); hashes = set()
    for record in records:
        if record.session_id in sessions: raise DuplicateBatchSession(f"Duplicate session ID: {record.session_id}")
        if record.receipt_hash in hashes: raise DuplicateBatchReceiptHash(f"Duplicate receipt hash: {record.receipt_hash}")
        sessions.add(record.session_id); hashes.add(record.receipt_hash)


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch Merkle membership proofs")
    sub = parser.add_subparsers(dest="command", required=True)
    proof = sub.add_parser("proof"); proof.add_argument("--anchor-id", type=int, required=True); proof.add_argument("--session-id", required=True); proof.add_argument("--output", type=Path)
    verify = sub.add_parser("verify-proof"); verify.add_argument("proof_json", type=Path)
    args = parser.parse_args()
    if args.command == "proof":
        from .batch_service import generate_anchor_membership_proof
        result = generate_anchor_membership_proof(args.anchor_id, args.session_id)
        text = json.dumps(result, indent=2, sort_keys=True)
        if args.output: args.output.write_text(text + "\n", encoding="utf-8")
        else: print(text)
        return 0
    payload = json.loads(args.proof_json.read_text(encoding="utf-8")); valid = verify_membership_proof(payload)
    print(json.dumps({"valid": valid})); return 0 if valid else 1


if __name__ == "__main__": raise SystemExit(main())

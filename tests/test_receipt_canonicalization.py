import json
import math
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from src.receipt_builder import hash_receipt
from src.receipt_canonicalization import (
    CANONICALIZATION_PROFILE_V1,
    CanonicalizationError,
    canonicalize_receipt,
    hash_canonical_receipt,
    parse_json_strict,
)

ROOT = Path(__file__).resolve().parents[1]
VECTORS = json.loads((ROOT / "tests/fixtures/canonicalization_vectors.json").read_text(encoding="utf-8"))


def _receipt():
    return deepcopy(VECTORS["vectors"][0]["receipt"])


def test_python_conformance_vector_exact_bytes_and_hash():
    vector = VECTORS["vectors"][0]
    canonical = canonicalize_receipt(vector["receipt"], VECTORS["profile"])
    assert canonical == vector["canonical_text"].encode("utf-8")
    assert hash_canonical_receipt(vector["receipt"], VECTORS["profile"]) == vector["sha256"]
    assert not canonical.startswith(b"\xef\xbb\xbf")
    assert not canonical.endswith(b"\n")
    assert b" " not in canonical


def test_javascript_matches_python_bytes_and_hash(tmp_path):
    vector = VECTORS["vectors"][0]
    input_path = tmp_path / "receipt.json"
    input_path.write_text(json.dumps(vector["receipt"], ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run(
        ["node", str(ROOT / "scripts/receipt_canonicalization.mjs"), str(input_path)],
        check=True, capture_output=True, text=True,
    )
    javascript = json.loads(completed.stdout)
    assert javascript["canonical_text"] == vector["canonical_text"]
    assert javascript["hash"] == vector["sha256"]


def test_equivalent_offsets_unicode_and_object_order_produce_same_hash():
    first = _receipt()
    second = {key: deepcopy(first[key]) for key in reversed(first)}
    second["start_ts"] = "2026-07-15T08:00:00.000000Z"
    second["session_id"] = "caf\u00e9-session"
    assert canonicalize_receipt(first, CANONICALIZATION_PROFILE_V1) == canonicalize_receipt(second, CANONICALIZATION_PROFILE_V1)


def test_array_order_is_semantically_significant():
    first = _receipt()
    second = deepcopy(first)
    second["pricing"]["import_components"].reverse()
    assert hash_canonical_receipt(first, CANONICALIZATION_PROFILE_V1) != hash_canonical_receipt(second, CANONICALIZATION_PROFILE_V1)


def test_negative_zero_and_half_up_fixed_scale():
    text = canonicalize_receipt(_receipt(), CANONICALIZATION_PROFILE_V1).decode("utf-8")
    assert '"gross_export_credit":"0.000"' in text
    assert '"energy_kwh":"1.235"' in text
    assert '"price_per_kwh":"0.126"' in text


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, "NaN", "Infinity"])
def test_non_finite_numbers_fail_closed(value):
    receipt = _receipt()
    receipt["energy_kwh"] = value
    with pytest.raises(CanonicalizationError, match="finite|decimal"):
        canonicalize_receipt(receipt, CANONICALIZATION_PROFILE_V1)


def test_absent_null_extra_naive_and_unknown_profile_fail_closed():
    absent = _receipt(); absent.pop("session_id")
    null = _receipt(); null["user_id"] = None
    extra = _receipt(); extra["new_field"] = "would change profile semantics"
    naive = _receipt(); naive["start_ts"] = "2026-07-15T08:00:00"
    for receipt in (absent, null, extra, naive):
        with pytest.raises(CanonicalizationError):
            canonicalize_receipt(receipt, CANONICALIZATION_PROFILE_V1)
    with pytest.raises(CanonicalizationError, match="Unknown"):
        canonicalize_receipt(_receipt(), "future-profile")


def test_strict_json_parser_rejects_duplicates_non_finite_and_bom():
    with pytest.raises(CanonicalizationError, match="Duplicate"):
        parse_json_strict('{"a":1,"a":2}')
    with pytest.raises(CanonicalizationError, match="Non-finite"):
        parse_json_strict('{"a":NaN}')
    with pytest.raises(CanonicalizationError, match="BOM"):
        parse_json_strict(b'\xef\xbb\xbf{}')


def test_legacy_receipt_hash_remains_exact_and_profile_dispatches():
    legacy = _receipt()
    legacy.pop("canonicalization_profile")
    legacy.pop("hash_algorithm")
    assert hash_receipt(legacy) == "0x323b3ee3097965d439990f62cd27fc324389a1a48326e35295569a322bd7ad56"
    assert hash_receipt(_receipt()) == VECTORS["vectors"][0]["sha256"]

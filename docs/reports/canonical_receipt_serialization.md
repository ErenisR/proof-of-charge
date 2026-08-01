# Canonical receipt serialization profile `poc-c14n-v1`

## Scope and identifiers

This document normatively defines the bytes hashed for new Proof-of-Charge
receipts using:

```text
canonicalization_profile = poc-c14n-v1
hash_algorithm           = sha-256
external hash format     = 0x + 64 lowercase hexadecimal digits
```

`schema_version` remains `v2g-v1` because the receipt's domain meaning is
unchanged. The profile identifies serialization semantics independently from the
domain schema.

Historical receipts without `canonicalization_profile` retain the original
Python serialization and hash behavior (`json.dumps(receipt, sort_keys=True)`
encoded as UTF-8). Verification selects that legacy path only when the marker is
absent. A profiled receipt is never silently interpreted as legacy. An unknown
profile fails closed.

## Frozen receipt structure

Every `poc-c14n-v1` receipt contains exactly these top-level fields:

```text
canonicalization_profile
end_ts
energy_kwh
energy_summary
evse_id
hash_algorithm
merkle_root
ocpp_tx_id
pricing
schema_version
session_id
session_type
settlement
start_ts
stream_hash_alg
user_id
version
```

Nested structures are exact:

- `energy_summary`: `import_kwh`, `export_kwh`, `net_kwh`.
- `pricing`: `currency`, `model`, `components`, `import_components`,
  `export_components`.
- Each pricing component: `from`, `to`, `price_per_kwh`.
- `settlement`: `currency`, `gross_import_cost`, `gross_export_credit`,
  `net_amount`.

All listed fields are required. No field is nullable in this profile. The three
pricing component fields are arrays and must be encoded as `[]` when empty.
Absent, explicit `null`, and empty array are distinct; absent and null are
rejected where an array is required. Extra fields are rejected because adding a
field would change the frozen profile. A future field requires a new profile.

## Canonical transformation

1. Validate the exact structure above before serialization.
2. Normalize every object key and string value to Unicode NFC. Case and leading
   or trailing characters are preserved. Identifiers are never trimmed.
   Normalization collisions and lone UTF-16 surrogates are rejected.
3. Sort object fields recursively in ascending Unicode code-point order. Preserve
   array order exactly; arrays are never sorted.
4. Normalize timestamps and fixed-scale decimals as specified below.
5. Serialize as RFC 8259 JSON with lowercase `true`, `false`, and `null` literals
   (although this profile has no nullable field), standard JSON string escaping,
   no insignificant whitespace, no trailing newline, and no byte-order mark.
6. Encode the resulting text as UTF-8.
7. SHA-256 hashes exactly those bytes.

The Python `parse_json_strict()` helper rejects duplicate object keys, non-finite
JSON constants, and a UTF-8 BOM. Callers that begin from a parsed mapping must use
a JSON parser with duplicate-key rejection because duplicates cannot be detected
after ordinary JSON parsing has discarded them.

## Decimal rules

Canonical JSON represents all energy, price, and monetary quantities as JSON
strings with exactly three digits after the decimal point:

| Category | Fields | Scale |
|---|---|---:|
| Energy | `energy_kwh`; all `energy_summary` values | 3 |
| Price | every `price_per_kwh` | 3 |
| Money | all numeric `settlement` values | 3 |

Profiled receipt fields must be decimal strings; JSON numeric tokens are rejected
because a parser may already have rounded them through binary floating point.
Receipt builders may accept application-level numbers but must convert them from
their decimal input spelling to strings before constructing the profiled receipt.
Rounding is decimal `ROUND_HALF_UP` to three places. Negative zero becomes
`"0.000"`. NaN, positive infinity, and negative infinity are rejected. No other
numeric field is defined by this profile.

Examples:

```text
1.2345  -> "1.235"
0.1255  -> "0.126"
-0.0    -> "0.000"
2       -> "2.000"
```

## Timestamp rules

`start_ts`, `end_ts`, and pricing component `from` and `to` values must be RFC
3339 strings with an explicit `Z` or `±HH:MM` offset. Naive timestamps are
rejected. Fractional input precision may contain zero through six digits.

Canonical output is UTC with exactly six fractional digits and `Z`:

```text
YYYY-MM-DDTHH:MM:SS.ffffffZ
```

Therefore `2026-07-15T10:00:00+02:00` and `2026-07-15T08:00:00Z`
both become `2026-07-15T08:00:00.000000Z`.

## APIs and reference implementations

Python:

```python
canonicalize_receipt(receipt, "poc-c14n-v1") -> bytes
hash_canonical_receipt(receipt, "poc-c14n-v1") -> "0x..."
```

Implementation: `src/receipt_canonicalization.py`.

JavaScript:

```javascript
canonicalizeReceipt(receipt, "poc-c14n-v1") // Buffer
hashCanonicalReceipt(receipt, "poc-c14n-v1") // "0x..."
```

Implementation: `scripts/receipt_canonicalization.mjs`. Run its CLI with:

```bash
node scripts/receipt_canonicalization.mjs receipt.json
```

Shared frozen vectors are in `tests/fixtures/canonicalization_vectors.json`.
Automated tests execute both implementations and compare exact canonical text
and SHA-256 hashes.

## Evolution and backward verification

- `schema_version` selects domain meaning.
- `canonicalization_profile` selects byte construction.
- `hash_algorithm` identifies the digest algorithm.
- The field set and transformation rules of `poc-c14n-v1` are immutable.
- New fields, scales, timestamp precision, nullability, or algorithms require a
  new profile identifier and new conformance vectors.
- Historical profile-less receipts continue to verify using the legacy serializer.
- Known profiled receipts use only their named rules.
- Unknown profiles and algorithms fail closed.

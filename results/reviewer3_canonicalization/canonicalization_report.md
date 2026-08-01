# Canonical receipt cross-language validation

Profile: `poc-c14n-v1`  
Hash algorithm: `sha-256`  
Executed: `2026-08-01T10:39:30.817431Z`

## Experimental result

| Vector | Bytes | Python hash | JavaScript hash | Bytes identical | Hash identical | Status |
|---|---:|---|---|---|---|---|
| offset-unicode-rounding-array-order | 932 | `0x091ee8ca80f9beb9f99b86f43abb33b5c97dc919f313b93d40f935169f2500eb` | `0x091ee8ca80f9beb9f99b86f43abb33b5c97dc919f313b93d40f935169f2500eb` | True | True | pass |

Overall validation: **PASS**.

The Python and JavaScript implementations were both executed against the frozen input in `tests/fixtures/canonicalization_vectors.json`. The comparison covers the exact UTF-8 byte sequence and the resulting SHA-256 digest; it is not inferred from source inspection.

## Scope

The vector exercises recursive object ordering, semantically significant array order, NFC Unicode normalization, equivalent timestamp offsets, fixed six-digit UTC timestamps, decimal ROUND_HALF_UP behavior, fixed energy/price/money scales, and negative-zero normalization. Automated pytest cases separately exercise rejection of nulls, missing and extra fields, naive timestamps, non-finite values, duplicate JSON keys, and unknown profiles.

This validates conformance for the committed profile and vectors. It is not a claim that arbitrary third-party implementations conform without running the same vectors.

# Reviewer 3 performance validation

Executed 50 measured runs and 5 warm-ups on one controlled local environment. No outliers were removed.

| Workload | Runs | Pipeline mean ± SD (ms/receipt) | 95% CI | p50 / p95 / p99 (ms) | Throughput (receipts/s) |
|---:|---:|---:|---:|---:|---:|
| 10 | 10 | 7.766 ± 0.566 | [7.362, 8.171] | 7.704 / 9.973 / 11.032 | 129.41 |
| 50 | 10 | 7.152 ± 0.793 | [6.585, 7.719] | 6.825 / 9.420 / 11.329 | 141.23 |
| 100 | 10 | 7.341 ± 0.655 | [6.873, 7.810] | 7.024 / 9.635 / 11.381 | 137.17 |
| 500 | 10 | 7.198 ± 0.382 | [6.925, 7.471] | 6.693 / 10.695 / 13.839 | 139.28 |
| 1000 | 10 | 7.853 ± 0.331 | [7.616, 8.090] | 7.135 / 12.033 / 15.076 | 127.54 |

Synthetic generation, export, reconciliation, and figure generation are excluded from primary receipt latency. Receipt construction, canonical hashing, validation, and actual per-receipt database persistence are separately observed. Confidence intervals use independent run-level means, sample SD (n−1), and Student-t critical values; percentiles use pooled receipt observations with linear interpolation on `(n−1)q`.

Local Anvil uses automatic local mining. `chain_send_command` describes the installed `cast send` behavior and is not a public-network confirmation-time estimate. Repetitions quantify within-environment variation, not cross-hardware generalization or universal performance.

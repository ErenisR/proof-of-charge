# Blockchain Demo Report - 2026-06-12

Source metrics:

- `results/chain_smoke_20260612/metrics.json`

## Demo Scope

This run demonstrates the current end-to-end Proof-of-Charge blockchain path:

```text
synthetic sessions -> deterministic receipts -> Postgres batch anchor ->
local Anvil transaction -> on-chain batch-root verification
```

## Commands

```bash
docker compose up -d postgres anvil
python3 -m src.db init
python3 scripts/deploy_anchor.py
python3 -m src.run_experiment 2 --day 2026-06-12 --seed 42 --run-id chain_smoke_20260612 --skip-figures --publish-chain
python3 -m src.blockchain.verifier 2026-06-12 --prefix chain_smoke_20260612
```

## Results

| Metric | Value |
| --- | ---: |
| Sessions requested | 2 |
| Sessions anchored | 2 |
| Receipt count verified | 2 |
| Meter values exported | 18 |
| Total finalization time | 0.159982 s |
| Average finalization time | 0.079991 s |
| Batch root matched DB recomputation | true |
| On-chain root matched DB anchor | true |

## Blockchain Metrics

| Metric | Value |
| --- | ---: |
| Chain ID | 31337 |
| Block number | 3 |
| Gas used | 142,675 |
| Effective gas price | 771,713,212 wei |
| Effective gas price | 0.771713212 gwei |
| Transaction fee | 110,104,182,522,100 wei |
| Transaction fee | 0.0001101041825221 ETH |
| Transaction status | 1 |

Transaction hash:

```text
0x376bc2d57f850e73ed5a1a279b4333d312357ea9a23a32cfeb2857afc38bf29e
```

Batch root:

```text
0x7a635c918099f10c2ac0ed1347278badfbc04300f270f9410e89dae1f149b88d
```

## Interpretation

The demo anchors one daily Merkle batch root on-chain rather than anchoring each
receipt individually. This keeps blockchain cost independent of receipt count
for a given batch, while Postgres keeps the full receipt data, batch membership
snapshot, and verification history.

The current implementation is local-chain only. The next research step is to run
the same publish/verify flow on a public testnet and compare gas/cost behavior.


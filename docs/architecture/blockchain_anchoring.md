# Blockchain Anchoring

Planned approach:

1. Compute and persist receipt hashes in Postgres.
2. Build Merkle batch roots from finalized receipts.
3. Anchor only the batch root on-chain.
4. Store the transaction hash in `batch_anchors.chain_tx`.
5. Verify by comparing the recomputed database batch root with the on-chain root.

Initial contract:

- `contracts/src/ProofOfChargeAnchor.sol`
- `anchorBatch(day, sessionPrefix, batchRoot, receiptCount)`
- `getAnchor(day, sessionPrefix)`
- `BatchAnchored(day, sessionPrefix, batchRoot, receiptCount, operator, timestamp)`

Local deployment:

1. Run `anvil` or `docker compose up -d anvil`.
2. Run `python3 scripts/deploy_anchor.py`.
3. Copy the printed `ANCHOR_CONTRACT_ADDRESS` into `.env`.
4. Publish a DB anchor with `python3 -m src.blockchain.publisher <day>`.
5. Verify it with `python3 -m src.blockchain.verifier <day>`.

The Docker Compose Anvil service is local-development infrastructure. It does
not persist chain state by default; redeploy the contract after recreating the
container.

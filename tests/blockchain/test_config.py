from src.blockchain.config import load_blockchain_config


def test_load_blockchain_config_uses_local_defaults(monkeypatch):
    monkeypatch.delenv("WEB3_RPC_URL", raising=False)
    monkeypatch.delenv("ANCHOR_CONTRACT_ADDRESS", raising=False)
    monkeypatch.delenv("ANCHOR_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("CHAIN_ID", raising=False)

    config = load_blockchain_config()

    assert config.rpc_url == "http://127.0.0.1:8545"
    assert config.contract_address is None
    assert config.private_key is None
    assert config.chain_id == 31337


def test_load_blockchain_config_reads_environment(monkeypatch):
    monkeypatch.setenv("WEB3_RPC_URL", "http://localhost:9545")
    monkeypatch.setenv("ANCHOR_CONTRACT_ADDRESS", "0x0000000000000000000000000000000000000001")
    monkeypatch.setenv("ANCHOR_PRIVATE_KEY", "0xabc")
    monkeypatch.setenv("CHAIN_ID", "11155111")

    config = load_blockchain_config()

    assert config.rpc_url == "http://localhost:9545"
    assert config.contract_address == "0x0000000000000000000000000000000000000001"
    assert config.private_key == "0xabc"
    assert config.chain_id == 11155111

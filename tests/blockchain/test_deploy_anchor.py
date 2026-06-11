from scripts.deploy_anchor import parse_deployed_address


def test_parse_deployed_address():
    output = """
    Compiler run successful!
    Deployer: 0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266
    Deployed to: 0x5fbdb2315678afecb367f032d93f642f64180aa3
    Transaction hash: 0xabc
    """

    assert parse_deployed_address(output) == "0x5fbdb2315678afecb367f032d93f642f64180aa3"

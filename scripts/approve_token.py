import os
import sys
from decimal import Decimal
from web3 import Web3
from py_clob_client.config import get_contract_config

# Auto-load .env in the script directory if present
from pathlib import Path
ENV_PATH = Path(__file__).resolve().parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if not line or line.strip().startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

ERC20_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "remaining", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function",
    },
]

def resolve_spender(chain_id: int, override: str | None) -> str | None:
    if override:
        return Web3.to_checksum_address(override)
    try:
        cfg = get_contract_config(chain_id)
        return Web3.to_checksum_address(cfg.exchange)
    except Exception:
        return None

def main():
    rpc_url = os.getenv("RPC_URL")
    private_key = os.getenv("PRIVATE_KEY")
    token_address = os.getenv("TOKEN_ADDRESS")
    approve_amount = os.getenv("APPROVE_AMOUNT", "max")
    chain_id = int(os.getenv("CHAIN_ID", "137"))
    neg_risk = os.getenv("NEG_RISK", "0").lower() in {"1", "true", "yes"}
    spender_env = os.getenv("SPENDER_ADDRESS")

    spender = resolve_spender(chain_id, spender_env)

    if not all([rpc_url, private_key, token_address, spender]):
        print("Missing env. Need RPC_URL, PRIVATE_KEY, TOKEN_ADDRESS, and SPENDER_ADDRESS (or detectable via py_clob_client config)")
        sys.exit(1)

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    account = w3.eth.account.from_key(private_key)

    try:
        token = w3.eth.contract(address=token_address, abi=ERC20_ABI)
        decimals = token.functions.decimals().call()
        symbol = token.functions.symbol().call()
    except Exception as e:
        print(f"Token contract error: {e}")
        sys.exit(1)

    if approve_amount.lower() == "max":
        amount_wei = 2 ** 256 - 1
    else:
        amount_wei = int(Decimal(approve_amount) * (10 ** decimals))

    # Show current balance/allowance
    balance = token.functions.balanceOf(account.address).call()
    allowance = token.functions.allowance(account.address, spender).call()
    human_balance = Decimal(balance) / (10 ** decimals)
    human_allowance = Decimal(allowance) / (10 ** decimals)
    print(f"Token: {symbol}, decimals: {decimals}")
    print(f"Address: {account.address}")
    print(f"ChainId: {chain_id} (neg_risk={neg_risk})")
    print(f"Spender: {spender}")
    print(f"Balance: {human_balance} {symbol}")
    print(f"Allowance to spender: {human_allowance} {symbol}")

    nonce = w3.eth.get_transaction_count(account.address)

    try:
        tx = token.functions.approve(spender, amount_wei).build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "gasPrice": w3.eth.gas_price,
            }
        )
        # Estimate gas
        gas_est = w3.eth.estimate_gas(tx)
        tx["gas"] = int(gas_est * 1.2)
        # Set chain id
        tx["chainId"] = w3.eth.chain_id
        signed = account.sign_transaction(tx)
        raw_tx = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction", None)
        if not raw_tx:
            raise RuntimeError("SignedTransaction missing rawTransaction attribute")
        tx_hash = w3.eth.send_raw_transaction(raw_tx)
        print(f"Submitted approve for {symbol}: {tx_hash.hex()}")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        print(f"Mined in block {receipt.blockNumber}, status {receipt.status}")
    except Exception as e:
        print(f"Approve failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

"""
One-time script: approve USDC.e spending for Polymarket exchange contracts
via Gnosis Safe execTransaction on the proxy wallet.
"""
import sys
from web3 import Web3
from eth_account import Account
from eth_abi import encode

import config as cfg

RPC = cfg.POLYGON_RPC_URL or "https://polygon-rpc.com"
w3 = Web3(Web3.HTTPProvider(RPC))

proxy_addr = Web3.to_checksum_address(cfg.FUNDER_ADDRESS or cfg.WALLET_ADDRESS)
acct = Account.from_key(cfg.PRIVATE_KEY)
print(f"EOA signer: {acct.address}")
print(f"Proxy wallet: {proxy_addr}")

USDC = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
MAX_UINT = 2**256 - 1

SPENDERS = [
    ("CTF Exchange", "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"),
    ("NegRisk CTF Exchange", "0xC5d563A36AE78145C45a50134d48A1215220f80a"),
    ("NegRisk Adapter", "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"),
]

# ERC20 approve(address,uint256) selector = 0x095ea7b3
def encode_approve(spender: str) -> bytes:
    selector = bytes.fromhex("095ea7b3")
    args = encode(["address", "uint256"], [Web3.to_checksum_address(spender), MAX_UINT])
    return selector + args

# Gnosis Safe execTransaction ABI
SAFE_ABI = [
    {
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
            {"name": "operation", "type": "uint8"},
            {"name": "safeTxGas", "type": "uint256"},
            {"name": "baseGas", "type": "uint256"},
            {"name": "gasPrice", "type": "uint256"},
            {"name": "gasToken", "type": "address"},
            {"name": "refundReceiver", "type": "address"},
            {"name": "signatures", "type": "bytes"},
        ],
        "name": "execTransaction",
        "outputs": [{"name": "success", "type": "bool"}],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "nonce",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "domainSeparator",
        "outputs": [{"name": "", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
            {"name": "operation", "type": "uint8"},
            {"name": "safeTxGas", "type": "uint256"},
            {"name": "baseGas", "type": "uint256"},
            {"name": "gasPrice", "type": "uint256"},
            {"name": "gasToken", "type": "address"},
            {"name": "refundReceiver", "type": "address"},
            {"name": "_nonce", "type": "uint256"},
        ],
        "name": "getTransactionHash",
        "outputs": [{"name": "", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# Check allowance ABI
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    }
]

safe = w3.eth.contract(address=proxy_addr, abi=SAFE_ABI)
usdc_contract = w3.eth.contract(address=USDC, abi=ERC20_ABI)

ZERO_ADDR = "0x0000000000000000000000000000000000000000"

for name, spender in SPENDERS:
    spender_cs = Web3.to_checksum_address(spender)
    allowance = usdc_contract.functions.allowance(proxy_addr, spender_cs).call()
    if allowance >= 10**18:
        print(f"  {name}: allowance already set ({allowance}), skipping")
        continue

    print(f"  {name}: allowance={allowance}, approving...")

    call_data = encode_approve(spender)
    nonce = safe.functions.nonce().call()

    # Get the Safe transaction hash for signing
    tx_hash = safe.functions.getTransactionHash(
        USDC,       # to
        0,          # value
        call_data,  # data
        0,          # operation (CALL)
        0,          # safeTxGas
        0,          # baseGas
        0,          # gasPrice
        ZERO_ADDR,  # gasToken
        ZERO_ADDR,  # refundReceiver
        nonce,      # _nonce
    ).call()

    # Gnosis Safe expects raw ECDSA signature over the tx hash (no EIP-191 prefix)
    import eth_keys
    pk = eth_keys.keys.PrivateKey(bytes(acct.key))
    raw_sig = pk.sign_msg_hash(tx_hash)
    # Gnosis Safe v (add 27 to recovery id)
    v = raw_sig.v + 27
    sig = raw_sig.r.to_bytes(32, 'big') + raw_sig.s.to_bytes(32, 'big') + v.to_bytes(1, 'big')

    # Build the on-chain execTransaction call
    tx = safe.functions.execTransaction(
        USDC,       # to
        0,          # value
        call_data,  # data
        0,          # operation
        0,          # safeTxGas
        0,          # baseGas
        0,          # gasPrice
        ZERO_ADDR,  # gasToken
        ZERO_ADDR,  # refundReceiver
        sig,        # signatures
    ).build_transaction({
        'from': acct.address,
        'nonce': w3.eth.get_transaction_count(acct.address),
        'gas': 150000,
        'gasPrice': w3.eth.gas_price,
        'chainId': 137,
    })

    signed_tx = acct.sign_transaction(tx)
    tx_hash_sent = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    print(f"  TX sent: {tx_hash_sent.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash_sent, timeout=60)
    print(f"  TX confirmed: status={receipt['status']} (1=success)")

    # Verify
    new_allowance = usdc_contract.functions.allowance(proxy_addr, spender_cs).call()
    print(f"  New allowance: {new_allowance}")

print("\nDone! All allowances set.")

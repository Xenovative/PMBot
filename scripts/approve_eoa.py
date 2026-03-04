"""
EOA wallet approval script for Polymarket.
Sets:
  1. USDC.e ERC-20 approve(spender, MAX) for CTF Exchange, NegRisk CTF Exchange, NegRisk Adapter
  2. CTF ERC-1155 setApprovalForAll(operator, true) for CTF Exchange and NegRisk CTF Exchange

Run once per EOA wallet (or after changing wallets).
Usage:
  python approve_eoa.py
Reads from .env in the same directory or environment variables:
  PRIVATE_KEY, POLYGON_RPC_URL (optional, defaults to public RPC)
"""
import os
import sys
from pathlib import Path

# Load .env from script directory
ENV_PATH = Path(__file__).resolve().parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from web3 import Web3

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()
RPC = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com").strip()
CHAIN_ID = 137

if not PRIVATE_KEY:
    print("ERROR: PRIVATE_KEY not set in environment or .env")
    sys.exit(1)

w3 = Web3(Web3.HTTPProvider(RPC))
if not w3.is_connected():
    print(f"ERROR: Cannot connect to RPC {RPC}")
    sys.exit(1)

acct = w3.eth.account.from_key(PRIVATE_KEY)
wallet = acct.address
print(f"EOA wallet: {wallet}")
print(f"RPC: {RPC}")

USDC     = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
CTF      = Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")  # ConditionalTokens

SPENDERS = [
    ("CTF Exchange",        "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"),
    ("NegRisk CTF Exchange","0xC5d563A36AE78145C45a50134d48A1215220f80a"),
    ("NegRisk Adapter",     "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"),
]

CTF_OPERATORS = [
    ("CTF Exchange",        "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"),
    ("NegRisk CTF Exchange","0xC5d563A36AE78145C45a50134d48A1215220f80a"),
]

MAX_UINT = 2**256 - 1

ERC20_ABI = [
    {"name": "approve",   "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"name": "allowance", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
]

ERC1155_ABI = [
    {"name": "setApprovalForAll", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}],
     "outputs": []},
    {"name": "isApprovedForAll", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}, {"name": "operator", "type": "address"}],
     "outputs": [{"name": "", "type": "bool"}]},
]

usdc_contract = w3.eth.contract(address=USDC, abi=ERC20_ABI)
ctf_contract  = w3.eth.contract(address=CTF,  abi=ERC1155_ABI)


def send_tx(tx, label):
    tx["nonce"] = w3.eth.get_transaction_count(wallet)
    tx["chainId"] = CHAIN_ID
    tx["gasPrice"] = w3.eth.gas_price
    try:
        gas = w3.eth.estimate_gas(tx)
        tx["gas"] = int(gas * 1.3)
    except Exception as e:
        tx["gas"] = 100_000
        print(f"  ⚠️  gas estimate failed ({e}), using 100k")
    signed = acct.sign_transaction(tx)
    raw = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction", None)
    tx_hash = w3.eth.send_raw_transaction(raw)
    print(f"  📤 {label} tx: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
    status = "✅ success" if receipt.status == 1 else "❌ failed"
    print(f"  {status} (block {receipt.blockNumber}, gas {receipt.gasUsed})")
    return receipt.status == 1


print("\n── USDC ERC-20 approvals ──")
for name, spender in SPENDERS:
    spender_cs = Web3.to_checksum_address(spender)
    allowance = usdc_contract.functions.allowance(wallet, spender_cs).call()
    if allowance >= 10**18:
        print(f"  {name}: already approved ({allowance}), skipping")
        continue
    print(f"  {name}: allowance={allowance}, approving max...")
    tx = usdc_contract.functions.approve(spender_cs, MAX_UINT).build_transaction({"from": wallet})
    send_tx(tx, f"USDC approve → {name}")

print("\n── CTF ERC-1155 setApprovalForAll ──")
for name, operator in CTF_OPERATORS:
    op_cs = Web3.to_checksum_address(operator)
    approved = ctf_contract.functions.isApprovedForAll(wallet, op_cs).call()
    if approved:
        print(f"  {name}: already approved, skipping")
        continue
    print(f"  {name}: not approved, setting...")
    tx = ctf_contract.functions.setApprovalForAll(op_cs, True).build_transaction({"from": wallet})
    send_tx(tx, f"setApprovalForAll → {name}")

print("\n✅ Done! All approvals set.")

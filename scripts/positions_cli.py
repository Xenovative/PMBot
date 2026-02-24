import os
import sys
from decimal import Decimal
from typing import Dict, Tuple

from web3 import Web3
from py_clob_client.config import get_contract_config
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderType, PostOrdersArgs

# Auto-load .env in this directory if present
from pathlib import Path
ENV_PATH = Path(__file__).resolve().parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if not line or line.strip().startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

# Env
RPC_URL = os.getenv("RPC_URL", "https://polygon-bor.publicnode.com")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
ADDRESS = os.getenv("ADDRESS")
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))
START_BLOCK = int(os.getenv("START_BLOCK", "0"))  # bump to speed up
END_BLOCK = os.getenv("END_BLOCK")
TX_HASHES = os.getenv("TX_HASHES", "")  # comma-separated tx hashes to inspect directly
ACTION = os.getenv("ACTION", "").lower()  # "transfer" to send ERC1155
TRANSFER_TO = os.getenv("TRANSFER_TO", "")
TOKEN_ID_ENV = os.getenv("TOKEN_ID", "")
AMOUNT_ENV = os.getenv("AMOUNT", "")  # base units (1e8 per share) for transfer; shares for sell
PRICE_ENV = os.getenv("PRICE", "")    # optional limit price for sell (if empty, market price)

if not PRIVATE_KEY and not ADDRESS:
    print("Set PRIVATE_KEY (or ADDRESS if read-only).")
    sys.exit(1)

w3 = Web3(Web3.HTTPProvider(RPC_URL))

addr = Web3.to_checksum_address(
    ADDRESS if ADDRESS else w3.eth.account.from_key(PRIVATE_KEY).address
)

cfg = get_contract_config(CHAIN_ID)
USDCe = Web3.to_checksum_address(cfg.collateral)
EXCHANGE = Web3.to_checksum_address(cfg.exchange)
CONDITIONAL = Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")

ERC20_ABI = [
    {"name": "balanceOf", "inputs": [{"name": "", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"name": "allowance", "inputs": [{"name": "", "type": "address"}, {"name": "", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"name": "decimals", "inputs": [], "outputs": [{"name": "", "type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"name": "symbol", "inputs": [], "outputs": [{"name": "", "type": "string"}], "stateMutability": "view", "type": "function"},
]

ERC1155_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "operator", "type": "address"},
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "id", "type": "uint256"},
            {"indexed": False, "name": "value", "type": "uint256"},
        ],
        "name": "TransferSingle",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "operator", "type": "address"},
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "ids", "type": "uint256[]"},
            {"indexed": False, "name": "values", "type": "uint256[]"},
        ],
        "name": "TransferBatch",
        "type": "event",
    },
    {
        "inputs": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "id", "type": "uint256"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
        ],
        "name": "safeTransferFrom",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


def show_usdce():
    token = w3.eth.contract(address=USDCe, abi=ERC20_ABI)
    dec = token.functions.decimals().call()
    sym = token.functions.symbol().call()
    bal = token.functions.balanceOf(addr).call()
    allow = token.functions.allowance(addr, EXCHANGE).call()
    print(f"Token: {sym} ({USDCe})")
    print(f"Address: {addr}")
    print(f"Balance: {Decimal(bal) / (10 ** dec)} {sym}")
    print(f"Allowance to exchange {EXCHANGE}: {Decimal(allow) / (10 ** dec)} {sym}\n")


def fetch_positions() -> Dict[int, int]:
    contract = w3.eth.contract(address=CONDITIONAL, abi=ERC1155_ABI)
    # Direct path: inspect provided tx hashes to avoid large log scans
    if TX_HASHES.strip():
        balances: Dict[int, int] = {}
        for h in [t.strip() for t in TX_HASHES.split(",") if t.strip()]:
            try:
                receipt = w3.eth.get_transaction_receipt(h)
                for lg in receipt.logs:
                    if lg.address.lower() != CONDITIONAL.lower():
                        continue
                    try:
                        evt = contract.events.TransferSingle().process_log(lg)
                        tid = evt["args"]["id"]
                        val = evt["args"]["value"]
                        if evt["args"]["from"].lower() == addr.lower():
                            balances[tid] = balances.get(tid, 0) - val
                        if evt["args"]["to"].lower() == addr.lower():
                            balances[tid] = balances.get(tid, 0) + val
                        continue
                    except Exception:
                        pass
                    try:
                        evt = contract.events.TransferBatch().process_log(lg)
                        frm = evt["args"]["from"].lower()
                        to = evt["args"]["to"].lower()
                        for tid, val in zip(evt["args"]["ids"], evt["args"]["values"]):
                            if frm == addr.lower():
                                balances[tid] = balances.get(tid, 0) - val
                            if to == addr.lower():
                                balances[tid] = balances.get(tid, 0) + val
                    except Exception:
                        pass
            except Exception as e:
                print(f"  tx {h} failed: {e}")
        return {k: v for k, v in balances.items() if v > 0}

    latest = w3.eth.block_number
    stop_block = int(END_BLOCK) if END_BLOCK else latest
    step = 1_000  # smaller to avoid >10k log limit
    from_block = START_BLOCK or max(stop_block - 200_000, 0)  # last ~200k blocks by default
    balances: Dict[int, int] = {}

    def apply_single(log):
        data = contract.events.TransferSingle().process_log(log)
        tid = data["args"]["id"]
        val = data["args"]["value"]
        if data["args"]["from"].lower() == addr.lower():
            balances[tid] = balances.get(tid, 0) - val
        if data["args"]["to"].lower() == addr.lower():
            balances[tid] = balances.get(tid, 0) + val

    def apply_batch(log):
        data = contract.events.TransferBatch().process_log(log)
        ids = data["args"]["ids"]
        vals = data["args"]["values"]
        frm = data["args"]["from"].lower()
        to = data["args"]["to"].lower()
        for tid, val in zip(ids, vals):
            if frm == addr.lower():
                balances[tid] = balances.get(tid, 0) - val
            if to == addr.lower():
                balances[tid] = balances.get(tid, 0) + val

    # Topic hashes for events (avoid missing .signature in older web3 builds)
    single_topic = Web3.keccak(text="TransferSingle(address,address,address,uint256,uint256)")
    batch_topic = Web3.keccak(text="TransferBatch(address,address,address,uint256[],uint256[])")

    print(f"Scanning ERC1155 transfers for {addr} from block {from_block} to {stop_block}...")

    def process_range(start: int, end: int, chunk: int):
        nonlocal step
        fb = start
        while fb <= end:
            tb = min(fb + chunk, end)
            try:
                logs = w3.eth.get_logs({
                    "fromBlock": fb,
                    "toBlock": tb,
                    "address": CONDITIONAL,
                    "topics": [
                        [single_topic, batch_topic],
                        None,
                        None,
                        None,
                    ],
                })
                for lg in logs:
                    if lg["topics"][0] == single_topic:
                        apply_single(lg)
                    else:
                        apply_batch(lg)
            except Exception as e:
                msg = str(e)
                if "more than 10000 results" in msg.lower() and chunk > 50:
                    mid = (fb + tb) // 2
                    process_range(fb, mid, max(chunk // 2, 50))
                    process_range(mid + 1, tb, max(chunk // 2, 50))
                else:
                    print(f"  chunk {fb}-{tb} failed: {msg}")
            fb = tb + 1

    process_range(from_block, stop_block, step)
    return {k: v for k, v in balances.items() if v > 0}


def transfer_position():
    if not PRIVATE_KEY:
        print("TRANSFER requires PRIVATE_KEY.")
        sys.exit(1)
    if not TRANSFER_TO or not TOKEN_ID_ENV or not AMOUNT_ENV:
        print("Set TRANSFER_TO, TOKEN_ID, AMOUNT (base units, 1e8 per share).")
        sys.exit(1)

    to_addr = Web3.to_checksum_address(TRANSFER_TO)
    token_id = int(TOKEN_ID_ENV)
    amount = int(AMOUNT_ENV)

    contract = w3.eth.contract(address=CONDITIONAL, abi=ERC1155_ABI)
    acct = w3.eth.account.from_key(PRIVATE_KEY)

    print(f"Transferring token {token_id} amount {amount} from {acct.address} to {to_addr}")
    nonce = w3.eth.get_transaction_count(acct.address)
    tx = contract.functions.safeTransferFrom(
        acct.address, to_addr, token_id, amount, b""
    ).build_transaction(
        {
            "from": acct.address,
            "nonce": nonce,
            "gasPrice": w3.eth.gas_price,
        }
    )
    gas_est = w3.eth.estimate_gas(tx)
    tx["gas"] = int(gas_est * 1.2)
    tx["chainId"] = w3.eth.chain_id
    signed = acct.sign_transaction(tx)
    raw_tx = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction", None)
    if not raw_tx:
        raise RuntimeError("SignedTransaction missing rawTransaction")
    tx_hash = w3.eth.send_raw_transaction(raw_tx)
    print(f"Submitted transfer: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Mined in block {receipt.blockNumber}, status {receipt.status}")


def sell_position():
    if not PRIVATE_KEY:
        print("SELL requires PRIVATE_KEY.")
        sys.exit(1)
    if not TOKEN_ID_ENV:
        print("Set TOKEN_ID (the ERC1155 token id to sell).")
        sys.exit(1)

    token_id = str(int(TOKEN_ID_ENV))
    # AMOUNT_ENV for sell is in shares (float), not base units
    amount_shares = float(AMOUNT_ENV) if AMOUNT_ENV else None
    price = float(PRICE_ENV) if PRICE_ENV else None

    client = ClobClient(
        "https://clob.polymarket.com",
        key=PRIVATE_KEY,
        chain_id=CHAIN_ID,
        signature_type=int(os.getenv("SIGNATURE_TYPE", "0")),
        funder=addr,
    )

    # Level 2 creds
    client.set_api_creds(client.create_or_derive_api_creds())

    # If amount not provided, fetch positions and use full size
    if amount_shares is None:
        positions = fetch_positions()
        amt_base = positions.get(int(token_id), 0)
        if amt_base <= 0:
            print(f"No position found for token {token_id} to sell.")
            return
        amount_shares = amt_base / 1e8

    print(f"Submitting IOC SELL for token {token_id}, amount {amount_shares} shares, price={price or 'market'}")
    order = client.create_market_order(
        MarketOrderArgs(
            token_id=token_id,
            side="SELL",
            amount=amount_shares,
            price=price,  # None => market
            order_type=OrderType.FOK,  # IOC not in this lib; use FOK
            taker="0x0000000000000000000000000000000000000000",
            fee_rate_bps=0,
        )
    )
    resp = client.post_orders(
        [PostOrdersArgs(order=order, orderType=OrderType.FOK, postOnly=False)]
    )
    print("Post response:")
    print(resp)


def main():
    if ACTION == "transfer":
        transfer_position()
        return
    if ACTION == "sell":
        sell_position()
        return

    show_usdce()
    balances = fetch_positions()
    if not balances:
        print("No ERC1155 positions found in scanned range.")
    else:
        print("Positions (token_id -> amount):")
        for tid, amt in sorted(balances.items()):
            print(f"  {tid}: {amt}")


if __name__ == "__main__":
    main()

from web3 import Web3

RPC = "https://polygon-mainnet.infura.io/v3/e88f8f27c873493bb0d3914d591354ed"
PRIVATE_KEY = "93c769b032b2f2e9e8c1d86beedb6332a91379942f18206b527beef806d54bce"  # controller/owner EOA
PROXY = Web3.to_checksum_address("0x00990F89f97b7A75b2F25d3e7301998D9C21D406")
USDC = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
SPENDER = Web3.to_checksum_address("0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E")
AMOUNT = (1 << 256) - 1  # max

w3 = Web3(Web3.HTTPProvider(RPC))
acct = w3.eth.account.from_key(PRIVATE_KEY)
controller = acct.address

usdc_abi = [
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
]
proxy_abi = [
    {"name": "execute", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "data", "type": "bytes"}],
     "outputs": []},
]

usdc = w3.eth.contract(address=USDC, abi=usdc_abi)
proxy = w3.eth.contract(address=PROXY, abi=proxy_abi)

# Encode USDC.approve(spender, amount)
calldata = usdc.functions.approve(SPENDER, AMOUNT).build_transaction({"from": PROXY})["data"]

# Build proxy.execute(to=USDC, data=calldata)
tx = proxy.functions.execute(USDC, calldata).build_transaction({
    "from": controller,
    "nonce": w3.eth.get_transaction_count(controller),
    "gas": 0,  # placeholder
    "maxFeePerGas": None,
    "maxPriorityFeePerGas": None,
    "chainId": 137,
})

gas_est = proxy.functions.execute(USDC, calldata).estimate_gas({"from": controller})
tx["gas"] = gas_est
tx["gasPrice"] = w3.eth.gas_price
tx.pop("maxFeePerGas", None)
tx.pop("maxPriorityFeePerGas", None)

signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
print("tx:", tx_hash.hex())
rcpt = w3.eth.wait_for_transaction_receipt(tx_hash)
print("status:", rcpt.status, "gas used:", rcpt.gasUsed)
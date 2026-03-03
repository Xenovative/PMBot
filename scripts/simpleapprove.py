from web3 import Web3

rpc = "https://polygon-mainnet.infura.io/v3/e88f8f27c873493bb0d3914d591354ed"
usdc = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
# Proxy smart wallet address (allowance target)
proxy_owner = Web3.to_checksum_address("0x00990F89f97b7A75b2F25d3e7301998D9C21D406")
spender = Web3.to_checksum_address("0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E")

w3 = Web3(Web3.HTTPProvider(rpc))
abi = [
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
]

acct = w3.eth.account.from_key(PRIVATE_KEY)
controller_addr = Web3.to_checksum_address(acct.address)
print("Controller (signer) address:", controller_addr)
print("Proxy target:", proxy_owner)

c = w3.eth.contract(address=usdc, abi=abi)
dec = c.functions.decimals().call()

def show_allowances(label, owner_addr):
    allow = c.functions.allowance(owner_addr, spender).call()
    print(f"{label} allowance -> {allow / (10 ** dec)}")

show_allowances("Before: controller", controller_addr)
if controller_addr.lower() != proxy_owner.lower():
    show_allowances("Before: proxy   ", proxy_owner)

# Send approve from controller address (sets allowance for controller, not proxy)
amount = (1 << 256) - 1
tx = c.functions.approve(spender, amount).build_transaction({
    "from": controller_addr,
    "nonce": w3.eth.get_transaction_count(controller_addr),
    "gas": 0,  # placeholder, will be replaced
    "maxFeePerGas": None,
    "maxPriorityFeePerGas": None,
    "chainId": 137,
})

# Estimate gas and gas price
gas_est = c.functions.approve(spender, amount).estimate_gas({"from": controller_addr})
fee = w3.eth.gas_price
tx["gas"] = gas_est
tx["gasPrice"] = fee
tx.pop("maxFeePerGas", None)
tx.pop("maxPriorityFeePerGas", None)

signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)

print("Sent approve tx:", tx_hash.hex())
receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
print("Status:", receipt.status, "Gas used:", receipt.gasUsed)

show_allowances("After: controller", controller_addr)
if controller_addr.lower() != proxy_owner.lower():
    show_allowances("After: proxy   ", proxy_owner)
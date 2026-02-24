from web3 import Web3
rpc = "https://polygon-mainnet.infura.io/v3/e88f8f27c873493bb0d3914d591354ed"
usdc = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
owner = Web3.to_checksum_address("0xf439c2373e192bd805f4a0864847b2782e4507a0")
spender = Web3.to_checksum_address("0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E")
w3 = Web3(Web3.HTTPProvider(rpc))
abi = [
    {"constant":True,"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}
]
c = w3.eth.contract(address=usdc, abi=abi)
dec = c.functions.decimals().call()
allow = c.functions.allowance(owner, spender).call()
print("allowance", allow / (10 ** dec))
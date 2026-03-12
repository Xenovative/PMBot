"""
CTF (Conditional Token Framework) Merge functionality for Polymarket
https://docs.polymarket.com/developers/CTF/merge

Merging allows you to convert equal amounts of YES and NO tokens back to USDC.
This is useful when you have a hedged position (both sides filled).
"""

import logging
from typing import Optional
from web3 import Web3
from eth_account import Account

from config import PRIVATE_KEY, FUNDER_ADDRESS, CHAIN_ID

logger = logging.getLogger(__name__)

# Polygon RPC endpoints
POLYGON_RPC = "https://polygon-rpc.com"

# Polymarket CTF Contract on Polygon
CTF_CONTRACT_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# USDC on Polygon
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# CTF ABI - only the mergePositions function we need
CTF_ABI = [
    {
        "inputs": [
            {"internalType": "contract IERC20", "name": "collateralToken", "type": "address"},
            {"internalType": "bytes32", "name": "parentCollectionId", "type": "bytes32"},
            {"internalType": "bytes32", "name": "conditionId", "type": "bytes32"},
            {"internalType": "uint256[]", "name": "partition", "type": "uint256[]"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"}
        ],
        "name": "mergePositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]


class CTFMerger:
    """Handles merging of conditional tokens back to USDC"""
    
    def __init__(self):
        self.w3: Optional[Web3] = None
        self.account = None
        self.ctf_contract = None
        self._initialized = False
    
    def initialize(self) -> bool:
        """Initialize web3 connection and account"""
        if self._initialized:
            return True
        
        if not PRIVATE_KEY:
            logger.error("PRIVATE_KEY not set")
            return False
        
        try:
            self.w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
            
            if not self.w3.is_connected():
                logger.error("Failed to connect to Polygon RPC")
                return False
            
            # Setup account
            private_key = PRIVATE_KEY if PRIVATE_KEY.startswith('0x') else f'0x{PRIVATE_KEY}'
            self.account = Account.from_key(private_key)
            
            # Setup CTF contract
            self.ctf_contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(CTF_CONTRACT_ADDRESS),
                abi=CTF_ABI
            )
            
            logger.info(f"CTF Merger initialized. Account: {self.account.address}")
            self._initialized = True
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize CTF Merger: {e}")
            return False
    
    def merge_positions(
        self,
        condition_id: str,
        amount: int,  # Amount in smallest unit (6 decimals for USDC)
    ) -> Optional[str]:
        """
        Merge YES and NO tokens back to USDC.
        
        Args:
            condition_id: The condition ID of the market (bytes32 hex string)
            amount: Amount to merge (in USDC smallest unit, 6 decimals)
        
        Returns:
            Transaction hash if successful, None otherwise
        """
        if not self.initialize():
            return None
        
        try:
            # Parent collection ID is null (0x0) for Polymarket
            parent_collection_id = bytes(32)
            
            # Partition for binary market: [1, 2] represents YES and NO
            partition = [1, 2]
            
            # Convert condition_id to bytes32
            if condition_id.startswith('0x'):
                condition_id_bytes = bytes.fromhex(condition_id[2:])
            else:
                condition_id_bytes = bytes.fromhex(condition_id)
            
            # Build transaction - must use account address (the key we're signing with)
            sender = self.account.address
            
            tx = self.ctf_contract.functions.mergePositions(
                Web3.to_checksum_address(USDC_ADDRESS),
                parent_collection_id,
                condition_id_bytes,
                partition,
                amount
            ).build_transaction({
                'from': Web3.to_checksum_address(sender),
                'nonce': self.w3.eth.get_transaction_count(Web3.to_checksum_address(sender)),
                'gas': 200000,
                'gasPrice': self.w3.eth.gas_price,
                'chainId': CHAIN_ID
            })
            
            # Sign and send transaction
            signed_tx = self.w3.eth.account.sign_transaction(tx, self.account.key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            
            logger.info(f"Merge transaction sent: {tx_hash.hex()}")
            
            # Wait for confirmation
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            
            if receipt['status'] == 1:
                logger.info(f"Merge successful! TX: {tx_hash.hex()}")
                return tx_hash.hex()
            else:
                logger.error(f"Merge transaction failed: {tx_hash.hex()}")
                return None
                
        except Exception as e:
            logger.error(f"Failed to merge positions: {e}", exc_info=True)
            return None


# Singleton instance
_merger: Optional[CTFMerger] = None

def get_merger() -> CTFMerger:
    """Get or create the CTF merger instance"""
    global _merger
    if _merger is None:
        _merger = CTFMerger()
    return _merger


def merge_hedge_position(condition_id: str, amount_usdc: float) -> Optional[str]:
    """
    Convenience function to merge a hedged position.
    
    Args:
        condition_id: The market's condition ID
        amount_usdc: Amount in USDC (will be converted to 6 decimal places)
    
    Returns:
        Transaction hash if successful
    """
    merger = get_merger()
    amount_wei = int(amount_usdc * 1e6)  # USDC has 6 decimals
    return merger.merge_positions(condition_id, amount_wei)

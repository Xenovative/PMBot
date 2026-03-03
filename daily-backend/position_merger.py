"""
持倉合併器 - 自動合併配對持倉（UP+DOWN）回收 USDC
透過 Polymarket CTF 合約的 mergePositions 函數實現即時利潤鎖定
"""
import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from eth_account import Account
from config import BotConfig
import trade_db

# Polymarket 合約地址 (Polygon)
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDCe on Polygon

# CTF mergePositions ABI
CTF_ABI = [
    {
        "name": "mergePositions",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "partition", "type": "uint256[]"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [],
    },
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "id", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "getPositionId",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "collectionId", "type": "bytes32"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "setApprovalForAll",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "operator", "type": "address"},
            {"name": "approved", "type": "bool"},
        ],
        "outputs": [],
    },
    {
        "name": "isApprovedForAll",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "operator", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
]


@dataclass
class MergeRecord:
    timestamp: str
    market_slug: str
    condition_id: str
    amount: float
    usdc_received: float
    tx_hash: str
    gas_cost: float
    net_profit: float
    status: str  # "success", "failed", "simulated"
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "market_slug": self.market_slug,
            "condition_id": self.condition_id,
            "amount": self.amount,
            "usdc_received": self.usdc_received,
            "tx_hash": self.tx_hash,
            "gas_cost": self.gas_cost,
            "net_profit": self.net_profit,
            "status": self.status,
            "details": self.details,
        }


@dataclass
class PairedPosition:
    market_slug: str
    condition_id: str
    up_token_id: str
    down_token_id: str
    up_balance: float = 0.0
    down_balance: float = 0.0
    mergeable_amount: float = 0.0
    total_cost_basis: float = 0.0  # 買入時的總成本

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market_slug": self.market_slug,
            "condition_id": self.condition_id,
            "up_token_id": self.up_token_id,
            "down_token_id": self.down_token_id,
            "up_balance": self.up_balance,
            "down_balance": self.down_balance,
            "mergeable_amount": self.mergeable_amount,
            "total_cost_basis": self.total_cost_basis,
        }


class PositionMerger:
    """自動合併配對持倉的管理器"""

    # Polygon RPC 端點列表（備用）
    RPC_ENDPOINTS = [
        "https://polygon-rpc.com",
        "https://rpc-mainnet.matic.quiknode.pro",
        "https://polygon.llamarpc.com",
    ]

    def __init__(self, config: BotConfig):
        self.config = config
        self.w3: Optional[Web3] = None
        self.account = None
        self.ctf_contract = None
        self.merge_history: List[MergeRecord] = []
        self.tracked_positions: Dict[str, PairedPosition] = {}  # condition_id -> PairedPosition
        self.auto_merge_enabled: bool = True
        self.min_merge_amount: float = 1.0  # 最小合併數量
        self.logs: List[str] = []
        self._initialized = False

    def add_log(self, msg: str):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        self.logs.append(entry)
        if len(self.logs) > 100:
            self.logs = self.logs[-100:]

    def initialize(self) -> bool:
        """初始化 Web3 連接和合約"""
        if self._initialized:
            return True

        if not self.config.private_key:
            self.add_log("⚠️ 未設定私鑰，合併功能不可用")
            return False

        # 嘗試連接 RPC
        for rpc_url in self.RPC_ENDPOINTS:
            try:
                self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
                self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                if self.w3.is_connected():
                    self.add_log(f"✅ 已連接 Polygon RPC: {rpc_url}")
                    break
            except Exception as e:
                self.add_log(f"⚠️ RPC 連接失敗 {rpc_url}: {e}")
                continue

        if not self.w3 or not self.w3.is_connected():
            self.add_log("❌ 無法連接任何 Polygon RPC")
            return False

        try:
            self.account = Account.from_key(self.config.private_key)
            self.ctf_contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(CTF_ADDRESS),
                abi=CTF_ABI,
            )
            if not self._ensure_merge_approval():
                self.add_log("❌ setApprovalForAll 失敗，無法合併")
                return False
            self._initialized = True
            self.add_log(f"✅ 合併器初始化完成 | 錢包: {self.account.address[:10]}...")
            return True
        except Exception as e:
            self.add_log(f"❌ 初始化失敗: {e}")
            return False

    def _ensure_merge_approval(self) -> bool:
        """確保 CTF 合約已被授權為 operator (ERC1155 setApprovalForAll)。"""
        try:
            owner = self.account.address
            operator = Web3.to_checksum_address(CTF_ADDRESS)
            approved = self.ctf_contract.functions.isApprovedForAll(owner, operator).call()
            if approved:
                return True
            tx = self.ctf_contract.functions.setApprovalForAll(operator, True).build_transaction({
                "from": owner,
                "nonce": self.w3.eth.get_transaction_count(owner),
                "gas": 120000,
                "gasPrice": self.w3.eth.gas_price,
                "chainId": 137,
            })
            signed = self.w3.eth.account.sign_transaction(tx, self.config.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt.status != 1:
                self.add_log(f"❌ setApprovalForAll 失敗 (reverted) | TX: {tx_hash.hex()[:16]}...")
                return False
            self.add_log(f"✅ setApprovalForAll 完成 | TX: {tx_hash.hex()[:16]}...")
            return True
        except Exception as e:
            self.add_log(f"❌ setApprovalForAll 發送失敗: {e}")
            return False

    def track_trade(self, market_slug: str, condition_id: str,
                    up_token_id: str, down_token_id: str,
                    amount: float, total_cost: float):
        """追蹤交易，累積配對持倉"""
        if condition_id not in self.tracked_positions:
            self.tracked_positions[condition_id] = PairedPosition(
                market_slug=market_slug,
                condition_id=condition_id,
                up_token_id=up_token_id,
                down_token_id=down_token_id,
            )

        pos = self.tracked_positions[condition_id]
        pos.up_balance += amount
        pos.down_balance += amount
        pos.mergeable_amount = min(pos.up_balance, pos.down_balance)
        pos.total_cost_basis += total_cost * amount
        self.add_log(
            f"📊 追蹤持倉 | {market_slug} | "
            f"UP: {pos.up_balance:.0f} DOWN: {pos.down_balance:.0f} | "
            f"可合併: {pos.mergeable_amount:.0f}"
        )

    async def check_on_chain_balances(self, condition_id: str) -> Optional[PairedPosition]:
        """從鏈上查詢實際代幣餘額"""
        if not self._initialized:
            if not self.initialize():
                return None

        pos = self.tracked_positions.get(condition_id)
        if not pos:
            return None

        try:
            wallet = self.account.address
            up_balance = self.ctf_contract.functions.balanceOf(
                wallet, int(pos.up_token_id)
            ).call()
            down_balance = self.ctf_contract.functions.balanceOf(
                wallet, int(pos.down_token_id)
            ).call()

            # CTF 代幣使用 6 位小數 (與 USDC 一致)
            pos.up_balance = up_balance / 1e6
            pos.down_balance = down_balance / 1e6
            pos.mergeable_amount = min(pos.up_balance, pos.down_balance)

            self.add_log(
                f"🔗 鏈上餘額 | {pos.market_slug} | "
                f"UP: {pos.up_balance:.2f} DOWN: {pos.down_balance:.2f}"
            )
            return pos

        except Exception as e:
            self.add_log(f"❌ 查詢鏈上餘額失敗: {e}")
            return None

    async def merge_positions(self, condition_id: str,
                              amount: Optional[float] = None) -> Optional[MergeRecord]:
        """
        執行合併操作：燒毀等量 UP+DOWN 代幣，取回 USDC
        mergePositions(collateralToken, parentCollectionId=0x0, conditionId, partition=[1,2], amount)
        """
        if not self._initialized:
            if not self.initialize():
                return None

        # 同步鏈上餘額，避免本地狀態失真導致 revert
        pos = await self.check_on_chain_balances(condition_id)
        if not pos:
            self.add_log(f"❌ 未找到持倉: {condition_id[:16]}...")
            return None

        merge_amount = amount or pos.mergeable_amount
        if merge_amount < self.min_merge_amount:
            self.add_log(f"⚠️ 合併數量不足: {merge_amount:.2f} < {self.min_merge_amount}")
            return None

        record = MergeRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            market_slug=pos.market_slug,
            condition_id=condition_id,
            amount=merge_amount,
            usdc_received=0,
            tx_hash="",
            gas_cost=0,
            net_profit=0,
            status="pending",
        )

        if self.config.dry_run:
            # 模擬合併
            record.status = "simulated"
            record.usdc_received = merge_amount
            cost_basis = (pos.total_cost_basis / max(pos.up_balance, 1)) * merge_amount
            record.net_profit = merge_amount - cost_basis
            record.details = f"🔸 模擬合併 {merge_amount:.0f} 對 → {merge_amount:.2f} USDC"
            self.add_log(record.details)

            # 更新追蹤
            pos.up_balance -= merge_amount
            pos.down_balance -= merge_amount
            pos.mergeable_amount = min(pos.up_balance, pos.down_balance)

            self.merge_history.append(record)
            return record

        # 真實合併
        try:
            wallet = self.account.address
            # 金額轉換為 6 位小數
            amount_raw = int(merge_amount * 1e6)
            condition_id_bytes = bytes.fromhex(condition_id.replace("0x", ""))

            # 構建交易
            tx_func = self.ctf_contract.functions.mergePositions(
                Web3.to_checksum_address(USDC_ADDRESS),  # collateralToken
                b'\x00' * 32,                             # parentCollectionId (null)
                condition_id_bytes,                       # conditionId
                [1, 2],                                   # partition (YES|NO)
                amount_raw,                               # amount
            )

            tx_params = {
                "from": wallet,
                "nonce": self.w3.eth.get_transaction_count(wallet),
                "gasPrice": self.w3.eth.gas_price,
                "chainId": 137,
            }

            # 估算 gas，若失敗提早報錯
            try:
                gas_est = self.w3.eth.estimate_gas({
                    "from": wallet,
                    "to": CTF_ADDRESS,
                    "data": tx_func._encode_transaction_data(),
                })
                tx_params["gas"] = int(gas_est * 1.2)
            except Exception as e:
                self.add_log(f"❌ merge estimateGas 失敗: {e}")
                return None

            tx = tx_func.build_transaction(tx_params)

            # 簽名並發送
            signed_tx = self.w3.eth.account.sign_transaction(tx, self.config.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            tx_hash_hex = tx_hash.hex()

            self.add_log(f"📤 合併交易已發送: {tx_hash_hex[:16]}...")

            # 等待確認
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

            gas_used = receipt["gasUsed"]
            gas_price = tx["gasPrice"]
            gas_cost_matic = (gas_used * gas_price) / 1e18

            if receipt["status"] == 1:
                record.status = "success"
                record.tx_hash = tx_hash_hex
                record.usdc_received = merge_amount
                record.gas_cost = gas_cost_matic
                cost_basis = (pos.total_cost_basis / max(pos.up_balance, 1)) * merge_amount
                record.net_profit = merge_amount - cost_basis
                record.details = (
                    f"✅ 合併成功 | {merge_amount:.0f} 對 → {merge_amount:.2f} USDC | "
                    f"Gas: {gas_cost_matic:.6f} MATIC | TX: {tx_hash_hex[:16]}..."
                )
                self.add_log(record.details)

                # 更新追蹤
                pos.up_balance -= merge_amount
                pos.down_balance -= merge_amount
                pos.mergeable_amount = min(pos.up_balance, pos.down_balance)
            else:
                record.status = "failed"
                record.details = f"❌ 合併交易失敗 (reverted) | TX: {tx_hash_hex[:16]}..."
                self.add_log(record.details)

        except Exception as e:
            record.status = "failed"
            record.details = f"❌ 合併執行失敗: {str(e)}"
            self.add_log(record.details)

        self.merge_history.append(record)

        # 持久化到 SQLite
        try:
            trade_db.record_merge(
                timestamp=record.timestamp,
                market_slug=record.market_slug,
                condition_id=record.condition_id,
                amount=record.amount,
                usdc_received=record.usdc_received,
                tx_hash=record.tx_hash,
                gas_cost=record.gas_cost,
                net_profit=record.net_profit,
                status=record.status,
                details=record.details,
            )
            trade_db.rebuild_daily_summary()
        except Exception:
            pass

        return record

    async def auto_merge_all(self) -> List[MergeRecord]:
        """自動合併所有可合併的配對持倉"""
        if not self.auto_merge_enabled:
            return []

        results = []
        for cid, pos in list(self.tracked_positions.items()):
            if pos.mergeable_amount >= self.min_merge_amount:
                self.add_log(
                    f"🔄 自動合併 | {pos.market_slug} | "
                    f"數量: {pos.mergeable_amount:.0f}"
                )
                record = await self.merge_positions(cid)
                if record:
                    results.append(record)

        return results

    def get_status(self) -> Dict[str, Any]:
        """取得合併器狀態"""
        positions = [p.to_dict() for p in self.tracked_positions.values()]
        total_mergeable = sum(p.mergeable_amount for p in self.tracked_positions.values())
        total_merged = sum(
            r.usdc_received for r in self.merge_history if r.status in ("success", "simulated")
        )
        total_gas = sum(
            r.gas_cost for r in self.merge_history if r.status == "success"
        )

        return {
            "auto_merge_enabled": self.auto_merge_enabled,
            "initialized": self._initialized,
            "positions": positions,
            "total_tracked": len(self.tracked_positions),
            "total_mergeable": total_mergeable,
            "total_merged_usdc": total_merged,
            "total_gas_cost": total_gas,
            "merge_count": len([r for r in self.merge_history if r.status in ("success", "simulated")]),
            "merge_history": [r.to_dict() for r in self.merge_history[-20:]],
            "logs": self.logs[-30:],
        }

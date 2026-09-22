# -*- coding: utf-8 -*-
"""
区块链对接模块
==============================
职责：
  1. 连接 Ganache 本地联盟链（http://127.0.0.1:8545）
  2. 加载 backend/contracts.json 中的合约地址与 ABI
  3. 封装智能合约调用：交易上链、风险上链、报告哈希上链、链上核验

降级策略（重要）：
  当 Ganache 未启动或 contracts.json 不存在时，系统自动降级为
  "本地模拟哈希" 模式：交易哈希仍按与合约完全一致的算法（keccak256）
  计算并写入数据库，但 chain_status 标记为 simulated，保证演示可用。
  一旦 Ganache 正常启动并完成部署，所有数据将真实写入联盟链。
"""
import hashlib
import time

from config import GANACHE_URL, GAS, GAS_PRICE, load_contract_config

try:
    from web3 import Web3
except Exception:  # pragma: no cover
    Web3 = None


def compute_tx_hash(from_account, to_account, amount_fen, nonce, timestamp):
    """
    计算交易哈希 —— 与智能合约 computeTxHash 完全一致：
        keccak256(abi.encodePacked(from, to, amount, nonce, timestamp))
    返回形如 0x + 64 位十六进制字符串。
    amount_fen 为以"分"为单位的金额（整数）。
    """
    if Web3 is not None:
        return Web3.solidity_keccak(
            ["string", "string", "uint256", "uint256", "uint256"],
            [from_account, to_account, int(amount_fen), int(nonce), int(timestamp)],
        ).hex()
    # 无 web3 库时的兜底：使用 sha256（仅本地模拟，格式同为 0x+64 位）
    raw = f"{from_account}:{to_account}:{amount_fen}:{nonce}:{timestamp}".encode("utf-8")
    return "0x" + hashlib.sha256(raw).hexdigest()


class ChainClient:
    """联盟链客户端单例封装"""

    def __init__(self):
        self.connected = False          # 是否真实连接联盟链
        self.w3 = None
        self.contract = None
        self.address = None             # 合约地址
        self.deployer = None            # 部署账户（交易发送方）

        if Web3 is None:
            return
        try:
            self.w3 = Web3(Web3.HTTPProvider(GANACHE_URL, request_kwargs={"timeout": 10}))
            if not self.w3.is_connected():
                print("[blockchain] 警告：无法连接 Ganache，降级为本地模拟哈希模式")
                return
            cfg = load_contract_config()
            if not cfg or not cfg.get("address"):
                print("[blockchain] 警告：未找到 contracts.json，请先运行 blockchain/deploy.js 部署合约")
                print("[blockchain] 当前降级为本地模拟哈希模式")
                return
            self.address = cfg["address"]
            self.contract = self.w3.eth.contract(address=self.address, abi=cfg["abi"])
            accounts = self.w3.eth.accounts
            self.deployer = accounts[0] if accounts else None
            self.connected = True
            print(f"[blockchain] 已连接联盟链，合约地址: {self.address}")
        except Exception as e:
            print(f"[blockchain] 连接失败，降级为本地模拟哈希模式: {e}")
            self.connected = False

    # ------------------------------------------------------------------
    # 交易上链
    # ------------------------------------------------------------------
    def record_transaction(self, from_account, to_account, amount_fen, nonce, timestamp):
        """
        交易上链。返回 (tx_hash, chain_status)。
        chain_status: confirmed（真实上链）/ simulated（模拟哈希）
        """
        tx_hash = compute_tx_hash(from_account, to_account, amount_fen, nonce, timestamp)
        if not self.connected or self.contract is None:
            return tx_hash, "simulated"
        try:
            txn = self.contract.functions.recordTransaction(
                from_account, to_account, int(amount_fen), int(nonce), int(timestamp)
            ).transact({"from": self.deployer, "gas": GAS, "gasPrice": GAS_PRICE})
            self.w3.eth.wait_for_transaction_receipt(txn, timeout=30)
            return tx_hash, "confirmed"
        except Exception as e:
            print(f"[blockchain] 交易上链失败: {e}")
            return tx_hash, "simulated"

    # ------------------------------------------------------------------
    # 交易风险信息补录上链
    # ------------------------------------------------------------------
    def update_tx_risk(self, tx_hash, risk_level, rule_ids):
        if not self.connected or self.contract is None:
            return False
        try:
            txn = self.contract.functions.updateTxRisk(
                tx_hash, int(risk_level), rule_ids
            ).transact({"from": self.deployer, "gas": GAS, "gasPrice": GAS_PRICE})
            self.w3.eth.wait_for_transaction_receipt(txn, timeout=30)
            return True
        except Exception as e:
            print(f"[blockchain] 交易风险补录失败: {e}")
            return False

    # ------------------------------------------------------------------
    # 风险行为上链
    # ------------------------------------------------------------------
    def record_risk(self, tx_hash, account, rule_ids, risk_level, timestamp):
        if not self.connected or self.contract is None:
            return False
        try:
            txn = self.contract.functions.recordRisk(
                tx_hash, account, rule_ids, int(risk_level), int(timestamp)
            ).transact({"from": self.deployer, "gas": GAS, "gasPrice": GAS_PRICE})
            self.w3.eth.wait_for_transaction_receipt(txn, timeout=30)
            return True
        except Exception as e:
            print(f"[blockchain] 风险记录上链失败: {e}")
            return False

    # ------------------------------------------------------------------
    # 存证报告哈希上链
    # ------------------------------------------------------------------
    def record_report(self, case_no, report_hash_hex):
        """
        report_hash_hex: 报告内容 SHA256 十六进制（64 位）。
        合约以 bytes32 存储（64 位 hex 恰好 32 字节）。
        """
        if not self.connected or self.contract is None:
            return None
        try:
            report_bytes = self.w3.to_bytes(hexstr="0x" + report_hash_hex)
            txn = self.contract.functions.recordReport(case_no, report_bytes).transact(
                {"from": self.deployer, "gas": GAS, "gasPrice": GAS_PRICE}
            )
            receipt = self.w3.eth.wait_for_transaction_receipt(txn, timeout=30)
            return self.w3.to_hex(receipt.transactionHash)
        except Exception as e:
            print(f"[blockchain] 报告哈希上链失败: {e}")
            return None

    # ------------------------------------------------------------------
    # 链上核验接口
    # ------------------------------------------------------------------
    def get_transaction(self, tx_hash):
        """按交易哈希核验链上交易，返回 dict；不存在返回 None"""
        if not self.connected or self.contract is None:
            return None
        try:
            r = self.contract.functions.getTransaction(tx_hash).call()
            if not r[7]:
                return None
            return {
                "tx_hash": r[0].hex(),
                "from_account": r[1],
                "to_account": r[2],
                "amount_fen": r[3],
                "timestamp": r[4],
                "risk_level": r[5],
                "rule_ids": r[6],
                "exists": r[7],
            }
        except Exception as e:
            print(f"[blockchain] 链上交易核验失败: {e}")
            return None

    def get_risk_record(self, tx_hash, account):
        """按 (交易哈希, 账户) 核验链上风险记录"""
        if not self.connected or self.contract is None:
            return None
        try:
            r = self.contract.functions.getRiskRecord(tx_hash, account).call()
            if not r[5]:
                return None
            return {
                "tx_hash": r[0].hex(),
                "account": r[1],
                "rule_ids": r[2],
                "risk_level": r[3],
                "timestamp": r[4],
                "exists": r[5],
            }
        except Exception as e:
            print(f"[blockchain] 链上风险记录核验失败: {e}")
            return None

    def get_report_hash(self, case_no):
        """按案件编号核验链上存证报告哈希，返回 0x+64位 或 None"""
        if not self.connected or self.contract is None:
            return None
        try:
            h = self.contract.functions.getReportHash(case_no).call()
            if h.hex() == "0x" + "00" * 32:  # 空 bytes32
                return None
            return "0x" + h.hex()
        except Exception as e:
            print(f"[blockchain] 链上报告哈希核验失败: {e}")
            return None


# 全局单例：全项目共用同一个链客户端
chain = ChainClient()

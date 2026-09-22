/**
 * Ganache 链上客户端（基于 ethers.js）
 * ====================================
 * 职责：
 *  1. 连接 Ganache 私有链（JsonRpcProvider）
 *  2. 加载 FundTrace 合约（地址 + ABI）
 *  3. 提供链上交易写入 / 查询 / 事件解析等封装
 *
 * 安全约定：
 *  - 用户转账必须由 MetaMask 签名后由前端直接广播到链上，后端绝不代签转账；
 *  - 后端仅使用存证签名账户（deployer / BACKEND_PRIVATE_KEY）
 *    写入 实名哈希、案件证据哈希 等系统级存证数据。
 */
const fs = require('fs');
const { ethers } = require('ethers');
const config = require('../config');

/** 合约产物（地址 + ABI） */
const contractConfig = JSON.parse(fs.readFileSync(config.CONTRACTS_FILE, 'utf8'));

/** Ganache Provider */
const provider = new ethers.providers.JsonRpcProvider(config.GANACHE_URL, {
  chainId: config.CHAIN_ID,
  name: 'ganache',
});

/**
 * 后端存证签名账户：
 *  - 配置了 BACKEND_PRIVATE_KEY 时使用该私钥；
 *  - 未配置时使用 Ganache account[0]（Ganache 默认解锁全部账户，可直接签名）。
 */
const backendWallet = config.BACKEND_PRIVATE_KEY
  ? new ethers.Wallet(config.BACKEND_PRIVATE_KEY, provider)
  : provider.getSigner(0);

/** 合约实例（写操作默认使用后端存证账户） */
const contract = new ethers.Contract(config.CONTRACT_ADDRESS, contractConfig.abi, backendWallet);

/** 合约只读实例 */
const readContract = new ethers.Contract(config.CONTRACT_ADDRESS, contractConfig.abi, provider);

// ---------------------------------------------------------------------
// 链可用性缓存
//   Ganache 未启动 / 合约未部署时，读接口返回降级值（空数据）而不是抛异常，
//   保证前端页面可正常加载并提示"链未连接"，写接口仍会明确报错。
// ---------------------------------------------------------------------
let _chainUp = false;
let _chainDownMsg = 'Ganache 未连接';

/** 记录链可用性探测结果 */
function _markChain(up, msg) {
  _chainUp = up;
  _chainDownMsg = msg || (up ? '' : 'Ganache 未连接');
  return up;
}

/** 当前链是否可用（供路由附加 chain_down 标记） */
function isChainUp() {
  return _chainUp;
}

/** 链不可用时的原因描述 */
function chainDownMsg() {
  return _chainDownMsg;
}

/**
 * 测试链与合约连通性
 */
async function getChainStatus() {
  try {
    const network = await provider.getNetwork();
    const blockNumber = await provider.getBlockNumber();
    const signerAddr = await backendWallet.getAddress();
    const txCount = await readContract.getTxCount();
    _markChain(true);
    return {
      ok: true,
      chainId: network.chainId,
      blockNumber,
      contractAddress: config.CONTRACT_ADDRESS,
      backendSigner: signerAddr,
      txCount: txCount.toNumber(),
      isContractDeployed: config.CONTRACT_ADDRESS !== ethers.constants.AddressZero,
    };
  } catch (e) {
    _markChain(false, `Ganache 连接失败：${e.message}`);
    return { ok: false, msg: _chainDownMsg };
  }
}

// ---------------------------------------------------------------------
// 交易查询（链上为准）
// ---------------------------------------------------------------------

/**
 * 解析链上交易记录
 */
function _parseTx(row) {
  return {
    txId: row[0].toNumber(),
    sender: row[1],
    receiver: row[2],
    amountWei: row[3].toString(),
    amountEth: ethers.utils.formatEther(row[3]),
    timestamp: row[4].toNumber(),
    nonce: row[5].toString(),
    riskLevel: row[6],
    riskText: config.RISK_TEXT[row[6]] || '未知',
    signature: row[7],
    exists: row[8],
  };
}

/**
 * 按交易ID查询链上交易（返回完整元数据，含签名）
 * 链不可用时返回 null（不抛异常，前端按空数据处理）
 */
async function getTransaction(txId) {
  try {
    const row = await readContract.getTransaction(txId);
    if (!row[8]) return null;
    return _parseTx(row);
  } catch (e) {
    _markChain(false, `Ganache 连接失败：${e.message}`);
    return null;
  }
}

/**
 * 获取链上交易总数（链不可用时返回 0）
 */
async function getTxCount() {
  try {
    const n = await readContract.getTxCount();
    return n.toNumber();
  } catch (e) {
    _markChain(false, `Ganache 连接失败：${e.message}`);
    return 0;
  }
}

/**
 * 遍历读取链上全部交易（用于监管全量查询 / 风险筛选 / 资金溯源）
 * 链不可用时返回空数组（不抛异常）
 */
async function getAllTransactions() {
  try {
    const count = await getTxCount();
    if (!count) return [];
    const list = [];
    for (let i = 1; i <= count; i++) {
      const row = await readContract.getTransaction(i);
      if (row[8]) list.push(_parseTx(row));
    }
    return list;
  } catch (e) {
    _markChain(false, `Ganache 连接失败：${e.message}`);
    return [];
  }
}

// ---------------------------------------------------------------------
// 交易写入（由前端 MetaMask 广播后，后端仅做回读与索引）
// ---------------------------------------------------------------------

/**
 * 回读一笔已上链转账交易，并在本地镜像索引表登记。
 * @param {string} txHash MetaMask 广播的链上交易哈希
 */
async function indexTxByHash(txHash) {
  const receipt = await provider.waitForTransaction(txHash, 1, 60000);
  if (!receipt || receipt.status !== 1) {
    throw new Error('链上交易未成功（status != 1）');
  }
  // 从事件日志中解析 TxRecorded(txId, sender, receiver, amountWei, timestamp, riskLevel)
  const iface = new ethers.utils.Interface(contractConfig.abi);
  let txId = null;
  for (const log of receipt.logs) {
    try {
      const parsed = iface.parseLog(log);
      if (parsed.name === 'TxRecorded') {
        txId = parsed.args.txId.toNumber();
        break;
      }
    } catch (e) { /* 非本合约事件，跳过 */ }
  }
  if (txId == null) {
    throw new Error('未在交易日志中找到 TxRecorded 事件');
  }
  return getTransaction(txId);
}

// ---------------------------------------------------------------------
// 存证写入（后端存证账户签名，非用户转账）
// ---------------------------------------------------------------------

/**
 * 实名信息哈希上链存证
 * @param {string} account 用户钱包地址
 * @param {string} nameHash 实名信息哈希（0x+64位）
 * @returns {string} 链上存证交易哈希
 */
async function recordRealNameOnChain(account, nameHash) {
  const tx = await contract.recordRealName(account, nameHash, { gasLimit: 300000 });
  await tx.wait();
  return tx.hash;
}

/**
 * 案件证据哈希上链存证
 * @param {string} caseNo 案件编号
 * @param {string} evidenceHash 证据整体哈希（0x+64位）
 * @returns {string} 链上存证交易哈希
 */
async function recordCaseEvidenceOnChain(caseNo, evidenceHash) {
  const tx = await contract.recordCaseEvidence(caseNo, evidenceHash, { gasLimit: 400000 });
  await tx.wait();
  return tx.hash;
}

/**
 * 查询链上实名哈希（链不可用时返回 null）
 */
async function getRealNameHashOnChain(account) {
  try {
    const h = await readContract.getRealNameHash(account);
    return h && h !== ethers.constants.HashZero ? h : null;
  } catch (e) {
    _markChain(false, `Ganache 连接失败：${e.message}`);
    return null;
  }
}

/**
 * 查询链上案件证据哈希（链不可用时返回 null）
 */
async function getCaseEvidenceHashOnChain(caseNo) {
  try {
    const h = await readContract.getCaseEvidenceHash(caseNo);
    return h && h !== ethers.constants.HashZero ? h : null;
  } catch (e) {
    _markChain(false, `Ganache 连接失败：${e.message}`);
    return null;
  }
}

/**
 * 按钱包地址查询 Ganache 实时余额（wei / ETH）
 * 链不可用时返回 0 余额（不抛异常）
 */
async function getBalance(account) {
  try {
    const b = await provider.getBalance(account);
    return { wei: b.toString(), eth: ethers.utils.formatEther(b) };
  } catch (e) {
    _markChain(false, `Ganache 连接失败：${e.message}`);
    return { wei: '0', eth: '0.0' };
  }
}

module.exports = {
  provider,
  contract,
  readContract,
  contractAbi: contractConfig.abi,
  backendWallet,
  getChainStatus,
  isChainUp,
  chainDownMsg,
  getTransaction,
  getTxCount,
  getAllTransactions,
  indexTxByHash,
  recordCaseEvidenceOnChain,
  getCaseEvidenceHashOnChain,
  getBalance,
};

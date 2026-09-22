/**
 * 全局配置模块（Node.js 后端）
 * ==============================
 * 本模块统一管理：
 *  1. Ganache 私有链连接信息与合约地址
 *  2. 监管管理员钱包白名单（其余 Ganache 地址均为普通用户）
 *  3. 风险阈值（底层统一使用 wei）
 *  4. MySQL 数据库连接信息
 *  5. 资金溯源参数
 *
 * 说明：本项目为封闭模拟仿真环境，所有配置均为本地模拟参数，
 *       严禁连接任何真实金融环境。
 */
const path = require('path');

// ---------------------------------------------------------------------
// 服务器配置
// ---------------------------------------------------------------------
const PORT = process.env.PORT || 5000;                 // 服务监听端口
const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');  // 前端静态目录

// ---------------------------------------------------------------------
// Ganache 私有链配置
// ---------------------------------------------------------------------
const GANACHE_URL = 'http://127.0.0.1:7545';   // Ganache GUI 默认 RPC 地址
const CHAIN_ID = 1337;                          // Ganache 默认 chainId

// 合约地址：部署后由 blockchain/deploy.js 自动回填到 contracts/FundTrace.json
const CONTRACTS_FILE = path.join(__dirname, 'contracts', 'FundTrace.json');
const CONTRACT_ADDRESS = require(CONTRACTS_FILE).address;   // 部署成功后为真实地址

// 后端"存证签名账户"私钥：
//   - 用于实名哈希存证、案件证据哈希存证等系统级上链写入（非用户转账）；
//   - 若留空则自动使用 Ganache account[0]（Ganache 默认解锁全部账户）。
//   - 从 Ganache UI 复制一个账户的私钥填入即可。
const BACKEND_PRIVATE_KEY = process.env.BACKEND_PRIVATE_KEY || '';

// ---------------------------------------------------------------------
// 监管管理员钱包白名单
//   - 匹配白名单的地址 -> 监管工作台权限（案件审核/查看全部交易/按风险筛选/溯源取证）
//   - 其余 Ganache 地址        -> 普通用户权限
//   - 请将下面地址替换为你自己的 Ganache 账户地址（小写或校验和均可）
// ---------------------------------------------------------------------
const ADMIN_WALLETS = [
  '0x0A739d7AA22bF2696F0674B3A7C645D84f0df7b9',   // 监管管理员（MetaMask 当前使用账户，Ganache GUI account[0]）
  '0x90F8bf6A479f320ead074411a4B0e7944Ea8c9C1',   // 备用管理员
];

// ---------------------------------------------------------------------
// 风险监测规则阈值（单位 wei）
//   - 高风险：金额 >= 10000000000000000000 wei（10 ETH）
//   - 中风险：5000000000000000000 wei（5 ETH）<= 金额 < 10 ETH
//   - 低风险：金额 < 5000000000000000000 wei（5 ETH）
// ---------------------------------------------------------------------
const RISK_THRESHOLDS = {
  HIGH_WEI: '10000000000000000000',
  MEDIUM_WEI: '5000000000000000000',
};

// 风险等级文案（与前端 riskBadge 映射保持一致）
const RISK_TEXT = { 1: '低风险', 2: '中风险', 3: '高风险' };
const RISK_COLOR = { 1: '#f1c40f', 2: '#f39c12', 3: '#ef4444' };

// ---------------------------------------------------------------------
// MySQL 数据库配置（请按本机环境修改）
// ---------------------------------------------------------------------
const DB_CONFIG = {
  host: '127.0.0.1',
  port: 3306,
  user: 'root',
  password: '06gkG12!',
  database: 'fund_trace',
  charset: 'utf8mb4',
  connectionLimit: 10,
};

// ---------------------------------------------------------------------
// 资金溯源参数
// ---------------------------------------------------------------------
const TRACE = {
  MAX_DEPTH: 6,        // 最大追溯深度（层）
  MAX_NODES: 60,       // 最大节点数
  FOLLOW_OUT_LIMIT: 20, // 每个中转账户最多追溯的后续转出笔数
};

module.exports = {
  PORT,
  FRONTEND_DIR,
  GANACHE_URL,
  CHAIN_ID,
  CONTRACTS_FILE,
  CONTRACT_ADDRESS,
  BACKEND_PRIVATE_KEY,
  ADMIN_WALLETS,
  RISK_THRESHOLDS,
  RISK_TEXT,
  RISK_COLOR,
  DB_CONFIG,
  TRACE,
};

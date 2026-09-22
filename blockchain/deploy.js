/**
 * FundTrace 合约编译与部署脚本（基于 ethers.js + solc，适配 Ganache 私有链）
 *
 * 前置条件：
 *   1. 已启动 Ganache 本地私有链（默认 http://127.0.0.1:8545）
 *   2. 已安装依赖：npm install（ethers + solc）
 *
 * 运行方式：
 *   node deploy.js
 *
 * 执行结果：
 *   1. 使用 solc 编译 contracts/FundTrace.sol
 *   2. 将合约部署到 Ganache 第一个账户（account[0]）
 *   3. 生成两份产物：
 *      - ../backend-js/contracts/FundTrace.json （Node.js 后端读取：地址 + ABI）
 *      - ../backend/contracts.json              （兼容旧 Flask 后端产物路径）
 *
 * 注意：部署账户即合约 owner，也是后端"存证签名账户"（写入实名哈希/案件证据哈希）。
 */
const fs = require('fs');
const path = require('path');
const solc = require('solc');
const { ethers } = require('ethers');

// ---- 路径常量 ----
const SOL_PATH = path.join(__dirname, 'contracts', 'FundTrace.sol');
const OUT_NODE = path.join(__dirname, '..', 'backend-js', 'contracts', 'FundTrace.json');
const OUT_FLASK = path.join(__dirname, '..', 'backend', 'contracts.json');

// ---- Ganache 默认 RPC 地址 ----
const GANACHE_URL = 'http://127.0.0.1:7545';

/**
 * 使用 solc 编译合约，返回 { abi, bytecode }
 */
function compile() {
  const source = fs.readFileSync(SOL_PATH, 'utf8');
  const input = {
    language: 'Solidity',
    sources: { 'FundTrace.sol': { content: source } },
    settings: {
      // Ganache GUI 的 EVM 较旧，不支持 Shanghai 的 PUSH0（0x5f），
      // 因此固定编译目标为 paris，避免部署时 "invalid opcode"
      evmVersion: 'paris',
      optimizer: { enabled: true, runs: 200 },
      outputSelection: { '*': { '*': ['abi', 'evm.bytecode'] } },
    },
  };
  const output = JSON.parse(solc.compile(JSON.stringify(input)));

  if (output.errors) {
    const errors = output.errors.filter((e) => e.severity === 'error');
    if (errors.length > 0) {
      console.error('编译失败：');
      errors.forEach((e) => console.error('  ' + e.formattedMessage));
      process.exit(1);
    }
  }

  const contract = output.contracts['FundTrace.sol']['FundTrace'];
  if (!contract) {
    console.error('未找到合约 FundTrace，请检查合约文件内容');
    process.exit(1);
  }
  return { abi: contract.abi, bytecode: contract.evm.bytecode.object };
}

/**
 * 部署合约到 Ganache 并输出 ABI/地址产物
 */
async function main() {
  console.log('>>> 开始编译 FundTrace.sol ...');
  const { abi, bytecode } = compile();
  console.log('>>> 编译成功，正在连接 Ganache ...');

  const provider = new ethers.providers.JsonRpcProvider(GANACHE_URL);
  const network = await provider.getNetwork();
  console.log('>>> Ganache 网络:', network.name, 'chainId:', network.chainId);

  const accounts = await provider.listAccounts();
  if (!accounts || accounts.length === 0) {
    console.error('无法获取 Ganache 账户，请确认 Ganache 已启动：' + GANACHE_URL);
    process.exit(1);
  }
  const deployer = accounts[0];
  const balance = await provider.getBalance(deployer);
  console.log('>>> 部署账户:', deployer, '余额(ETH):', ethers.utils.formatEther(balance));

  console.log('>>> 正在部署合约 ...');
  const factory = new ethers.ContractFactory(abi, bytecode, provider.getSigner(deployer));
  const contract = await factory.deploy({ gasLimit: 5000000, gasPrice: 20000000000 });
  await contract.deployed();

  const address = contract.address;
  const output = {
    address,
    abi,
    network: 'ganache',
    deployedAt: new Date().toISOString(),
    deployer,
  };

  // 确保输出目录存在
  fs.mkdirSync(path.dirname(OUT_NODE), { recursive: true });
  fs.writeFileSync(OUT_NODE, JSON.stringify(output, null, 2));
  fs.writeFileSync(OUT_FLASK, JSON.stringify(output, null, 2));

  console.log('>>> 合约部署成功！地址:', address);
  console.log('>>> ABI/地址已写入:', OUT_NODE);
  console.log('>>> 兼容产物已写入:', OUT_FLASK);
}

main().catch((err) => {
  console.error('部署失败：', err);
  process.exit(1);
});

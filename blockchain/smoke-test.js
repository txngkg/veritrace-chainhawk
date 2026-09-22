/**
 * 端到端冒烟测试（临时脚本，验证后删除）
 * 用 Ganache 实际账户私钥模拟 MetaMask 对 messageHash 本地签名，
 * 走完整链路：钱包校验 -> 交易准备 -> 合约上链 -> 回执索引 -> 监管风险查询。
 */
const { ethers } = require('ethers');
const fs = require('fs');
const http = require('http');

const GANACHE = 'http://127.0.0.1:8545';
const provider = new ethers.providers.JsonRpcProvider(GANACHE);

// Ganache 账户（助记词 myth like bonus scare over problem client lizard pioneer submit female collect）
const ACCOUNTS = {
  admin: '0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d', // account[0] 监管管理员
  user: '0x6cbed15c793ce57650b9877cf6fa156fbef513c4e6134f022a85b1ffdd59b2a1',  // account[1] 普通用户
  receiver: '0x6370fd033278c143179d81c5526140625662b8daa446c22ee2d73db3707e620c', // account[2] 收款方
};
const userKey = new ethers.Wallet(ACCOUNTS.user);
const adminAddr = new ethers.Wallet(ACCOUNTS.admin).address;
const receiverAddr = new ethers.Wallet(ACCOUNTS.receiver).address;

function post(path, body, wallet) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const headers = { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) };
    if (wallet) headers['X-Wallet-Address'] = wallet;
    const req = http.request({ host: '127.0.0.1', port: 5000, path, method: 'POST', headers }, (res) => {
      let buf = '';
      res.on('data', (d) => (buf += d));
      res.on('end', () => resolve(JSON.parse(buf)));
    });
    req.on('error', reject);
    req.write(data);
    req.end();
  });
}
function get(path, wallet) {
  return new Promise((resolve, reject) => {
    const headers = {};
    if (wallet) headers['X-Wallet-Address'] = wallet;
    const req = http.request({ host: '127.0.0.1', port: 5000, path, method: 'GET', headers }, (res) => {
      let buf = '';
      res.on('data', (d) => (buf += d));
      res.on('end', () => resolve(JSON.parse(buf)));
    });
    req.on('error', reject);
    req.end();
  });
}

(async () => {
  console.log('用户钱包:', userKey.address);
  console.log('接收钱包:', receiverAddr);

  const v = await post('/api/wallet/verify', { address: userKey.address });
  console.log('1.钱包校验 role:', v.role, '(期望 user)');

  const prep = await post('/api/tx/prepare', { to: receiverAddr, amountEth: '0.25' }, userKey.address);
  console.log('2.prepare: amountWei=' + prep.amountWei, 'amountEth=' + prep.amountEth);

  const signature = await userKey.signMessage(ethers.utils.arrayify(prep.messageHash));
  console.log('3.MetaMask签名长度:', signature.length, '(期望132)');

  const cf = JSON.parse(fs.readFileSync('../backend-js/contracts/FundTrace.json', 'utf8'));
  const c = new ethers.Contract(cf.address, cf.abi, userKey.connect(provider));
  const tx = await c.transferEth(receiverAddr, prep.nonce, signature, { value: prep.amountWei, gasLimit: 1000000 });
  const receipt = await tx.wait();
  console.log('4.合约上链成功 txHash:', receipt.transactionHash);

  const sub = await post('/api/tx/submit', { txHash: receipt.transactionHash }, userKey.address);
  console.log('5.submit:', sub.ok, '| 风险:', sub.riskText, '(期望高风险) | txId:', sub.txId);

  const mine = await get('/api/tx/mine', userKey.address);
  console.log('6.我的交易记录:', mine.transactions.length, '笔, chain_down:', mine.chain_down);

  const risk = await get('/api/admin/risk-tx', adminAddr);
  console.log('7.监管风险交易:', risk.total, '笔, 首笔风险:', risk.transactions[0] && risk.transactions[0].riskText);

  const onchain = await c.getTransaction(sub.txId);
  console.log('8.链上签名与报案签名一致:', onchain.signature === signature);
})().catch((e) => {
  console.error('测试失败:', e.message);
  process.exit(1);
});

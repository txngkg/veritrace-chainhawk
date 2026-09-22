/**
 * 诈骗资金风险监测与报案溯源固证辅助系统 —— Node.js 后端服务入口
 * =============================================================
 * 技术栈：Express + ethers.js + MySQL + Ganache 私有链
 *
 * 身份体系：不使用数据库账号密码，所有用户身份 = Ganache 钱包地址。
 *   普通用户与监管管理员统一通过 MetaMask 连接钱包登录；
 *   后端校验钱包地址：匹配白名单 ADMIN_WALLETS 则授予监管工作台权限，
 *   其余 Ganache 地址均为普通用户权限。
 *
 * 安全约束：
 *   - 所有转账必须经用户 MetaMask 签名并直接广播至 Ganache，后端绝不代签转账；
 *   - 后端存证账户仅写入 实名哈希 / 案件证据哈希 等系统级存证数据；
 *   - 实名明文禁止上链，仅存信息哈希。
 *
 * 启动方式（在 backend-js/ 目录下）：
 *   npm install
 *   node server.js
 * 默认监听 http://127.0.0.1:5000
 */
const path = require('path');
const express = require('express');
const cors = require('cors');
const { ethers } = require('ethers');

const config = require('./config');
const db = require('./lib/db');
const chain = require('./lib/chain');
const risk = require('./lib/risk');
const sig = require('./lib/signature');
const realname = require('./lib/realname');
const traceModule = require('./lib/trace');

const app = express();
app.use(cors());
app.use(express.json({ limit: '2mb' }));

// ---------------------------------------------------------------------
// 鉴权中间件
// ---------------------------------------------------------------------

/** 提取请求钱包地址（X-Wallet-Address 头） */
function getWalletAddress(req) {
  const raw = req.headers['x-wallet-address'] || '';
  return raw.trim().toLowerCase();
}

/**
 * 普通用户鉴权：要求携带有效钱包地址
 */
function requireWallet(req, res, next) {
  const addr = getWalletAddress(req);
  if (!addr || !ethers.utils.isAddress(addr)) {
    return res.status(401).json({ ok: false, msg: '未连接钱包，请先通过 MetaMask 连接钱包' });
  }
  req.wallet = ethers.utils.getAddress(addr);
  req.isAdmin = isAdminAddress(addr); // 管理员命中白名单时为 true，供案件详情等接口做权限分支
  next();
}

/**
 * 监管管理员鉴权：钱包地址必须命中白名单
 */
function requireAdmin(req, res, next) {
  const addr = getWalletAddress(req);
  if (!addr || !ethers.utils.isAddress(addr)) {
    return res.status(401).json({ ok: false, msg: '未连接钱包，请先通过 MetaMask 连接钱包' });
  }
  if (!isAdminAddress(addr)) {
    return res.status(403).json({ ok: false, msg: '无监管权限，当前钱包非白名单监管管理员账户' });
  }
  req.wallet = ethers.utils.getAddress(addr);
  req.isAdmin = true;
  next();
}

/** 判断地址是否白名单监管管理员 */
function isAdminAddress(address) {
  const a = String(address).toLowerCase();
  return config.ADMIN_WALLETS.some((w) => w.toLowerCase() === a);
}

// =====================================================================
// 一、钱包连接校验接口
// =====================================================================

/**
 * POST /api/wallet/verify
 * 钱包连接校验：唤起 MetaMask 授权后，前端将当前钱包地址提交后端校验。
 * 后端判定：命中白名单管理员地址 -> 监管工作台权限；其余 -> 普通用户。
 * 入参：{ "address": "0x..." }
 * 出参：{ ok, address, is_admin, role, contract_address, chain_ok }
 */
app.post('/api/wallet/verify', async (req, res) => {
  try {
    const address = String(req.body.address || '').trim();
    if (!ethers.utils.isAddress(address)) {
      return res.status(400).json({ ok: false, msg: '钱包地址格式不正确' });
    }
    const normalized = ethers.utils.getAddress(address);
    const isAdmin = isAdminAddress(normalized);
    const chainStatus = await chain.getChainStatus();
    res.json({
      ok: true,
      address: normalized,
      is_admin: isAdmin,
      role: isAdmin ? 'admin' : 'user',
      contract_address: config.CONTRACT_ADDRESS,
      ganache_url: config.GANACHE_URL,
      chain_ok: chainStatus.ok,
    });
  } catch (e) {
    res.status(500).json({ ok: false, msg: '钱包校验失败：' + e.message });
  }
});

// =====================================================================
// 二、交易相关接口（转账必须经 MetaMask 签名）
// =====================================================================

/**
 * POST /api/tx/prepare
 * 交易签名准备：用户输入接收方地址与 ETH 金额后调用。
 * 后端将 ETH 自动转换为 wei，生成随机 nonce 并计算签名消息哈希，
 * 前端需用 MetaMask 对 messageHash 执行 personal_sign。
 * 入参：{ "to": "0x...", "amountEth": "0.123" }
 * 出参：{ ok, to, amountWei, amountEth, nonce, messageHash }
 */
app.post('/api/tx/prepare', requireWallet, async (req, res) => {
  try {
    const to = sig.normalizeAddress(req.body.to || '');
    const amountWei = risk.ethToWei(req.body.amountEth);
    // 生成随机 nonce（256 位随机数，防签名重放）
    const nonce = ethers.BigNumber.from(ethers.utils.randomBytes(32)).toString();
    const messageHash = sig.txMessageHash(to, amountWei, nonce);
    res.json({
      ok: true,
      to,
      amountWei,
      amountEth: ethers.utils.formatEther(amountWei),
      nonce,
      messageHash,
    });
  } catch (e) {
    res.status(400).json({ ok: false, msg: e.message });
  }
});

/**
 * POST /api/tx/submit
 * 交易提交（上链后回执确认）：
 *   前端已通过 MetaMask 签名并调用合约 transferEth 广播到 Ganache，
 *   交易成功后携带链上交易哈希调用本接口。
 *   后端回读交易收据，解析 TxRecorded 事件得到链上交易ID，
 *   核验发送方为当前钱包后登记镜像索引并返回风险等级。
 * 入参：{ "txHash": "0x..." }
 * 出参：{ ok, txId, txHash, from, to, amountWei, amountEth, riskLevel, riskText, timestamp, nonce }
 */
app.post('/api/tx/submit', requireWallet, async (req, res) => {
  try {
    const txHash = String(req.body.txHash || '');
    if (!/^0x[0-9a-fA-F]{64}$/.test(txHash)) {
      return res.status(400).json({ ok: false, msg: '交易哈希格式不正确' });
    }
    const tx = await chain.indexTxByHash(txHash);
    // 核验：链上交易的发送方必须为当前连接钱包（防止代记账）
    if (tx.sender.toLowerCase() !== req.wallet.toLowerCase()) {
      return res.status(403).json({ ok: false, msg: '交易发送方与当前钱包不一致' });
    }
    // 登记镜像索引（链上为准，此处仅加速查询）
    await db.execute(
      `INSERT INTO tx_index (tx_id, tx_hash, from_addr, to_addr, amount_wei, timestamp, risk_level, signature)
       VALUES (?,?,?,?,?,?,?,?)
       ON DUPLICATE KEY UPDATE signature=VALUES(signature)`,
      [tx.txId, txHash, tx.sender, tx.receiver, tx.amountWei, tx.timestamp, tx.riskLevel, tx.signature || ''],
    );
    const balance = await chain.getBalance(req.wallet);
    res.json({
      ok: true,
      msg: '交易已上链存证',
      txId: tx.txId,
      txHash,
      from: tx.sender,
      to: tx.receiver,
      amountWei: tx.amountWei,
      amountEth: tx.amountEth,
      riskLevel: tx.riskLevel,
      riskText: tx.riskText,
      timestamp: tx.timestamp,
      nonce: tx.nonce,
      fromBalanceWei: balance.wei,
      fromBalanceEth: balance.eth,
    });
  } catch (e) {
    res.status(400).json({ ok: false, msg: '交易回执确认失败：' + e.message });
  }
});

/**
 * GET /api/tx/mine
 * 当前钱包历史交易（链上为准；链未连接时返回空数组并标记 chain_down）
 */
app.get('/api/tx/mine', requireWallet, async (req, res) => {
  try {
    const all = await chain.getAllTransactions();
    const mine = all.filter(
      (t) => t.sender.toLowerCase() === req.wallet.toLowerCase() ||
             t.receiver.toLowerCase() === req.wallet.toLowerCase(),
    ).reverse();
    res.json({ ok: true, transactions: mine, chain_down: !chain.isChainUp(), chain_msg: chain.chainDownMsg() });
  } catch (e) {
    res.status(500).json({ ok: false, msg: '查询失败：' + e.message });
  }
});

/**
 * GET /api/tx/list
 * 链上全部交易记录查询（监管端），支持按风险等级筛选
 * 入参：?risk_level=3   （1低/2中/3高，缺省返回全部）
 */
app.get('/api/tx/list', requireAdmin, async (req, res) => {
  try {
    const riskFilter = req.query.risk_level ? Number(req.query.risk_level) : null;
    let all = await chain.getAllTransactions();
    if (riskFilter && [1, 2, 3].includes(riskFilter)) {
      all = all.filter((t) => t.riskLevel === riskFilter);
    }
    res.json({ ok: true, total: all.length, risk_level: riskFilter, transactions: all.reverse(),
      chain_down: !chain.isChainUp(), chain_msg: chain.chainDownMsg() });
  } catch (e) {
    res.status(500).json({ ok: false, msg: '查询失败：' + e.message });
  }
});

/**
 * GET /api/admin/risk-tx
 * 风险交易查询（监管端）：仅返回中/高风险交易，支持按等级筛选
 */
app.get('/api/admin/risk-tx', requireAdmin, async (req, res) => {
  try {
    const riskFilter = req.query.risk_level ? Number(req.query.risk_level) : null;
    let all = await chain.getAllTransactions();
    let filtered = all.filter((t) => t.riskLevel >= 2);
    if (riskFilter && [2, 3].includes(riskFilter)) {
      filtered = filtered.filter((t) => t.riskLevel === riskFilter);
    }
    res.json({ ok: true, total: filtered.length, transactions: filtered.reverse(),
      chain_down: !chain.isChainUp(), chain_msg: chain.chainDownMsg() });
  } catch (e) {
    res.status(500).json({ ok: false, msg: '查询失败：' + e.message });
  }
});

// =====================================================================
// 三、报案相关接口（用户仅提交材料，不自动触发溯源取证）
// =====================================================================

/**
 * POST /api/report/submit
 * 报案提交：用户选择涉案交易（链上交易ID）并提交交易签名与描述材料。
 * 后端仅保存报案材料并计算证据哈希上链存证；不会自动触发溯源取证。
 * 溯源取证权限由监管账户在后端手动发起。
 * 入参：{ "txId": 1, "txSignature": "0x...", "caseDesc": "..." }
 * 出参：{ ok, caseId, caseNo, status:"submitted", evidenceHash, chainEvidenceHash }
 */
app.post('/api/report/submit', requireWallet, async (req, res) => {
  try {
    const txId = Number(req.body.txId);
    const txSignature = String(req.body.txSignature || '').trim();
    const caseDesc = String(req.body.caseDesc || '').trim().slice(0, 1000);

    if (!Number.isInteger(txId) || txId <= 0) {
      return res.status(400).json({ ok: false, msg: '请选择涉案交易' });
    }
    if (!txSignature) {
      return res.status(400).json({ ok: false, msg: '请提交涉案交易的签名（需与链上签名一致）' });
    }

    // 读取链上涉案交易
    const onChainTx = await chain.getTransaction(txId);
    if (!onChainTx) {
      return res.status(404).json({ ok: false, msg: '链上未找到该交易，无法报案' });
    }

    const caseNo = 'CASE' + Date.now() + Math.floor(Math.random() * 9000 + 1000);

    // 计算报案材料证据哈希并上链存证
    const evidencePayload = {
      caseNo,
      txId,
      reporter: req.wallet,
      amountWei: onChainTx.amountWei,
      timestamp: onChainTx.timestamp,
      caseDesc,
      txSignature,
    };
    const evidenceHash = realname.caseEvidenceHash(evidencePayload);
    const chainEvidenceHash = await chain.recordCaseEvidenceOnChain(caseNo, evidenceHash);

    // 落库：案件材料（含用户提交的交易签名，供审核比对）
    const result = await db.execute(
      `INSERT INTO cases (case_no, reporter_addr, tx_id, tx_signature, amount_wei, case_desc,
                          status, evidence_hash, chain_evidence_hash)
       VALUES (?,?,?,?,?,?,?,?,?)`,
      [caseNo, req.wallet, txId, txSignature, onChainTx.amountWei, caseDesc,
       'submitted', evidenceHash, chainEvidenceHash],
    );

    res.json({
      ok: true,
      msg: '报案材料已提交并完成链上存证，等待监管方审核',
      caseId: result.insertId,
      caseNo,
      status: 'submitted',
      evidenceHash,
      chainEvidenceHash,
    });
  } catch (e) {
    res.status(500).json({ ok: false, msg: '报案提交失败：' + e.message });
  }
});

/**
 * GET /api/report/my
 * 我的报案列表（用户端）
 */
app.get('/api/report/my', requireWallet, async (req, res) => {
  try {
    const rows = await db.query(
      'SELECT * FROM cases WHERE reporter_addr=? ORDER BY created_at DESC', [req.wallet]);
    res.json({ ok: true, cases: rows.map(serializeCase) });
  } catch (e) {
    res.status(500).json({ ok: false, msg: '查询失败：' + e.message });
  }
});

/**
 * GET /api/report/all
 * 全部报案案件列表（监管端）
 */
app.get('/api/report/all', requireAdmin, async (req, res) => {
  try {
    const rows = await db.query('SELECT * FROM cases ORDER BY created_at DESC');
    res.json({ ok: true, cases: rows.map(serializeCase) });
  } catch (e) {
    res.status(500).json({ ok: false, msg: '查询失败：' + e.message });
  }
});

/**
 * GET /api/report/detail?caseId=
 * 案件详情：报案材料 + 链上交易签名 + 签名比对结果 + 链上证据核验
 * （报案人或监管管理员可查看）
 */
app.get('/api/report/detail', requireWallet, async (req, res) => {
  try {
    const caseId = Number(req.query.caseId);
    const row = await db.queryOne('SELECT * FROM cases WHERE id=?', [caseId]);
    if (!row) return res.status(404).json({ ok: false, msg: '案件不存在' });
    if (!req.isAdmin && row.reporter_addr.toLowerCase() !== req.wallet.toLowerCase()) {
      return res.status(403).json({ ok: false, msg: '无权查看该案件' });
    }

    const onChainTx = row.tx_id ? await chain.getTransaction(row.tx_id) : null;
    const onChainSig = onChainTx ? onChainTx.signature : null;
    const onChainEvidence = await chain.getCaseEvidenceHashOnChain(row.case_no);

    res.json({
      ok: true,
      case: serializeCase(row),
      onChainTx,
      signatureMatch: onChainSig ? sig.compareSignatures(onChainSig, row.tx_signature || '') : null,
      onChainEvidence,
      evidenceVerified: !!(onChainEvidence && onChainEvidence === row.evidence_hash),
    });
  } catch (e) {
    res.status(500).json({ ok: false, msg: '查询失败：' + e.message });
  }
});

/**
 * POST /api/report/audit
 * 报案审核（监管端）：
 *   系统自动调取链上原始交易签名与用户报案提交的交易签名做比对：
 *   - 签名一致   -> 审核通过（approved）
 *   - 签名不一致 -> 驳回报案，并标记为恶意报案（malicious）
 * 入参：{ "caseId": 1, "remark": "..." }
 */
app.post('/api/report/audit', requireAdmin, async (req, res) => {
  try {
    const caseId = Number(req.body.caseId);
    const remark = String(req.body.remark || '').trim().slice(0, 500);
    const row = await db.queryOne('SELECT * FROM cases WHERE id=?', [caseId]);
    if (!row) return res.status(404).json({ ok: false, msg: '案件不存在' });
    if (row.status !== 'submitted') {
      return res.status(400).json({ ok: false, msg: '该案件已审核，请勿重复操作' });
    }

    // 自动比对：链上原始交易签名 vs 用户报案提交的交易签名
    const onChainTx = await chain.getTransaction(row.tx_id);
    if (!onChainTx) {
      return res.status(400).json({ ok: false, msg: '链上未找到涉案交易，无法审核' });
    }
    const match = sig.compareSignatures(onChainTx.signature, row.tx_signature || '');
    const status = match ? 'approved' : 'malicious';

    await db.execute(
      `UPDATE cases SET status=?, signature_match=?, audit_remark=?, audited_at=NOW()
       WHERE id=?`,
      [status, match ? 1 : 0, match ? remark || '签名一致，审核通过' : remark || '签名不一致，驳回并标记为恶意报案', caseId],
    );

    res.json({
      ok: true,
      msg: match ? '审核通过：链上交易签名与报案签名一致' : '已驳回：签名不一致，该报案已标记为恶意报案',
      status,
      signatureMatch: match,
      caseId,
    });
  } catch (e) {
    res.status(500).json({ ok: false, msg: '审核失败：' + e.message });
  }
});

// =====================================================================
// 四、资金溯源取证接口（监管端手动发起）
// =====================================================================

/**
 * POST /api/trace/run
 * 案件溯源取证（监管端）：审核通过后可对案件执行资金溯源，
 * 程序自动遍历链上资金流转链路，生成结构化证据包并上链存证。
 * 入参：{ "caseId": 1 }
 */
app.post('/api/trace/run', requireAdmin, async (req, res) => {
  try {
    const caseId = Number(req.body.caseId);
    const row = await db.queryOne('SELECT * FROM cases WHERE id=?', [caseId]);
    if (!row) return res.status(404).json({ ok: false, msg: '案件不存在' });
    if (row.status !== 'approved') {
      return res.status(400).json({ ok: false, msg: '仅审核通过的案件可执行溯源取证' });
    }

    // 读取链上全部交易并自动遍历资金链路
    const allTx = await chain.getAllTransactions();
    const traceResult = traceModule.traceFundFlow(allTx, row.tx_id);
    const evidence = traceModule.buildEvidencePackage(row, traceResult);

    // 溯源报告哈希上链存证
    const reportHash = realname.caseEvidenceHash({
      caseNo: row.case_no, txId: row.tx_id, nodes: evidence.topology.nodes.length,
      links: evidence.topology.links.length, generatedAt: Date.now(),
    });
    await chain.recordCaseEvidenceOnChain(row.case_no + '_REPORT', reportHash);

    // 先落库溯源状态与时间，再用最新行构建证据包（保证 evidence.case.tracedAt 等字段完整）
    await db.execute(
      `UPDATE cases SET status='traced', report_hash=?, traced_at=NOW() WHERE id=?`,
      [reportHash, caseId],
    );
    const freshRow = await db.queryOne('SELECT * FROM cases WHERE id=?', [caseId]);
    const fullEvidence = traceModule.buildEvidencePackage(freshRow, traceResult);
    await db.execute(`UPDATE cases SET report_json=? WHERE id=?`, [JSON.stringify(fullEvidence), caseId]);

    res.json({ ok: true, msg: '溯源取证完成，结构化证据已生成并上链存证', caseId, reportHash, evidence: fullEvidence });
  } catch (e) {
    res.status(500).json({ ok: false, msg: '溯源取证失败：' + e.message });
  }
});

/**
 * GET /api/trace/detail?caseId=
 * 溯源取证结果（结构化证据包：拓扑 + 交易明细 + 风险账户清单）
 */
app.get('/api/trace/detail', requireAdmin, async (req, res) => {
  try {
    const caseId = Number(req.query.caseId);
    const row = await db.queryOne('SELECT * FROM cases WHERE id=?', [caseId]);
    if (!row) return res.status(404).json({ ok: false, msg: '案件不存在' });
    res.json({
      ok: true,
      case: serializeCase(row),
      evidence: row.report_json ? JSON.parse(row.report_json) : null,
    });
  } catch (e) {
    res.status(500).json({ ok: false, msg: '查询失败：' + e.message });
  }
});

// =====================================================================
// 五、实名信息哈希存证接口（明文不上链，仅存哈希）
// =====================================================================

// =====================================================================
// 六、监管总览与系统状态
// =====================================================================

/**
 * GET /api/admin/stats
 * 监管数据总览大屏统计
 */
app.get('/api/admin/stats', requireAdmin, async (req, res) => {
  try {
    const caseRows = await db.query('SELECT COUNT(*) c FROM cases');
    const submittedRows = await db.query(`SELECT COUNT(*) c FROM cases WHERE status='submitted'`);
    const approvedRows = await db.query(`SELECT COUNT(*) c FROM cases WHERE status IN ('approved','traced')`);
    const maliciousRows = await db.query(`SELECT COUNT(*) c FROM cases WHERE status='malicious'`);
    const tracedRows = await db.query(`SELECT COUNT(*) c FROM cases WHERE status='traced'`);
    const riskDistRows = await db.query('SELECT risk_level, COUNT(*) c FROM tx_index GROUP BY risk_level');
    const recentCaseRows = await db.query('SELECT * FROM cases ORDER BY created_at DESC LIMIT 10');
    const caseStatusRows = await db.query('SELECT status, COUNT(*) c FROM cases GROUP BY status');

    const chainStatus = await chain.getChainStatus();
    const allTx = await chain.getAllTransactions();
    const riskLevelDist = { 1: 0, 2: 0, 3: 0 };
    allTx.forEach((t) => { riskLevelDist[t.riskLevel] = (riskLevelDist[t.riskLevel] || 0) + 1; });

    res.json({
      ok: true,
      stats: {
        caseCount: caseRows[0]?.c || 0,
        submittedCount: submittedRows[0]?.c || 0,
        approvedCount: approvedRows[0]?.c || 0,
        maliciousCount: maliciousRows[0]?.c || 0,
        tracedCount: tracedRows[0]?.c || 0,
        txCount: allTx.length,
        riskLevelDist,
        riskDist: [
          { name: '低风险', value: riskLevelDist[1] || 0 },
          { name: '中风险', value: riskLevelDist[2] || 0 },
          { name: '高风险', value: riskLevelDist[3] || 0 },
        ],
        caseStatusDist: (caseStatusRows || []).map((r) => ({
          name: ({ submitted: '待审核', approved: '已通过', rejected: '已驳回', malicious: '恶意报案', traced: '已溯源' })[r.status] || r.status,
          value: r.c,
        })),
        recentCases: (recentCaseRows || []).map(serializeCase),
        recentTxs: allTx.slice(-10).reverse(),
        chainStatus,
      },
    });
  } catch (e) {
    res.status(500).json({ ok: false, msg: '统计失败：' + e.message });
  }
});

/**
 * GET /api/chain/status
 * Ganache 与合约连接状态（含合约地址与 ABI，供前端 MetaMask 调用转账合约）
 */
app.get('/api/chain/status', async (req, res) => {
  const status = await chain.getChainStatus();
  res.json({
    ok: true,
    ...status,
    contract_address: config.CONTRACT_ADDRESS,
    abi: chain.contractAbi,
    adminWallets: config.ADMIN_WALLETS,
  });
});

/**
 * GET /api/wallet/balance
 * 当前钱包 Ganache 实时余额（ETH 展示，底层 wei）
 * 链未连接时返回 0 余额并标记 chain_down（不返回 500）
 */
app.get('/api/wallet/balance', requireWallet, async (req, res) => {
  try {
    const balance = await chain.getBalance(req.wallet);
    res.json({
      ok: true,
      address: req.wallet,
      wei: balance.wei,
      eth: balance.eth,
      chain_down: !chain.isChainUp(),
      chain_msg: chain.chainDownMsg(),
    });
  } catch (e) {
    res.status(500).json({ ok: false, msg: '余额查询失败：' + e.message });
  }
});

// =====================================================================
// 案件序列化（统一输出）
// =====================================================================
function serializeCase(row) {
  if (!row) return null;
  return {
    id: row.id,
    caseNo: row.case_no,
    reporterAddr: row.reporter_addr,
    txId: row.tx_id,
    txSignature: row.tx_signature,
    amountWei: row.amount_wei,
    amountEth: row.amount_wei ? ethers.utils.formatEther(row.amount_wei) : null,
    caseDesc: row.case_desc,
    status: row.status,
    signatureMatch: row.signature_match,
    auditRemark: row.audit_remark,
    evidenceHash: row.evidence_hash,
    chainEvidenceHash: row.chain_evidence_hash,
    reportHash: row.report_hash,
    createdAt: row.created_at,
    auditedAt: row.audited_at,
    tracedAt: row.traced_at,
  };
}

// =====================================================================
// 前端静态页面托管
// =====================================================================
app.use(express.static(config.FRONTEND_DIR));
app.get('/', (req, res) => res.sendFile(path.join(config.FRONTEND_DIR, 'index.html')));

// 404 兜底
app.use((req, res) => {
  res.status(404).json({ ok: false, msg: '接口或页面不存在' });
});

// ---------------------------------------------------------------------
// 启动服务
// ---------------------------------------------------------------------
app.listen(config.PORT, async () => {
  console.log('='.repeat(60));
  console.log('  诈骗资金风险监测与报案溯源固证辅助系统（Node.js 后端）');
  console.log('  访问地址：http://127.0.0.1:' + config.PORT);
  console.log('  Ganache ：' + config.GANACHE_URL);
  console.log('  合约地址：' + config.CONTRACT_ADDRESS);
  console.log('='.repeat(60));

  try {
    await db.ping();
    console.log('[db] MySQL 连接成功');
  } catch (e) {
    console.warn('[db] MySQL 连接失败：' + e.message + '（请先执行 schema.sql 建表）');
  }

  const chainStatus = await chain.getChainStatus();
  if (chainStatus.ok) {
    console.log('[chain] Ganache 连接成功，区块高度：' + chainStatus.blockNumber +
      '，链上交易数：' + chainStatus.txCount);
  } else {
    console.warn('[chain] ' + chainStatus.msg + '（请先启动 Ganache 并执行 blockchain/deploy.js 部署合约）');
  }
});

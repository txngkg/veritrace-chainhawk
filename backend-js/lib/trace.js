/**
 * 资金溯源模块（自动程序遍历，非人工录入）
 * =========================================
 * 以涉案首笔交易为起点，基于链上全部交易构建资金流转图，BFS 穿透多层中转，
 * 输出结构化溯源证据（拓扑 + 交易明细 + 风险账户清单）。
 */
const config = require('../config');

/**
 * 构建链上交易图：address -> [{tx}]
 */
function _buildGraph(txs) {
  const outEdges = {};
  for (const t of txs) {
    (outEdges[t.sender] = outEdges[t.sender] || []).push(t);
  }
  // 按交易ID升序（确定时序）
  for (const k of Object.keys(outEdges)) {
    outEdges[k].sort((a, b) => a.txId - b.txId);
  }
  return outEdges;
}

/**
 * 从涉案起始交易开始 BFS 穿透资金链路
 * @param {Array}  txs        链上全部交易
 * @param {number} startTxId  涉案首笔交易ID
 * @returns {Object} { nodes, edges, initTx }
 */
function traceFundFlow(txs, startTxId) {
  const outEdges = _buildGraph(txs);
  const initTx = txs.find((t) => t.txId === startTxId) || null;
  if (!initTx) {
    throw new Error('链上未找到涉案交易（txId=' + startTxId + '）');
  }

  const { MAX_DEPTH, MAX_NODES, FOLLOW_OUT_LIMIT } = config.TRACE;
  const nodes = {};   // addr -> 节点信息
  const edges = [];   // 涉案交易边
  const visited = new Set();
  const queue = [];   // {addr, depth, inTx}

  // 涉案起点：受害账户节点
  const victim = initTx.sender;
  visited.add(victim);
  nodes[victim] = { address: victim, depth: 0, role: '受害账户', riskLevel: initTx.riskLevel, txCount: 0 };

  // 起点交易本身作为首条涉案边
  edges.push({ ...initTx, traceDepth: 1 });
  const firstReceiver = initTx.receiver;
  if (!visited.has(firstReceiver)) {
    visited.add(firstReceiver);
    nodes[firstReceiver] = { address: firstReceiver, depth: 1, role: '一级收款', riskLevel: initTx.riskLevel, txCount: 1 };
    queue.push({ addr: firstReceiver, depth: 1, inTx: initTx });
  }

  // 逐层穿透中转账户的后续转出
  while (queue.length && Object.keys(nodes).length < MAX_NODES) {
    const { addr, depth, inTx } = queue.shift();
    if (depth >= MAX_DEPTH) continue;
    const follows = (outEdges[addr] || [])
      .filter((t) => t.txId > inTx.txId)   // 仅追溯起始交易之后（按ID时序）
      .slice(0, FOLLOW_OUT_LIMIT);

    for (const f of follows) {
      if (Object.keys(nodes).length >= MAX_NODES) break;
      edges.push({ ...f, traceDepth: depth + 1 });
      nodes[addr].txCount = (nodes[addr].txCount || 0) + 1;
      if (!visited.has(f.receiver)) {
        visited.add(f.receiver);
        nodes[f.receiver] = {
          address: f.receiver, depth: depth + 1, role: '',
          riskLevel: f.riskLevel, txCount: 1,
        };
        queue.push({ addr: f.receiver, depth: depth + 1, inTx: f });
      }
    }
  }

  // 计算链路角色：一级收款 / 中转 / 归集终点
  const outSet = new Set(edges.map((e) => e.sender));
  for (const addr of Object.keys(nodes)) {
    const node = nodes[addr];
    if (node.depth === 0) continue;
    const roles = node.depth === 1 ? ['一级收款'] : [];
    roles.push(outSet.has(addr) ? '中转' : '归集终点');
    node.role = roles.join('/');
  }

  return { nodes, edges, initTx };
}

/**
 * 生成结构化取证证据包 JSON（监管端展示与报告生成）
 */
function buildEvidencePackage(caseRow, traceResult) {
  const { nodes, edges } = traceResult;
  const sortedEdges = [...edges].sort((a, b) => a.traceDepth - b.traceDepth || a.txId - b.txId);
  const riskAccounts = Object.values(nodes)
    .filter((n) => n.riskLevel >= 2)
    .sort((a, b) => b.riskLevel - a.riskLevel || a.depth - b.depth);

  return {
    case: {
      caseNo: caseRow.case_no,
      reporterAddr: caseRow.reporter_addr,
      txId: caseRow.tx_id,
      amountWei: caseRow.amount_wei,
      status: caseRow.status,
      createdAt: caseRow.created_at,
      tracedAt: caseRow.traced_at,
    },
    topology: {
      nodes: Object.values(nodes).map((n) => ({
        id: n.address,
        name: n.address,
        riskLevel: n.riskLevel,
        riskText: config.RISK_TEXT[n.riskLevel] || '未知',
        role: n.role,
        depth: n.depth,
        txCount: n.txCount,
      })),
      links: sortedEdges.map((e) => ({
        source: e.sender,
        target: e.receiver,
        txId: e.txId,
        amountWei: e.amountWei,
        amountEth: e.amountEth,
        timestamp: e.timestamp,
        riskLevel: e.riskLevel,
        riskText: config.RISK_TEXT[e.riskLevel] || '未知',
        signature: e.signature,
        traceDepth: e.traceDepth,
      })),
    },
    transactions: sortedEdges,
    riskAccounts,
  };
}

module.exports = { traceFundFlow, buildEvidencePackage };

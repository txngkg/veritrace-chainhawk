/**
 * 证据哈希模块
 * ================
 * 报案材料整体哈希计算（SHA256），用于证据固证与链上比对。
 */
const crypto = require('crypto');

/**
 * 计算案件材料证据哈希（报案材料整体哈希）
 * 将报案核心要素序列化为稳定 JSON 后计算 sha256，并转为 0x+64位。
 */
function caseEvidenceHash(payload) {
  const canonical = JSON.stringify(payload, Object.keys(payload).sort());
  return '0x' + crypto.createHash('sha256').update(canonical).digest('hex');
}

module.exports = { caseEvidenceHash };

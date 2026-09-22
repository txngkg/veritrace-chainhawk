/**
 * 风险监测规则引擎（金额阈值型）
 * =============================
 * 底层统一使用 wei 判断；前端展示单位为 ETH，由 ethers.js 自动转换。
 * 阈值与智能合约 FundTrace.riskLevelFromAmount 完全一致，保证三方判定一致。
 */
const { ethers } = require('ethers');
const config = require('../config');

const HIGH_WEI = ethers.BigNumber.from(config.RISK_THRESHOLDS.HIGH_WEI);     // 10 ETH
const MEDIUM_WEI = ethers.BigNumber.from(config.RISK_THRESHOLDS.MEDIUM_WEI); // 5 ETH

/**
 * 按金额（wei）判定风险等级
 * @param {string|number|BigNumber} amountWei
 * @returns {number} 1低风险 / 2中风险 / 3高风险
 */
function riskLevel(amountWei) {
  const a = ethers.BigNumber.from(amountWei);
  if (a.gte(HIGH_WEI)) return 3;        // >= 0.2 ETH -> 高风险
  if (a.gte(MEDIUM_WEI)) return 2;      // 0.05 ~ 0.2 ETH -> 中风险
  return 1;                              // < 0.05 ETH -> 低风险
}

/**
 * 风险等级文案
 */
function riskText(level) {
  return config.RISK_TEXT[level] || '未知';
}

/**
 * 风险等级颜色
 */
function riskColor(level) {
  return config.RISK_COLOR[level] || '#94a3b8';
}

/**
 * 校验 ETH 金额字符串是否为合法小数，并转换为 wei
 * @param {string} ethAmount 用户输入（ETH，支持小数，如 0.123）
 * @returns {string} wei 字符串
 */
function ethToWei(ethAmount) {
  const s = String(ethAmount).trim();
  if (!/^\d+(\.\d+)?$/.test(s)) {
    throw new Error('金额格式不正确，请输入有效 ETH 数值');
  }
  const wei = ethers.utils.parseEther(s);
  if (wei.isZero() || wei.isNegative()) {
    throw new Error('金额必须大于 0');
  }
  return wei.toString();
}

module.exports = { riskLevel, riskText, riskColor, ethToWei };

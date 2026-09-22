/**
 * 签名比对 / 验签模块
 * ===================
 * 交易签名消息格式（前端 / 后端 / 智能合约三方完全一致）：
 *   msgHash = keccak256(abi.encodePacked(receiver, amountWei, nonce))
 * 用户通过 MetaMask personal_sign 对 msgHash 签名，得到 65 字节签名。
 */
const { ethers } = require('ethers');

/**
 * 计算交易签名消息哈希
 * @param {string} receiver 接收方地址
 * @param {string} amountWei 金额（wei）
 * @param {string} nonce 随机数
 * @returns {string} 0x+64位 消息哈希
 */
function txMessageHash(receiver, amountWei, nonce) {
  return ethers.utils.solidityKeccak256(
    ['address', 'uint256', 'uint256'],
    [receiver, amountWei, nonce],
  );
}

/**
 * 从签名中恢复签名者地址（与合约 ecrecover + Ethereum Signed Message 前缀一致）
 * @param {string} messageHash 0x+64位 消息哈希
 * @param {string} signature   MetaMask personal_sign 产物
 * @returns {string} 签名者地址
 */
function recoverSigner(messageHash, signature) {
  return ethers.utils.verifyMessage(ethers.utils.arrayify(messageHash), signature);
}

/**
 * 校验签名是否由指定钱包发出
 */
function verifySignature(messageHash, signature, expectedAddress) {
  try {
    const recovered = recoverSigner(messageHash, signature);
    return recovered.toLowerCase() === String(expectedAddress).toLowerCase();
  } catch (e) {
    return false;
  }
}

/**
 * 比对两个交易签名是否一致（报案审核核心逻辑）
 * @param {string} onChainSignature 链上原始交易签名（合约读取）
 * @param {string} reportSignature  用户报案提交的交易签名
 * @returns {boolean} true 一致 / false 不一致
 */
function compareSignatures(onChainSignature, reportSignature) {
  if (!onChainSignature || !reportSignature) return false;
  return onChainSignature.toLowerCase() === reportSignature.toLowerCase();
}

/**
 * 校验以太坊地址格式并返回校验和地址
 */
function normalizeAddress(address) {
  if (!ethers.utils.isAddress(address)) {
    throw new Error('钱包地址格式不正确');
  }
  return ethers.utils.getAddress(address);
}

module.exports = { txMessageHash, recoverSigner, verifySignature, compareSignatures, normalizeAddress };

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title FundTrace
 * @notice 诈骗资金风险监测与报案溯源固证辅助系统 —— 核心存证智能合约（Ganache 私有链模拟环境）
 *
 * 功能定位：
 *  1. 转账上链：用户通过 MetaMask 调用 transferEth 完成 ETH 转账，
 *     合约自动记录 发送方 / 接收方 / 金额(wei) / 时间戳 / 风险等级 / 交易签名；
 *  2. 风险判定：合约内嵌金额阈值规则（底层统一使用 wei），
 *     每笔转账到账时自动打上风险等级标签并随交易永久上链；
 *  3. 实名哈希存证：仅存储用户实名信息的哈希值（bytes32），实名明文绝不上链；
 *  4. 案件证据存证：报案材料整体哈希上链，供监管端核验真伪。
 *
 * 安全约定：
 *  - 所有 ETH 转账必须携带用户 MetaMask 签名（personal_sign 产物），合约在链上完成验签，
 *    验签通过后才放行转账，保证"交易必须经用户钱包签名"；
 *  - 签名消息格式（前端 / 后端 / 合约三方完全一致，可交叉核验）：
 *        msgHash = keccak256(abi.encodePacked(receiver, amountWei, nonce))
 *    再对 msgHash 施加 Ethereum Signed Message 前缀后由用户私钥签名；
 *  - 同一 msgHash 仅允许使用一次，防止签名重放。
 *
 * 风险等级映射（阈值单位 wei）：
 *  - 金额 >= 200000000000000000 wei（0.2 ETH）           -> 3 高风险
 *  - 50000000000000000 wei（0.05 ETH） <= 金额 < 0.2 ETH  -> 2 中风险
 *  - 金额 < 50000000000000000 wei（0.05 ETH）             -> 1 低风险
 */
contract FundTrace {

    /* ============================ 状态变量 ============================ */
    address public owner;                // 合约部署者（系统管理方）
    uint256 public txCount;              // 已上链交易总数

    /// @notice 风险阈值常量（wei）
    uint256 public constant HIGH_RISK_THRESHOLD  = 10000000000000000000; // 10 ETH
    uint256 public constant MEDIUM_RISK_THRESHOLD = 5000000000000000000; // 5 ETH

    /// @notice 链上交易记录结构体
    struct TxRecord {
        uint256 txId;          // 交易ID（自增主键，用于确定交易时序）
        address sender;        // 发送方钱包地址
        address receiver;      // 接收方钱包地址
        uint256 amountWei;     // 转账金额（wei）
        uint256 timestamp;     // 交易时间戳（Unix 秒）
        uint256 nonce;         // 签名随机数（防重放）
        uint8   riskLevel;     // 风险等级：1低 / 2中 / 3高
        bytes   signature;     // 用户 MetaMask 签名（65 字节）
        bool    exists;        // 记录是否存在
    }
    mapping(uint256 => TxRecord) private txs;        // txId => 交易记录
    mapping(bytes32 => bool) private usedMsgHashes;  // 已使用签名消息哈希（防重放）

    /// @notice 实名信息哈希存证：walletAddr => keccak256(name, idCard)
    mapping(address => bytes32) private realNameHashes;

    /// @notice 案件证据哈希存证：caseNo => 证据整体哈希
    mapping(string => bytes32) private caseEvidenceHashes;

    /* ============================ 事件 ============================ */
    event TxRecorded(uint256 indexed txId, address indexed sender, address indexed receiver,
                     uint256 amountWei, uint256 timestamp, uint8 riskLevel);
    event RealNameRecorded(address indexed account, bytes32 nameHash, uint256 timestamp);
    event CaseEvidenceRecorded(string caseNo, bytes32 evidenceHash, uint256 timestamp);

    /* ============================ 构造函数 ============================ */
    constructor() {
        owner = msg.sender;
    }

    /* ============================ 风险等级判定 ============================ */

    /**
     * @notice 按金额（wei）判定风险等级：3高 / 2中 / 1低
     *         前端展示、后端风控与合约判定使用同一套阈值，保证三方一致。
     */
    function riskLevelFromAmount(uint256 amountWei) public pure returns (uint8) {
        if (amountWei >= HIGH_RISK_THRESHOLD) return 3;
        if (amountWei >= MEDIUM_RISK_THRESHOLD) return 2;
        return 1;
    }

    /* ============================ 转账上链（需 MetaMask 签名） ============================ */

    /// @notice 将 msgHash 施加 Ethereum Signed Message 前缀（与 MetaMask personal_sign 一致）
    function _toEthSignedMessageHash(bytes32 h) private pure returns (bytes32) {
        return keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", h));
    }

    /// @notice 从签名中恢复签名者地址（支持 v=27/28 或 0/1）
    function _recover(bytes32 msgHash, bytes calldata signature) private pure returns (address) {
        require(signature.length == 65, "invalid signature length");
        // calldata 数组无法在 assembly 中直接 mload，先整体拷贝到 memory
        bytes memory sig = signature;
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := mload(add(sig, 32))
            s := mload(add(sig, 64))
            v := byte(0, mload(add(sig, 96)))
        }
        if (v < 27) v += 27;
        return ecrecover(_toEthSignedMessageHash(msgHash), v, r, s);
    }

    /**
     * @notice 链上转账：用户经 MetaMask 调用本函数并携带 ETH，
     *         合约验签通过后自动转发 ETH 给接收方，同时记录完整交易元数据。
     * @param to        接收方地址
     * @param nonce     后端签发的随机数（防止签名重放）
     * @param signature 用户对 msgHash 的 MetaMask 签名
     * @return txId     链上交易ID
     */
    function transferEth(address payable to, uint256 nonce, bytes calldata signature)
        external payable returns (uint256)
    {
        require(msg.value > 0, "FundTrace: amount must be > 0");
        require(to != address(0) && to != msg.sender, "FundTrace: invalid receiver");

        // 计算签名消息哈希并链上验签，确保转账意图确由本人签名
        bytes32 msgHash = keccak256(abi.encodePacked(to, msg.value, nonce));
        require(!usedMsgHashes[msgHash], "FundTrace: signature reused");
        require(_recover(msgHash, signature) == msg.sender, "FundTrace: signature verify failed");
        usedMsgHashes[msgHash] = true;

        // 自动判定风险等级并记录交易（金额单位 wei）
        uint256 id = ++txCount;
        txs[id] = TxRecord(
            id, msg.sender, to, msg.value, block.timestamp, nonce,
            riskLevelFromAmount(msg.value), signature, true
        );

        // 将 ETH 转发给接收方，实现链上真实余额变更
        (bool ok, ) = to.call{value: msg.value}("");
        require(ok, "FundTrace: ETH transfer failed");

        emit TxRecorded(id, msg.sender, to, msg.value, block.timestamp, txs[id].riskLevel);
        return id;
    }

    /* ============================ 实名信息哈希存证 ============================ */

    /**
     * @notice 实名信息哈希上链存证（由后端统一写入，明文绝不上链）
     * @param account  用户钱包地址
     * @param nameHash 实名信息哈希 keccak256(abi.encodePacked(realName, idCardNo))
     */
    function recordRealName(address account, bytes32 nameHash) external {
        require(account != address(0), "FundTrace: invalid account");
        realNameHashes[account] = nameHash;
        emit RealNameRecorded(account, nameHash, block.timestamp);
    }

    /* ============================ 案件证据存证 ============================ */

    /**
     * @notice 报案材料/溯源报告整体哈希上链存证，同一案件仅允许存证一次
     * @param caseNo       案件编号（唯一标识）
     * @param evidenceHash 证据内容整体哈希
     */
    function recordCaseEvidence(string calldata caseNo, bytes32 evidenceHash) external {
        require(bytes(caseNo).length > 0, "FundTrace: empty caseNo");
        require(caseEvidenceHashes[caseNo] == bytes32(0), "FundTrace: evidence already recorded");
        caseEvidenceHashes[caseNo] = evidenceHash;
        emit CaseEvidenceRecorded(caseNo, evidenceHash, block.timestamp);
    }

    /* ============================ 查询接口（供后端/监管核验） ============================ */

    /**
     * @notice 按交易ID查询链上交易完整数据（含风险等级与交易签名）
     * @return txId, sender, receiver, amountWei, timestamp, nonce, riskLevel, signature, exists
     */
    function getTransaction(uint256 txId) external view returns (
        uint256, address, address, uint256, uint256, uint256, uint8, bytes memory, bool
    ) {
        TxRecord storage t = txs[txId];
        return (t.txId, t.sender, t.receiver, t.amountWei, t.timestamp, t.nonce,
                t.riskLevel, t.signature, t.exists);
    }

    /// @notice 链上已记录交易总数（供后端遍历全量交易）
    function getTxCount() external view returns (uint256) {
        return txCount;
    }

    /// @notice 查询指定钱包的实名信息哈希（不存在返回 0x000...0）
    function getRealNameHash(address account) external view returns (bytes32) {
        return realNameHashes[account];
    }

    /// @notice 查询案件证据哈希（不存在返回 0x000...0）
    function getCaseEvidenceHash(string calldata caseNo) external view returns (bytes32) {
        return caseEvidenceHashes[caseNo];
    }
}

-- =====================================================================
-- 诈骗资金风险监测与报案溯源固证辅助系统 —— 新架构建表脚本（Node.js 后端）
-- =====================================================================
-- 架构说明：
--   * 账号体系完全移除：不再使用数据库存储账号/密码，用户身份 = 钱包地址；
--   * 交易数据以 Ganache 链上为准，tx_index 仅为快速查询的镜像索引；
--   * 案件材料保存在 cases 表，证据哈希上链存证；
--   * 实名信息仅存哈希（name_hash），明文绝不上链、不落库。
--
-- 执行方式：mysql -u root -p < schema.sql
-- =====================================================================

CREATE DATABASE IF NOT EXISTS fund_trace DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
USE fund_trace;

-- ---------------------------------------------------------------------
-- 清空旧架构：删除预置测试账号 / 假数据 / 旧表（账号体系已移除）
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS users;             -- 旧用户名密码账号表（弃用）
DROP TABLE IF EXISTS audit_logs;        -- 旧审核日志
DROP TABLE IF EXISTS case_accounts;     -- 旧溯源账户关联表
DROP TABLE IF EXISTS case_transactions; -- 旧溯源交易关联表
DROP TABLE IF EXISTS risk_records;      -- 旧六维规则风险记录
DROP TABLE IF EXISTS transactions;      -- 旧交易表（改为链上为准）
DROP TABLE IF EXISTS cases;             -- 旧案件表（重建）

-- ---------------------------------------------------------------------
-- 案件表：用户报案材料 + 审核结果 + 溯源报告（证据哈希上链存证）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cases (
  id                INT AUTO_INCREMENT PRIMARY KEY COMMENT '案件ID',
  case_no           VARCHAR(32)  NOT NULL UNIQUE COMMENT '案件编号',
  reporter_addr     VARCHAR(42)  NOT NULL COMMENT '报案人钱包地址',
  tx_id             BIGINT       DEFAULT NULL COMMENT '涉案链上交易ID',
  tx_signature      TEXT         COMMENT '用户报案提交的交易签名',
  amount_wei        VARCHAR(40)  DEFAULT NULL COMMENT '涉案金额（wei）',
  case_desc         VARCHAR(1000) DEFAULT NULL COMMENT '报案描述材料',
  status            ENUM('submitted','approved','rejected','malicious','traced') NOT NULL DEFAULT 'submitted'
                                   COMMENT 'submitted待审核/approved已通过/rejected已驳回/malicious恶意报案/traced已溯源',
  signature_match   TINYINT      DEFAULT NULL COMMENT '签名比对：1一致/0不一致/NULL未审核',
  audit_remark      VARCHAR(500) DEFAULT NULL COMMENT '审核意见',
  evidence_hash     VARCHAR(66)  DEFAULT NULL COMMENT '报案材料证据哈希（本地）',
  chain_evidence_hash VARCHAR(66) DEFAULT NULL COMMENT '链上存证证据哈希',
  report_hash       VARCHAR(66)  DEFAULT NULL COMMENT '溯源报告哈希',
  report_json       MEDIUMTEXT   COMMENT '溯源取证证据包JSON',
  report_md         LONGTEXT     COMMENT '溯源取证报告Markdown',
  created_at        DATETIME     DEFAULT CURRENT_TIMESTAMP COMMENT '报案时间',
  audited_at        DATETIME     DEFAULT NULL COMMENT '审核时间',
  traced_at         DATETIME     DEFAULT NULL COMMENT '溯源时间',
  KEY idx_reporter (reporter_addr),
  KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='报案案件表';

-- ---------------------------------------------------------------------
-- 实名信息哈希表：仅存哈希（镜像链上存证，便于监管查询）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS real_names (
  id           INT AUTO_INCREMENT PRIMARY KEY COMMENT 'ID',
  wallet_addr  VARCHAR(42) NOT NULL UNIQUE COMMENT '钱包地址',
  name_hash    VARCHAR(66) NOT NULL COMMENT '实名信息哈希（keccak256）',
  chain_status ENUM('confirmed','pending') DEFAULT 'confirmed' COMMENT '链上存证状态',
  created_at   DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '存证时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='实名信息哈希存证表';

-- ---------------------------------------------------------------------
-- 交易索引表：链上交易镜像（链上为准，本表仅加速查询与风险筛选）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tx_index (
  id         INT AUTO_INCREMENT PRIMARY KEY COMMENT 'ID',
  tx_id      BIGINT       NOT NULL UNIQUE COMMENT '链上交易ID',
  tx_hash    VARCHAR(66)  NOT NULL COMMENT 'MetaMask签名广播的交易哈希',
  from_addr  VARCHAR(42)  NOT NULL COMMENT '发送方钱包地址',
  to_addr    VARCHAR(42)  NOT NULL COMMENT '接收方钱包地址',
  amount_wei VARCHAR(40)  NOT NULL COMMENT '转账金额（wei）',
  timestamp  BIGINT       NOT NULL COMMENT '链上时间戳（秒）',
  risk_level TINYINT      NOT NULL COMMENT '风险等级：1低/2中/3高',
  signature  TEXT         COMMENT 'MetaMask交易签名',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_from (from_addr),
  KEY idx_to (to_addr),
  KEY idx_risk (risk_level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='链上交易镜像索引表';

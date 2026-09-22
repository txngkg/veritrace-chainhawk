-- =====================================================================
-- 诈骗资金风险监测与报案溯源固证辅助系统 —— MySQL 建库建表脚本
-- 适用：MySQL 5.7+ / 8.0
-- 执行方式：mysql -u root -p < db.sql
-- =====================================================================

CREATE DATABASE IF NOT EXISTS fund_trace DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
USE fund_trace;

-- ---------------------------------------------------------------------
-- 用户表（含普通用户与监管管理员；普通用户即"账户"）
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS users;
CREATE TABLE users (
  id            INT AUTO_INCREMENT PRIMARY KEY COMMENT '用户ID',
  username      VARCHAR(50)  NOT NULL UNIQUE COMMENT '登录用户名',
  password_hash VARCHAR(255) NOT NULL COMMENT '密码哈希（werkzeug scrypt/pbkdf2）',
  account_id    VARCHAR(20)  NOT NULL UNIQUE COMMENT '账户ID（链上标识，如 ACCT0001）',
  real_name     VARCHAR(50)  DEFAULT NULL COMMENT '真实姓名',
  id_card_no    VARCHAR(18)  DEFAULT NULL COMMENT '身份证号',
  phone         VARCHAR(20)  DEFAULT NULL COMMENT '手机号',
  role          ENUM('user','admin') NOT NULL DEFAULT 'user' COMMENT '角色：user普通用户 / admin监管管理员',
  status        ENUM('pending','approved','rejected') NOT NULL DEFAULT 'pending' COMMENT '实名审核状态',
  risk_level    TINYINT      NOT NULL DEFAULT 0 COMMENT '账户风险等级 0正常/1低/2中/3高',
  balance       DECIMAL(20,2) NOT NULL DEFAULT 1000000.00 COMMENT '模拟账户余额（元）',
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '注册时间',
  approved_at   DATETIME     DEFAULT NULL COMMENT '实名审核通过时间',
  INDEX idx_risk (risk_level),
  INDEX idx_status (status)
) ENGINE=InnoDB COMMENT='用户/账户表';

-- ---------------------------------------------------------------------
-- 交易表（每笔转账实时上链，tx_hash 为链上唯一标识）
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS transactions;
CREATE TABLE transactions (
  id          INT AUTO_INCREMENT PRIMARY KEY COMMENT '本地自增ID',
  tx_hash     CHAR(66)     NOT NULL UNIQUE COMMENT '链上交易哈希 0x+64位',
  from_account VARCHAR(20) NOT NULL COMMENT '转出账户ID',
  to_account  VARCHAR(20)  NOT NULL COMMENT '接收账户ID',
  amount      DECIMAL(20,2) NOT NULL COMMENT '转账金额（元）',
  tx_time     DATETIME     NOT NULL COMMENT '交易时间',
  chain_status ENUM('pending','confirmed','simulated','failed') NOT NULL DEFAULT 'pending'
               COMMENT '上链状态：confirmed已上链 / simulated模拟哈希（无链环境降级）',
  risk_level  TINYINT      NOT NULL DEFAULT 0 COMMENT '交易风险等级 0正常/1低/2中/3高',
  rules_hit   VARCHAR(100) NOT NULL DEFAULT '' COMMENT '命中风险规则ID，逗号分隔，如 "1,3"',
  remark      VARCHAR(200) DEFAULT NULL COMMENT '转账备注',
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  INDEX idx_from (from_account),
  INDEX idx_to (to_account),
  INDEX idx_time (tx_time),
  INDEX idx_risk (risk_level)
) ENGINE=InnoDB COMMENT='全网交易流水表（链上链下双记录）';

-- ---------------------------------------------------------------------
-- 风险记录表（六维规则引擎命中记录，与链上 risk 记录一一对应）
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS risk_records;
CREATE TABLE risk_records (
  id          INT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
  tx_hash     CHAR(66)     NOT NULL COMMENT '关联交易哈希',
  account_id  VARCHAR(20)  NOT NULL COMMENT '风险行为归属账户',
  rule_ids    VARCHAR(50)  NOT NULL COMMENT '命中规则ID，如 "1,2"',
  rule_desc   VARCHAR(500) DEFAULT NULL COMMENT '规则命中描述',
  risk_level  TINYINT      NOT NULL COMMENT '该笔风险等级',
  chain_status VARCHAR(20) NOT NULL DEFAULT 'confirmed' COMMENT '上链状态',
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录时间',
  INDEX idx_tx (tx_hash),
  INDEX idx_account (account_id),
  INDEX idx_time (created_at)
) ENGINE=InnoDB COMMENT='风险行为记录表';

-- ---------------------------------------------------------------------
-- 报案案件表
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS cases;
CREATE TABLE cases (
  id              INT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
  case_no         VARCHAR(32)  NOT NULL UNIQUE COMMENT '案件编号（唯一标识）',
  reporter_id     INT          NOT NULL COMMENT '报案人用户ID',
  victim_account  VARCHAR(20)  NOT NULL COMMENT '受害账户ID',
  reported_amount DECIMAL(20,2) NOT NULL COMMENT '报案被骗金额（元）',
  time_start      DATETIME     NOT NULL COMMENT '受骗时间段-开始',
  time_end        DATETIME     NOT NULL COMMENT '受骗时间段-结束',
  case_desc       VARCHAR(500) DEFAULT NULL COMMENT '报案描述',
  first_tx_hash   CHAR(66)     DEFAULT NULL COMMENT '涉案起始交易哈希（溯源起点）',
  report_hash     CHAR(64)     DEFAULT NULL COMMENT '存证报告SHA256（64位）',
  chain_report_hash CHAR(66)   DEFAULT NULL COMMENT '报告哈希上链的交易哈希（无链时为空）',
  report_json     LONGTEXT     COMMENT '结构化证据包（含拓扑图数据）',
  report_md       LONGTEXT     COMMENT '取证报告 Markdown 全文',
  status          ENUM('submitted','processing','done','failed') NOT NULL DEFAULT 'processing'
                  COMMENT '案件状态：submitted待取证 / processing溯源中 / done完成 / failed失败',
  fail_reason     VARCHAR(200) DEFAULT NULL COMMENT '失败原因',
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '报案时间',
  INDEX idx_reporter (reporter_id),
  INDEX idx_victim (victim_account),
  INDEX idx_time (created_at)
) ENGINE=InnoDB COMMENT='诈骗报案案件表';

-- ---------------------------------------------------------------------
-- 案件-交易关联表（溯源结果：案件包含的全部涉案交易）
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS case_transactions;
CREATE TABLE case_transactions (
  id          INT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
  case_id     INT NOT NULL COMMENT '案件ID',
  tx_hash     CHAR(66) NOT NULL COMMENT '涉案交易哈希',
  trace_depth INT NOT NULL DEFAULT 1 COMMENT '溯源层级（1为一级收款）',
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_case (case_id),
  INDEX idx_tx (tx_hash)
) ENGINE=InnoDB COMMENT='案件涉案交易表';

-- ---------------------------------------------------------------------
-- 案件-账户表（溯源涉及账户及角色/风险信息）
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS case_accounts;
CREATE TABLE case_accounts (
  id          INT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
  case_id     INT NOT NULL COMMENT '案件ID',
  account_id  VARCHAR(20) NOT NULL COMMENT '涉案账户ID',
  role        VARCHAR(50) NOT NULL DEFAULT '' COMMENT '链路角色：受害账户/一级收款/中转/归集终点',
  risk_level  TINYINT NOT NULL DEFAULT 0 COMMENT '账户风险等级',
  rules_hit   VARCHAR(255) DEFAULT NULL COMMENT '累计命中规则ID（去重）',
  trace_depth INT NOT NULL DEFAULT 0 COMMENT '溯源深度（0为受害账户）',
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_case (case_id),
  INDEX idx_account (account_id)
) ENGINE=InnoDB COMMENT='案件涉案账户表';

-- ---------------------------------------------------------------------
-- 实名审核日志表
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS audit_logs;
CREATE TABLE audit_logs (
  id         INT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
  user_id    INT NOT NULL COMMENT '被审核用户ID',
  admin_id   INT DEFAULT NULL COMMENT '审核管理员ID',
  action     VARCHAR(50) NOT NULL COMMENT '动作：APPROVE/REJECT',
  remark     VARCHAR(255) DEFAULT NULL COMMENT '审核备注',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_user (user_id)
) ENGINE=InnoDB COMMENT='实名审核日志表';

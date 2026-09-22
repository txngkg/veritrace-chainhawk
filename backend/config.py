# -*- coding: utf-8 -*-
"""
全局配置模块
==============================
本模块统一管理：
  1. MySQL 数据库连接信息与常用数据库访问函数
  2. Ganache 联盟链连接信息与合约 ABI 加载
  3. 六维风控规则引擎的阈值参数
  4. 系统级常量（初始余额、密钥等）

说明：本项目为封闭模拟环境，所有配置均为本地模拟参数。
"""
import json
import os
import pymysql

# ---------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))          # backend/ 目录
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))  # 前端静态目录
CONTRACTS_FILE = os.path.join(BASE_DIR, "contracts.json")      # 合约部署产物

# ---------------------------------------------------------------------
# Flask 会话/令牌密钥（演示环境固定值，生产环境请改为随机）
# ---------------------------------------------------------------------
SECRET_KEY = "fundtrace-demo-secret-key-2026-competition"

# ---------------------------------------------------------------------
# MySQL 数据库配置（请按本机环境修改）
# ---------------------------------------------------------------------
DB_CONFIG = {
    "host": "127.0.0.1",      # MySQL 地址
    "port": 3306,             # MySQL 端口
    "user": "root",           # MySQL 用户名
    "password": "06gkG12!",   # MySQL 密码
    "database": "fund_trace", # 数据库名（由 db.sql 创建）
    "charset": "utf8mb4",
}

# ---------------------------------------------------------------------
# Ganache 联盟链配置
# ---------------------------------------------------------------------
GANACHE_URL = "http://127.0.0.1:8545"   # Ganache 默认 RPC 地址
GAS = 5000000                            # 交易 Gas 上限
GAS_PRICE = 20000000000                  # 20 Gwei，适配 Ganache

# ---------------------------------------------------------------------
# 系统常量
# ---------------------------------------------------------------------
INIT_BALANCE = 1000000.00   # 新用户初始模拟余额（元）
MAX_TRACE_DEPTH = 6         # 资金溯源最大深度（层）
MAX_TRACE_NODES = 60        # 资金溯源最大节点数
FOLLOW_OUT_LIMIT = 20       # 每个中转账户最多追溯的后续转出笔数

# 风险等级文案
RISK_LEVEL_TEXT = {0: "正常", 1: "低风险", 2: "中风险", 3: "高风险"}
RISK_LEVEL_COLOR = {0: "#2ecc71", 1: "#f1c40f", 2: "#f39c12", 3: "#e74c3c"}

# ---------------------------------------------------------------------
# 六维风控规则引擎阈值配置
# 所有时间窗口单位为秒，金额单位为元
# ---------------------------------------------------------------------
RULE_CONFIG = {
    # 规则1：大额拆分转出 —— 收到大额资金后短时间拆分为多笔小额分散转出
    "rule1": {
        "big_amount_threshold": 10000,   # 单笔入账 >= 该值视为"大额资金"
        "big_window": 1800,              # 大额入账回溯窗口（30分钟）
        "split_count": 3,                # 拆分转出笔数下限
        "split_single_max_ratio": 0.5,   # 单笔转出 <= 大额资金 * 该比例 视为"小额"
        "split_total_ratio": 0.8,        # 小额转出总额 >= 大额资金 * 该比例
    },
    # 规则2：短时高频转账 —— 短时间向多个陌生地址连续多笔转出
    "rule2": {
        "time_window": 600,              # 时间窗口（10分钟）
        "out_count": 4,                  # 转出笔数下限
        "distinct_addr": 3,              # 不同接收方数量下限
        "new_addr_min": 3,               # 其中"陌生地址"数量下限
    },
    # 规则3：纯中转洗钱账户 —— 入账后立刻全额转出、无资金留存
    "rule3": {
        "relay_window": 600,             # 入账后转出时间窗口（10分钟）
        "relay_min_ratio": 0.9,          # 转出总额 >= 入账额 * 该比例
        "relay_max_ratio": 1.1,          # 转出总额 <= 入账额 * 该比例（防止混入自有资金）
    },
    # 规则4：环形资金流转 —— 资金形成 A->B->C->A 闭环
    "rule4": {
        "cycle_window": 3600,            # 环形检测回溯窗口（1小时）
        "min_cycle_len": 3,              # 闭环至少包含的账户数
    },
    # 规则5：新账户异常收款 —— 新注册无历史交易账户短时间批量收账
    "rule5": {
        "new_account_age": 86400,        # 注册不超过该秒数视为"新账户"（24小时）
        "time_window": 600,              # 收款时间窗口（10分钟）
        "in_count": 3,                   # 收款笔数下限
    },
    # 规则6：分批资金归集 —— 多个账户持续向同一账户汇聚资金
    "rule6": {
        "time_window": 1800,             # 归集回溯窗口（30分钟）
        "distinct_from": 3,              # 不同来源账户数量下限
        "min_total": 10000,              # 归集总额下限（元）
    },
}

# ---------------------------------------------------------------------
# 数据库访问辅助函数（全局通用）
# ---------------------------------------------------------------------

def get_conn():
    """创建并返回一个 MySQL 连接（自动提交）"""
    return pymysql.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        database=DB_CONFIG["database"],
        charset=DB_CONFIG["charset"],
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def query(sql, args=None, one=False):
    """执行查询，返回 dict 列表；one=True 时返回单条 dict 或 None"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args or ())
            rows = cur.fetchall()
            return (rows[0] if rows else None) if one else rows
    finally:
        conn.close()


def execute(sql, args=None):
    """执行写操作（INSERT/UPDATE/DELETE），返回影响行数"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            n = cur.execute(sql, args or ())
        return n
    finally:
        conn.close()


# ---------------------------------------------------------------------
# 合约配置加载
# ---------------------------------------------------------------------

def load_contract_config():
    """
    加载 backend/contracts.json（由 blockchain/deploy.js 生成）。
    文件不存在时返回 None，后端将降级为本地模拟哈希模式。
    """
    if os.path.exists(CONTRACTS_FILE):
        try:
            with open(CONTRACTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

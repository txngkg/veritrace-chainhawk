# -*- coding: utf-8 -*-
"""
交易模块（核心：六维风控规则引擎）
==============================
功能接口：
  1. POST /api/tx/transfer            链上模拟转账（余额变动 + 实时上链 + 实时风控扫描）
  2. GET  /api/tx/my                  当前用户交易记录
  3. GET  /api/tx/all                 全网交易查询（监管端，支持过滤）
  4. GET  /api/tx/verify?tx_hash=     交易哈希链上核验（监管端/用户端通用）
  5. GET  /api/admin/risk_accounts    风险账户管理列表（监管端）
  6. GET  /api/admin/account_detail   账户交易与风险详情（监管端）

六大风险识别规则（纯规则引擎，无 AI / 机器学习）：
  规则1 大额拆分转出：收到大额资金后，短时间拆分为多笔小额分散转出
  规则2 短时高频转账：短时间内向多个陌生地址连续多笔转出
  规则3 纯中转洗钱账户：入账后立刻全额转出，无资金留存
  规则4 环形资金流转：资金形成闭环流转（A→B→C→A）
  规则5 新账户异常收款：新注册无历史交易账户，短时间批量收账
  规则6 分批资金归集：多个账户持续向同一账户汇聚资金

风险等级量化：命中0条=正常 / 1条=低风险 / 2条=中风险 / >=3条=高风险
"""
import time
from collections import deque
from datetime import datetime
from decimal import Decimal

from flask import Blueprint, jsonify, request

from blockchain import chain
from config import RULE_CONFIG, execute, query
from user import require_admin, require_user

bp = Blueprint("transaction", __name__, url_prefix="/api")

# 六维规则名称与说明映射
RULE_NAMES = {
    1: "大额拆分转出",
    2: "短时高频转账",
    3: "纯中转洗钱账户",
    4: "环形资金流转",
    5: "新账户异常收款",
    6: "分批资金归集",
}


def risk_text(level):
    """风险等级数值 -> 文案"""
    return {0: "正常", 1: "低风险", 2: "中风险", 3: "高风险"}.get(int(level), "正常")


# =====================================================================
# 一、六维风控规则引擎（逐条实现，纯规则判断）
# =====================================================================

def _rule1_large_split(sender, receiver, amount, now_ts):
    """
    规则1：大额拆分转出
    特征：账户在窗口内收到 >= 阈值的"大额资金"，随后将其中 80% 以上拆分为
          多笔小额（每笔 <= 大额资金的一半）转出至至少 2 个不同账户。
    目标：转出方（sender）
    """
    cfg = RULE_CONFIG["rule1"]
    # 1) 最近窗口内该账户收到的大额入账（按时间倒序取最近一笔作为基准）
    #    同时取出自增 id，用于精确定位"该笔入账之后"的交易（避免秒级时间戳
    #    精度不足导致同一秒内的转出被漏检）
    incomings = query(
        """SELECT id, amount FROM transactions
           WHERE to_account=%s AND UNIX_TIMESTAMP(tx_time) >= %s AND UNIX_TIMESTAMP(tx_time) <= %s
           ORDER BY tx_time DESC, id DESC""",
        (sender, now_ts - cfg["big_window"], now_ts),
    )
    large = [i for i in incomings if i["amount"] >= cfg["big_amount_threshold"]]
    if not large:
        return None
    ref = large[0]  # 最近一笔大额入账
    # 2) 该笔大额入账之后发生的全部转出（含当前这笔）——以自增 id 判定先后，
    #    保证同一秒内连续交易的先后顺序判定准确
    outs = query(
        """SELECT amount, to_account FROM transactions
           WHERE from_account=%s AND id > %s AND UNIX_TIMESTAMP(tx_time) <= %s""",
        (sender, ref["id"], now_ts),
    )
    # 3) 拆分特征判定（注意 Decimal 与 float 需统一为 float 后比较）
    ref_amount = float(ref["amount"])
    max_single = cfg["split_single_max_ratio"] * ref_amount
    small = [o for o in outs if float(o["amount"]) <= max_single]
    total_small = float(sum(o["amount"] for o in small))
    distinct_to = len(set(o["to_account"] for o in small))
    if (
        len(small) >= cfg["split_count"]
        and distinct_to >= 2
        and total_small >= cfg["split_total_ratio"] * ref_amount
    ):
        return {
            "rule_id": 1,
            "target": sender,
            "desc": (
                f"账户{sender}收到大额资金{ref_amount:.2f}元后，"
                f"短时间拆分为{len(small)}笔小额分散转出至{distinct_to}个账户，"
                f"拆分总额{total_small:.2f}元"
            ),
        }
    return None


def _rule2_high_freq(sender, receiver, amount, now_ts):
    """
    规则2：短时高频转账
    特征：时间窗口内转出笔数 >= 阈值，接收方数量 >= 阈值，且其中"陌生地址"
          （此前与该账户无任何交易往来）数量 >= 阈值。
    目标：转出方（sender）
    """
    cfg = RULE_CONFIG["rule2"]
    # 窗口内全部转出（含当前这笔）
    outs = query(
        """SELECT to_account FROM transactions
           WHERE from_account=%s AND UNIX_TIMESTAMP(tx_time) >= %s AND UNIX_TIMESTAMP(tx_time) <= %s""",
        (sender, now_ts - cfg["time_window"], now_ts),
    )
    if len(outs) < cfg["out_count"]:
        return None
    receivers = set(o["to_account"] for o in outs)
    if len(receivers) < cfg["distinct_addr"]:
        return None
    # 统计陌生地址数量（窗口开始之前无任何交易往来）
    strangers = []
    for r in receivers:
        c = query(
            """SELECT COUNT(*) c FROM transactions
               WHERE UNIX_TIMESTAMP(tx_time) < %s
                 AND ((from_account=%s AND to_account=%s) OR (from_account=%s AND to_account=%s))""",
            (now_ts - cfg["time_window"], sender, r, r, sender), one=True,
        )["c"]
        if c == 0:
            strangers.append(r)
    if len(strangers) >= cfg["new_addr_min"]:
        return {
            "rule_id": 2,
            "target": sender,
            "desc": (
                f"账户{sender}在{cfg['time_window'] // 60}分钟内连续转出{len(outs)}笔"
                f"至{len(receivers)}个不同地址，其中{len(strangers)}个为无历史交易的陌生地址"
            ),
        }
    return None


def _rule3_pure_relay(sender, receiver, amount, now_ts):
    """
    规则3：纯中转洗钱账户
    特征：入账后短时间内（窗口内）转出总额占入账额 90%~110%，资金过手即走、无留存。
    目标：转出方（sender）
    """
    cfg = RULE_CONFIG["rule3"]
    incomings = query(
        """SELECT id, amount FROM transactions
           WHERE to_account=%s AND UNIX_TIMESTAMP(tx_time) >= %s AND UNIX_TIMESTAMP(tx_time) <= %s
           ORDER BY tx_time DESC, id DESC""",
        (sender, now_ts - cfg["relay_window"], now_ts),
    )
    for inc in incomings:
        # 该笔入账之后到现在的全部转出（含当前这笔）——以自增 id 判定先后
        outs = query(
            """SELECT amount FROM transactions
               WHERE from_account=%s AND id > %s AND UNIX_TIMESTAMP(tx_time) <= %s""",
            (sender, inc["id"], now_ts),
        )
        total_out = float(sum(o["amount"] for o in outs))
        x = float(inc["amount"])
        if cfg["relay_min_ratio"] * x <= total_out <= cfg["relay_max_ratio"] * x:
            return {
                "rule_id": 3,
                "target": sender,
                "desc": (
                    f"账户{sender}收到入账{x:.2f}元后，在{cfg['relay_window'] // 60}分钟内"
                    f"全额转出{total_out:.2f}元，无资金留存，符合纯中转洗钱特征"
                ),
            }
    return None


def _rule4_cycle(sender, receiver, amount, now_ts):
    """
    规则4：环形资金流转
    特征：在回溯窗口的交易图中，从本笔交易的接收方出发能够沿资金流向回到转出方，
          即构成 A→B→…→A 的资金闭环，且闭环账户数 >= 3。
    目标：整笔交易（sender 与 receiver 双方均记录）
    """
    cfg = RULE_CONFIG["rule4"]
    rows = query(
        """SELECT from_account, to_account FROM transactions
           WHERE UNIX_TIMESTAMP(tx_time) >= %s AND UNIX_TIMESTAMP(tx_time) <= %s""",
        (now_ts - cfg["cycle_window"], now_ts),
    )
    # 构建有向图：from -> set(to)
    graph = {}
    for r in rows:
        graph.setdefault(r["from_account"], set()).add(r["to_account"])

    # BFS：从 receiver 出发找回到 sender 的路径，路径边数 >= 环长-1
    min_path_len = cfg["min_cycle_len"] - 1
    queue = deque([(receiver, 0)])
    seen = {receiver: 0}
    while queue:
        node, d = queue.popleft()
        for nxt in graph.get(node, set()):
            if nxt == sender and d + 1 >= min_path_len:
                return {
                    "rule_id": 4,
                    "target": "both",
                    "desc": (
                        f"检测到资金闭环流转：{sender}→{receiver}→…→{sender}，"
                        f"闭环账户数 >= {cfg['min_cycle_len']}，资金回流形成环形洗钱路径"
                    ),
                }
            if nxt not in seen or seen[nxt] > d + 1:
                seen[nxt] = d + 1
                queue.append((nxt, d + 1))
    return None


def _rule5_new_account(sender, receiver, amount, now_ts):
    """
    规则5：新账户异常收款
    特征：接收方为注册 24 小时内的新账户、从未有过转出记录，却在时间窗口内
          短时间批量收到多笔入账。
    目标：接收方（receiver）
    """
    cfg = RULE_CONFIG["rule5"]
    recv = query("SELECT * FROM users WHERE account_id=%s", (receiver,), one=True)
    if not recv:
        return None
    reg_ts = int(recv["created_at"].timestamp())
    if now_ts - reg_ts > cfg["new_account_age"]:
        return None  # 非新账户
    # 无任何历史转出记录
    out_count = query(
        "SELECT COUNT(*) c FROM transactions WHERE from_account=%s", (receiver,), one=True
    )["c"]
    if out_count > 0:
        return None
    # 窗口内批量收账
    in_count = query(
        """SELECT COUNT(*) c FROM transactions
           WHERE to_account=%s AND UNIX_TIMESTAMP(tx_time) >= %s AND UNIX_TIMESTAMP(tx_time) <= %s""",
        (receiver, now_ts - cfg["time_window"], now_ts), one=True,
    )["c"]
    if in_count >= cfg["in_count"]:
        return {
            "rule_id": 5,
            "target": receiver,
            "desc": (
                f"新账户{receiver}（注册未满24小时且无转出记录）在"
                f"{cfg['time_window'] // 60}分钟内批量收到{in_count}笔入账，符合异常收款特征"
            ),
        }
    return None


def _rule6_collect(sender, receiver, amount, now_ts):
    """
    规则6：分批资金归集
    特征：时间窗口内，多个（>=3）不同账户持续向同一账户汇聚资金，且归集总额达到阈值。
    目标：接收方（receiver）
    """
    cfg = RULE_CONFIG["rule6"]
    ins = query(
        """SELECT from_account, amount FROM transactions
           WHERE to_account=%s AND UNIX_TIMESTAMP(tx_time) >= %s AND UNIX_TIMESTAMP(tx_time) <= %s""",
        (receiver, now_ts - cfg["time_window"], now_ts),
    )
    distinct_from = len(set(i["from_account"] for i in ins))
    total = float(sum(i["amount"] for i in ins))
    if distinct_from >= cfg["distinct_from"] and total >= cfg["min_total"]:
        return {
            "rule_id": 6,
            "target": receiver,
            "desc": (
                f"账户{receiver}在{cfg['time_window'] // 60}分钟内被{distinct_from}个"
                f"不同账户分批归集资金共{total:.2f}元，符合资金归集特征"
            ),
        }
    return None


# 规则执行器注册表：全部六条规则按序执行
RULE_EXECUTORS = [
    _rule1_large_split,
    _rule2_high_freq,
    _rule3_pure_relay,
    _rule4_cycle,
    _rule5_new_account,
    _rule6_collect,
]


def run_risk_engine(sender, receiver, amount, now_ts):
    """
    六维风控扫描入口：对单笔已入库交易执行全部六条规则。
    返回命中列表：[{rule_id, target, desc}, ...]
    """
    hits = []
    for executor in RULE_EXECUTORS:
        hit = executor(sender, receiver, amount, now_ts)
        if hit:
            hits.append(hit)
    return hits


# =====================================================================
# 二、转账核心逻辑（余额变动 + 实时上链 + 实时风控）
# =====================================================================

def do_transfer(from_user, to_user, amount, remark="", tx_time=None):
    """
    统一转账入口（API 与测试数据脚本共用）。
    流程：扣款 → 计算哈希 → 上链 → 落库 → 入账 → 风控扫描 → 风险上链 → 账户分级
    返回结果字典。
    """
    now_dt = tx_time or datetime.now()
    now_ts = int(now_dt.timestamp())
    from_account = from_user["account_id"]
    to_account = to_user["account_id"]

    # 1. 扣减转出方余额（条件更新保证余额充足）
    n = execute(
        "UPDATE users SET balance = balance - %s WHERE account_id=%s AND balance >= %s",
        (amount, from_account, amount),
    )
    if n == 0:
        raise ValueError("账户余额不足")

    # 2. 计算交易哈希并实时上链（Ganache 可用则真实上链，否则降级模拟哈希）
    amount_fen = int(amount * 100)                       # 金额换算为分
    nonce = int(time.time() * 1000) % 1000000000         # 毫秒级随机数保证哈希唯一
    tx_hash, chain_status = chain.record_transaction(from_account, to_account, amount_fen, nonce, now_ts)

    # 3. 交易落库（初始风险等级为 0，风控结果随后回填）
    execute(
        """INSERT INTO transactions (tx_hash, from_account, to_account, amount, tx_time, chain_status, remark)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (tx_hash, from_account, to_account, amount, now_dt, chain_status, remark),
    )

    # 4. 增加接收方余额
    execute("UPDATE users SET balance = balance + %s WHERE account_id=%s", (amount, to_account))

    # 5. 实时六维风控扫描
    hits = run_risk_engine(from_account, to_account, amount, now_ts)
    risk_level = min(len(hits), 3)                       # 0/1/2/3 四级
    rule_ids = ",".join(str(h["rule_id"]) for h in hits) if hits else ""

    if hits:
        # 5.1 回填交易风险信息（本地 + 链上）
        execute(
            "UPDATE transactions SET risk_level=%s, rules_hit=%s WHERE tx_hash=%s",
            (risk_level, rule_ids, tx_hash),
        )
        if chain_status == "confirmed":
            chain.update_tx_risk(tx_hash, risk_level, rule_ids)
        # 5.2 逐条生成风险记录并上链（规则4 同时记录收发双方）
        for h in hits:
            targets = [from_account, to_account] if h["target"] == "both" else [h["target"]]
            for acc in targets:
                execute(
                    """INSERT INTO risk_records (tx_hash, account_id, rule_ids, rule_desc, risk_level)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (tx_hash, acc, str(h["rule_id"]), f"规则{h['rule_id']}：{h['desc']}", risk_level),
                )
                chain.record_risk(tx_hash, acc, str(h["rule_id"]), risk_level, now_ts)
        # 5.3 更新收发双方账户风险等级（取历史最高）
        for acc in {from_account, to_account}:
            execute(
                "UPDATE users SET risk_level = GREATEST(risk_level, %s) WHERE account_id=%s",
                (risk_level, acc),
            )

    # 6. 返回转账结果
    return {
        "ok": True,
        "tx_hash": tx_hash,
        "chain_status": chain_status,
        "amount": float(amount),
        "from_account": from_account,
        "to_account": to_account,
        "tx_time": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "risk_level": risk_level,
        "risk_text": risk_text(risk_level),
        "rule_ids": rule_ids,
        "hits": [{"rule_id": h["rule_id"], "name": RULE_NAMES[h["rule_id"]], "desc": h["desc"]} for h in hits],
        "from_balance": float(
            query("SELECT balance FROM users WHERE account_id=%s", (from_account,), one=True)["balance"]
        ),
        "to_balance": float(
            query("SELECT balance FROM users WHERE account_id=%s", (to_account,), one=True)["balance"]
        ),
    }


def tx_row_to_dict(t):
    """交易记录 dict 序列化（前端展示用）"""
    return {
        "tx_hash": t["tx_hash"],
        "from_account": t["from_account"],
        "to_account": t["to_account"],
        "amount": float(t["amount"]),
        "tx_time": t["tx_time"].strftime("%Y-%m-%d %H:%M:%S"),
        "chain_status": t["chain_status"],
        "risk_level": t["risk_level"],
        "risk_text": risk_text(t["risk_level"]),
        "rules_hit": t["rules_hit"],
        "remark": t["remark"],
    }


# =====================================================================
# 三、转账 API
# =====================================================================

@bp.route("/tx/transfer", methods=["POST"])
def transfer_api():
    """普通用户发起链上模拟转账（自动实时风控）"""
    user = require_user()
    if not user:
        return jsonify({"ok": False, "msg": "未登录或登录已过期"}), 401
    if user["status"] != "approved":
        return jsonify({"ok": False, "msg": "实名认证未通过，无法转账"}), 403

    data = request.get_json(silent=True) or {}
    to_account = (data.get("to_account") or "").strip()
    remark = (data.get("remark") or "").strip()[:100]
    try:
        amount = Decimal(str(data.get("amount", "0")))
    except Exception:
        return jsonify({"ok": False, "msg": "金额格式错误"}), 400
    if amount <= 0:
        return jsonify({"ok": False, "msg": "转账金额必须大于 0"}), 400
    if not to_account:
        return jsonify({"ok": False, "msg": "请选择收款账户"}), 400
    if to_account == user["account_id"]:
        return jsonify({"ok": False, "msg": "不能向自己转账"}), 400

    to_user = query(
        "SELECT * FROM users WHERE account_id=%s AND role='user' AND status='approved'",
        (to_account,), one=True,
    )
    if not to_user:
        return jsonify({"ok": False, "msg": "收款账户不存在或未通过实名审核"}), 400

    try:
        result = do_transfer(user, to_user, amount, remark)
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)}), 400
    return jsonify(result)


@bp.route("/tx/my", methods=["GET"])
def my_transactions():
    """查看本人交易记录（转出与转入）"""
    user = require_user()
    if not user:
        return jsonify({"ok": False, "msg": "未登录或登录已过期"}), 401
    rows = query(
        """SELECT * FROM transactions
           WHERE from_account=%s OR to_account=%s
           ORDER BY tx_time DESC LIMIT 200""",
        (user["account_id"], user["account_id"]),
    )
    return jsonify({"ok": True, "transactions": [tx_row_to_dict(r) for r in rows]})


@bp.route("/tx/all", methods=["GET"])
def all_transactions():
    """全网交易查询（监管端）：支持账户/风险等级/哈希关键词/时间过滤"""
    admin = require_admin()
    if not admin:
        return jsonify({"ok": False, "msg": "无权限"}), 401

    account = request.args.get("account", "").strip()
    risk_level = request.args.get("risk_level", "").strip()
    keyword = request.args.get("keyword", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()

    where, args = [], []
    if account:
        where.append("(from_account=%s OR to_account=%s)")
        args += [account, account]
    if risk_level in ("0", "1", "2", "3"):
        where.append("risk_level=%s")
        args.append(int(risk_level))
    if keyword:
        where.append("tx_hash LIKE %s")
        args.append("%" + keyword + "%")
    if date_from:
        where.append("tx_time >= %s")
        args.append(date_from)
    if date_to:
        where.append("tx_time <= %s")
        args.append(date_to + " 23:59:59")

    sql = "SELECT * FROM transactions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY tx_time DESC LIMIT 500"
    rows = query(sql, args)
    return jsonify({"ok": True, "transactions": [tx_row_to_dict(r) for r in rows]})


@bp.route("/tx/verify", methods=["GET"])
def verify_transaction():
    """交易哈希核验：本地记录与联盟链记录比对，证明交易未篡改"""
    tx_hash = (request.args.get("tx_hash") or "").strip()
    if not tx_hash:
        return jsonify({"ok": False, "msg": "缺少交易哈希参数"}), 400
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash

    db_row = query("SELECT * FROM transactions WHERE tx_hash=%s", (tx_hash,), one=True)
    on_chain = chain.get_transaction(tx_hash)

    if not db_row:
        return jsonify({"ok": False, "msg": "本地数据库未找到该交易"}), 404

    if on_chain:
        chain_ok = on_chain["exists"] and on_chain["from_account"] == db_row["from_account"] \
            and on_chain["to_account"] == db_row["to_account"] \
            and on_chain["amount_fen"] == int(float(db_row["amount"]) * 100)
        msg = "链上核验通过：交易内容与联盟链记录一致，未被篡改" if chain_ok else "链上记录与本地记录不一致！"
    else:
        chain_ok = False
        msg = "当前环境未连接联盟链（Ganache），无法进行链上比对；本地记录如上。"

    return jsonify({
        "ok": True,
        "msg": msg,
        "matched": chain_ok,
        "tx": tx_row_to_dict(db_row),
        "on_chain": on_chain,
        "chain_connected": on_chain is not None,
    })


# =====================================================================
# 四、风险账户管理（监管端）
# =====================================================================

@bp.route("/admin/risk_accounts", methods=["GET"])
def risk_accounts():
    """风险账户清单：风险等级 + 累计命中规则 + 交易量"""
    admin = require_admin()
    if not admin:
        return jsonify({"ok": False, "msg": "无权限"}), 401

    min_level = request.args.get("min_level", "1")
    rows = query(
        """SELECT u.account_id, u.username, u.real_name, u.risk_level, u.balance,
                  u.status, u.created_at,
                  (SELECT COUNT(*) FROM transactions t
                    WHERE t.from_account=u.account_id OR t.to_account=u.account_id) AS tx_n,
                  (SELECT GROUP_CONCAT(DISTINCT rule_ids ORDER BY rule_ids SEPARATOR ',')
                     FROM risk_records r WHERE r.account_id=u.account_id) AS rules_hit
           FROM users u
           WHERE u.role='user' AND u.risk_level >= %s
           ORDER BY u.risk_level DESC, tx_n DESC""",
        (int(min_level),),
    )
    result = []
    for r in rows:
        r["risk_text"] = risk_text(r["risk_level"])
        r["balance"] = float(r["balance"] or 0)
        r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if r["created_at"] else ""
        result.append(r)
    return jsonify({"ok": True, "accounts": result})


@bp.route("/admin/account_detail", methods=["GET"])
def account_detail():
    """账户详情：基本信息 + 交易流水 + 风险记录（监管端钻取）"""
    admin = require_admin()
    if not admin:
        return jsonify({"ok": False, "msg": "无权限"}), 401
    account_id = (request.args.get("account_id") or "").strip()
    user = query("SELECT * FROM users WHERE account_id=%s", (account_id,), one=True)
    if not user:
        return jsonify({"ok": False, "msg": "账户不存在"}), 404

    txs = query(
        "SELECT * FROM transactions WHERE from_account=%s OR to_account=%s ORDER BY tx_time DESC LIMIT 200",
        (account_id, account_id),
    )
    risks = query(
        "SELECT * FROM risk_records WHERE account_id=%s ORDER BY created_at DESC LIMIT 100",
        (account_id,),
    )
    return jsonify({
        "ok": True,
        "user": {
            "account_id": user["account_id"],
            "username": user["username"],
            "real_name": user["real_name"],
            "risk_level": user["risk_level"],
            "risk_text": risk_text(user["risk_level"]),
            "balance": float(user["balance"] or 0),
            "status": user["status"],
            "created_at": user["created_at"].strftime("%Y-%m-%d %H:%M:%S") if user["created_at"] else "",
        },
        "transactions": [tx_row_to_dict(t) for t in txs],
        "risks": [
            {
                "tx_hash": r["tx_hash"],
                "rule_ids": r["rule_ids"],
                "rule_desc": r["rule_desc"],
                "risk_level": r["risk_level"],
                "risk_text": risk_text(r["risk_level"]),
                "created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S"),
            }
            for r in risks
        ],
    })

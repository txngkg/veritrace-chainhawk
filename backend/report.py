# -*- coding: utf-8 -*-
"""
报案取证固证模块（系统核心）
==============================
功能接口：
  1. POST /api/case/report             用户在线报案（自动受理 + 资金溯源 + 生成取证报告）
  2. GET  /api/case/my                 我的报案与溯源结果（用户端）
  3. GET  /api/case/all                全部报案案件（监管端）
  4. GET  /api/case/detail?case_id=    案件完整结构化证据包（拓扑图 + 明细表 + 风险清单）
  5. GET  /api/case/download?case_id=  下载取证报告（Markdown）
  6. GET  /api/case/verify?case_id=    存证报告哈希核验（链上 + 内容重算）

核心能力：以涉案首笔交易为起点，程序自动穿透多层中转资金链路，输出
警方可直接用于办案研判的五大部分结构化证据：
  一、案件基础证据信息
  二、完整多级资金流转可视化拓扑
  三、标准化涉案交易明细证据表
  四、涉案可疑风险账户汇总清单
  五、存证报告哈希固化与核验
"""
import hashlib
import io
import json
import random
from collections import deque
from datetime import datetime

from flask import Blueprint, jsonify, request, send_file

from blockchain import chain
from config import (
    FOLLOW_OUT_LIMIT,
    MAX_TRACE_DEPTH,
    MAX_TRACE_NODES,
    RISK_LEVEL_TEXT,
    execute,
    query,
)
from transaction import RULE_NAMES, risk_text
from user import require_admin, require_user

bp = Blueprint("report", __name__, url_prefix="/api")


class NoTransactionError(Exception):
    """报案时间段内未找到涉案交易"""


# =====================================================================
# 一、资金溯源：以涉案首笔交易为起点自动遍历多层中转链路
# =====================================================================

def _tx_to_edge(t, depth):
    """交易记录 -> 拓扑边"""
    return {
        "tx_hash": t["tx_hash"],
        "from_account": t["from_account"],
        "to_account": t["to_account"],
        "amount": float(t["amount"]),
        "tx_time": t["tx_time"].strftime("%Y-%m-%d %H:%M:%S"),
        "risk_level": t["risk_level"],
        "risk_text": risk_text(t["risk_level"]),
        "rules_hit": t["rules_hit"] or "",
        "trace_depth": depth,
    }


def _trace_fund_flow(case):
    """
    资金溯源主流程（BFS 广度优先遍历）：
      1) 定位受害账户在受骗时间段内的全部转出作为涉案起点；
      2) 对每个一级收款账户，自动追踪其后继转出，逐层穿透中转链路；
      3) 记录全部涉案交易与账户，并计算链路角色。
    返回 (nodes, edges, init_tx)。
    """
    victim = case["victim_account"]
    start_ts = int(case["time_start"].timestamp())
    end_ts = int(case["time_end"].timestamp())

    # 1) 涉案起点：受害账户在时间段内的转出交易（正常报案方向）
    victim_outs = query(
        """SELECT * FROM transactions
           WHERE from_account=%s AND UNIX_TIMESTAMP(tx_time) BETWEEN %s AND %s
           ORDER BY tx_time ASC""",
        (victim, start_ts, end_ts),
    )
    fallback_ins = None
    if not victim_outs:
        # 2) 降级兜底：若受害账户为被动收款，则以时间段内第一笔转入作为起点
        fallback_ins = query(
            """SELECT * FROM transactions
               WHERE to_account=%s AND UNIX_TIMESTAMP(tx_time) BETWEEN %s AND %s
               ORDER BY tx_time ASC LIMIT 1""",
            (victim, start_ts, end_ts), one=True,
        )
        if not fallback_ins:
            raise NoTransactionError("在报案时间段内未找到受害账户相关交易")

    init_tx = victim_outs[0] if victim_outs else fallback_ins
    follow_end = end_ts + 24 * 3600   # 中转跟踪窗口：涉案结束时间后 24 小时

    nodes = {}    # account_id -> 节点信息
    edges = []    # 边列表（即涉案交易）
    queue = deque()
    visited = {victim}

    # 受害账户节点
    nodes[victim] = {
        "account_id": victim,
        "depth": 0,
        "role": "受害账户",
        "risk_level": 0,
        "rules_hit": "",
    }

    # 起点：受害账户转出的每笔交易 → 一级收款账户
    if victim_outs:
        for t in victim_outs:
            edges.append(_tx_to_edge(t, 1))
            target = t["to_account"]
            if target not in visited:
                visited.add(target)
                nodes[target] = {
                    "account_id": target, "depth": 1,
                    "role": "", "risk_level": 0, "rules_hit": "",
                }
                queue.append((target, 1, t))
    else:
        # 兜底：仅记录该笔转入交易及其对手方
        edges.append(_tx_to_edge(fallback_ins, 1))
        if fallback_ins["from_account"] not in visited:
            visited.add(fallback_ins["from_account"])
            nodes[fallback_ins["from_account"]] = {
                "account_id": fallback_ins["from_account"], "depth": 1,
                "role": "", "risk_level": 0, "rules_hit": "",
            }

    # 3) 逐层穿透中转账户的后续转出
    while queue and len(nodes) < MAX_TRACE_NODES:
        account, depth, in_tx = queue.popleft()
        if depth >= MAX_TRACE_DEPTH:
            continue
        follows = query(
            """SELECT * FROM transactions
               WHERE from_account=%s AND UNIX_TIMESTAMP(tx_time) >= %s
                     AND UNIX_TIMESTAMP(tx_time) <= %s
               ORDER BY tx_time ASC LIMIT %s""",
            (account, int(in_tx["tx_time"].timestamp()), follow_end, FOLLOW_OUT_LIMIT),
        )
        for f in follows:
            if len(nodes) >= MAX_TRACE_NODES:
                break
            edges.append(_tx_to_edge(f, depth + 1))
            target = f["to_account"]
            if target not in visited:
                visited.add(target)
                nodes[target] = {
                    "account_id": target, "depth": depth + 1,
                    "role": "", "risk_level": 0, "rules_hit": "",
                }
                queue.append((target, depth + 1, f))

    # 4) 汇总账户风险信息（用户表风险等级 + 案件内风险记录命中规则）
    _enrich_node_risk(nodes, edges)

    # 5) 计算链路角色：一级收款 / 中转 / 归集终点
    out_set = {e["from_account"] for e in edges}
    for acc, node in nodes.items():
        if node["depth"] == 0:
            continue
        roles = ["一级收款"] if node["depth"] == 1 else []
        roles.append("中转" if acc in out_set else "归集终点")
        node["role"] = "/".join(roles)

    return nodes, edges, init_tx


def _enrich_node_risk(nodes, edges):
    """为每个涉案账户补充：账户风险等级 + 命中规则 + 真实姓名/用户名"""
    tx_hashes = [e["tx_hash"] for e in edges]
    risk_rows = []
    if tx_hashes:
        placeholders = ",".join(["%s"] * len(tx_hashes))
        risk_rows = query(
            f"SELECT * FROM risk_records WHERE tx_hash IN ({placeholders})", tx_hashes
        )
    by_acc = {}
    for r in risk_rows:
        by_acc.setdefault(r["account_id"], set()).add(r["rule_ids"])
    for acc, node in nodes.items():
        node["rules_hit"] = ",".join(sorted(by_acc.get(acc, set())))
        u = query(
            "SELECT username, real_name, risk_level FROM users WHERE account_id=%s",
            (acc,), one=True,
        )
        if u:
            node["risk_level"] = u["risk_level"]
            node["username"] = u["username"]
            node["real_name"] = u["real_name"] or ""
        else:
            node["username"] = acc
            node["real_name"] = ""
        node["risk_text"] = RISK_LEVEL_TEXT.get(node["risk_level"], "正常")


# =====================================================================
# 二、结构化取证报告生成（五大部分）
# =====================================================================

# 报告哈希占位符（生成报告时先占位，哈希计算完成后替换为真实值）
# 保证报告正文包含"报告哈希"字段的同时，哈希可通过"还原占位"方式被一致重算
SENT_HASH = "___REPORT_HASH_SENTINEL___"
SENT_CHAIN = "___CHAIN_HASH_SENTINEL___"


def _build_report_markdown(case, reporter, nodes, edges):
    """依据溯源结果生成完整取证报告（Markdown），包含五大证据部分"""
    md = []
    md.append("# 诈骗资金溯源取证存证报告")
    md.append("")
    md.append(f"> 系统定位：诈骗资金风险监测与报案溯源固证辅助系统（联盟链模拟仿真原型）")
    md.append("")
    md.append("---")
    md.append("")

    # ---------- 第一部分：案件基础证据信息 ----------
    md.append("## 一、案件基础证据信息")
    md.append("")
    md.append("| 证据项 | 内容 |")
    md.append("| --- | --- |")
    md.append(f"| 案件编号 | {case['case_no']} |")
    md.append(f"| 报案人账户 | {case['victim_account']}（{reporter['username']}） |")
    md.append(f"| 报案时间 | {case['created_at'].strftime('%Y-%m-%d %H:%M:%S')} |")
    md.append(f"| 涉案起始交易哈希 | {case['first_tx_hash'] or '—'} |")
    md.append(f"| 涉案时间段 | {case['time_start'].strftime('%Y-%m-%d %H:%M:%S')} 至 {case['time_end'].strftime('%Y-%m-%d %H:%M:%S')} |")
    md.append(f"| 初始涉案金额 | {float(case['reported_amount']):.2f} 元 |")
    md.append(f"| 涉案交易总笔数 | {len(edges)} 笔 |")
    md.append(f"| 涉案账户总数 | {len(nodes)} 个 |")
    md.append(f"| 证据报告哈希 | {SENT_HASH} |")
    md.append("")

    # ---------- 第二部分：资金流转链路描述 ----------
    md.append("## 二、完整多级资金流转链路")
    md.append("")
    md.append("资金流向（自动溯源还原，非人工录入）：")
    md.append("")
    md.append("```text")
    for e in edges:
        md.append(f"{e['trace_depth']} 层 | {e['from_account']} --({e['amount']:.2f}元)--> {e['to_account']}  |  {e['tx_time']}")
    md.append("```")
    md.append("")
    md.append("账户角色分布：")
    md.append("")
    md.append("| 账户 | 角色 | 溯源深度 | 风险等级 | 命中规则 |")
    md.append("| --- | --- | --- | --- | --- |")
    for acc, node in nodes.items():
        md.append(f"| {acc} | {node['role']} | {node['depth']} | {node['risk_text']} | {node['rules_hit'] or '—'} |")
    md.append("")

    # ---------- 第三部分：标准化涉案交易明细证据表 ----------
    md.append("## 三、标准化涉案交易明细证据表（警方重点核验）")
    md.append("")
    md.append("> 说明：每一笔流水均含链上唯一交易哈希，可在联盟链独立核验真伪，确保证据未篡改。")
    md.append("")
    md.append("| 序号 | 转出账户 | 接收账户 | 转账金额(元) | 交易时间 | 链上交易哈希 | 风险等级 | 命中风险规则 |")
    md.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    sorted_edges = sorted(edges, key=lambda e: (e["trace_depth"], e["tx_time"]))
    for i, e in enumerate(sorted_edges, 1):
        md.append(
            f"| {i} | {e['from_account']} | {e['to_account']} | {e['amount']:.2f} | "
            f"{e['tx_time']} | {e['tx_hash']} | {e['risk_text']} | {e['rules_hit'] or '—'} |"
        )
    md.append("")

    # ---------- 第四部分：涉案可疑风险账户汇总清单 ----------
    md.append("## 四、涉案可疑风险账户汇总清单（中风险及以上）")
    md.append("")
    md.append("> 以下账户为本案件命中中/高风险等级的可疑账户，建议警方重点排查洗钱中转账户与最终归集账户。")
    md.append("")
    md.append("| 序号 | 账户 | 风险等级 | 触发规则 | 链路位置 |")
    md.append("| --- | --- | --- | --- | --- |")
    suspect = sorted(
        [n for n in nodes.values() if n["risk_level"] >= 2],
        key=lambda n: (-n["risk_level"], n["depth"]),
    )
    if not suspect:
        md.append("| — | 本案件未发现中/高风险可疑账户 | — | — | — |")
    else:
        for i, n in enumerate(suspect, 1):
            rule_desc = "、".join(f"规则{r}" for r in (n["rules_hit"] or "").split(",") if r) or "—"
            md.append(f"| {i} | {n['account_id']} | {n['risk_text']} | {rule_desc} | {n['role']}（深度{n['depth']}） |")
    md.append("")

    # ---------- 第五部分：存证报告哈希固化与核验 ----------
    md.append("## 五、存证报告哈希固化与核验")
    md.append("")
    md.append(f"- 报告整体 SHA256 哈希：`{SENT_HASH}`")
    md.append(f"- 报告已上链存证（案件编号：`{case['case_no']}`），链上存证记录交易哈希：`{SENT_CHAIN}`")
    md.append("- 监管端核验方式：进入「存证报告核验」页面，输入案件编号，系统将比对链上哈希与本地报告内容重算哈希，三者一致即为真实未篡改证据。")
    md.append("")
    md.append("---")
    md.append("")
    md.append(f"*本报告由系统自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}，仅供参考，系统为模拟仿真环境，不构成真实执法依据。*")
    md.append("")
    return "\n".join(md)


def _build_report_json(case, reporter, nodes, edges):
    """构建结构化证据包 JSON（前端拓扑图与表格直接消费）"""
    sorted_edges = sorted(edges, key=lambda e: (e["trace_depth"], e["tx_time"]))
    suspect = sorted(
        [n for n in nodes.values() if n["risk_level"] >= 2],
        key=lambda n: (-n["risk_level"], n["depth"]),
    )
    return {
        "case": {
            "case_no": case["case_no"],
            "victim_account": case["victim_account"],
            "reporter_name": reporter["username"],
            "reported_amount": float(case["reported_amount"]),
            "time_start": case["time_start"].strftime("%Y-%m-%d %H:%M:%S"),
            "time_end": case["time_end"].strftime("%Y-%m-%d %H:%M:%S"),
            "first_tx_hash": case["first_tx_hash"],
            "report_hash": case["report_hash"],
            "chain_report_hash": case["chain_report_hash"],
            "created_at": case["created_at"].strftime("%Y-%m-%d %H:%M:%S"),
            "status": case["status"],
            "fail_reason": case["fail_reason"],
        },
        "topology": {
            "nodes": [
                {
                    "id": n["account_id"],
                    "name": n["account_id"],
                    "username": n.get("username", ""),
                    "real_name": n.get("real_name", ""),
                    "risk_level": n["risk_level"],
                    "risk_text": n["risk_text"],
                    "rules_hit": n["rules_hit"],
                    "role": n["role"],
                    "depth": n["depth"],
                }
                for n in nodes.values()
            ],
            "links": [
                {
                    "source": e["from_account"],
                    "target": e["to_account"],
                    "value": e["amount"],
                    "tx_hash": e["tx_hash"],
                    "tx_time": e["tx_time"],
                    "risk_level": e["risk_level"],
                    "risk_text": e["risk_text"],
                    "rules_hit": e["rules_hit"],
                    "trace_depth": e["trace_depth"],
                }
                for e in sorted_edges
            ],
        },
        "transactions": sorted_edges,
        "risk_accounts": suspect,
    }


# =====================================================================
# 三、报案 API
# =====================================================================

@bp.route("/case/report", methods=["POST"])
def submit_case():
    """（保留旧接口）用户在线报案：受理 → 自动溯源 → 生成取证报告 → 报告哈希上链"""
    user = require_user()
    if not user:
        return jsonify({"ok": False, "msg": "未登录或登录已过期"}), 401
    if user["status"] != "approved":
        return jsonify({"ok": False, "msg": "实名认证未通过，无法报案"}), 403

    data = request.get_json(silent=True) or {}
    try:
        amount = float(data.get("amount", 0))
        time_start = data.get("time_start")
        time_end = data.get("time_end")
        desc = (data.get("desc") or "").strip()[:500]
    except Exception:
        return jsonify({"ok": False, "msg": "参数格式错误"}), 400

    if amount <= 0:
        return jsonify({"ok": False, "msg": "被骗金额必须大于 0"}), 400
    if not time_start or not time_end:
        return jsonify({"ok": False, "msg": "请填写受骗时间段"}), 400
    try:
        ts_start = datetime.strptime(time_start, "%Y-%m-%dT%H:%M")
        ts_end = datetime.strptime(time_end, "%Y-%m-%dT%H:%M")
    except ValueError:
        try:
            ts_start = datetime.strptime(time_start, "%Y-%m-%d %H:%M")
            ts_end = datetime.strptime(time_end, "%Y-%m-%d %H:%M")
        except ValueError:
            return jsonify({"ok": False, "msg": "时间格式错误，应为 YYYY-MM-DD HH:MM"}), 400
    if ts_start >= ts_end:
        return jsonify({"ok": False, "msg": "受骗开始时间必须早于结束时间"}), 400
    if (ts_end - ts_start).days > 30:
        return jsonify({"ok": False, "msg": "受骗时间段跨度不能超过 30 天"}), 400

    # 生成唯一案件编号
    case_no = "CASE" + datetime.now().strftime("%Y%m%d%H%M%S") + str(random.randint(1000, 9999))

    # 插入案件（处理中）
    case_id = _insert_case(case_no, user["id"], user["account_id"], amount, ts_start, ts_end, desc)

    result = _trace_and_report(case_id, user)
    code = 200 if result["ok"] else (400 if "未找到" in result.get("msg", "") else 500)
    return jsonify(result), code


def _insert_case(case_no, reporter_id, victim_account, amount, ts_start, ts_end, desc, status="processing"):
    """插入案件记录并返回自增ID"""
    execute(
        """INSERT INTO cases (case_no, reporter_id, victim_account, reported_amount,
                             time_start, time_end, case_desc, status)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (case_no, reporter_id, victim_account, amount, ts_start, ts_end, desc, status),
    )
    return query("SELECT id FROM cases WHERE case_no=%s", (case_no,), one=True)["id"]


def _trace_and_report(case_id, reporter):
    """
    对案件执行完整溯源取证（监管端触发 / 旧接口自动触发共用）：
      1) 资金溯源穿透
      2) 固化溯源结果
      3) 生成取证报告 + 报告哈希上链存证
    """
    try:
        case = query("SELECT * FROM cases WHERE id=%s", (case_id,), one=True)
        if not case:
            return {"ok": False, "msg": "案件不存在"}

        # 1) 资金溯源
        nodes, edges, init_tx = _trace_fund_flow(case)
        # 2) 固化溯源结果到案件关联表
        for e in edges:
            execute(
                "INSERT INTO case_transactions (case_id, tx_hash, trace_depth) VALUES (%s,%s,%s)",
                (case_id, e["tx_hash"], e["trace_depth"]),
            )
        for acc, node in nodes.items():
            execute(
                """INSERT INTO case_accounts (case_id, account_id, role, risk_level, rules_hit, trace_depth)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (case_id, acc, node["role"], node["risk_level"], node["rules_hit"], node["depth"]),
            )
        # 3) 回填首笔交易哈希
        execute("UPDATE cases SET first_tx_hash=%s WHERE id=%s", (init_tx["tx_hash"], case_id))

        # 4) 组装结构化证据包 JSON
        case_row = query("SELECT * FROM cases WHERE id=%s", (case_id,), one=True)
        report_json = _build_report_json(case_row, reporter, nodes, edges)

        # 5) 生成取证报告 Markdown（哈希字段为占位符），并基于占位版计算报告整体哈希
        md_template = _build_report_markdown(case_row, reporter, nodes, edges)
        report_hash = hashlib.sha256(md_template.encode("utf-8")).hexdigest()

        # 6) 报告哈希上链存证（返回链上存证交易哈希）
        chain_report_hash = chain.record_report(case_row["case_no"], report_hash)

        # 7) 用真实哈希替换占位符，形成最终报告正文（正文自包含哈希，且不影响哈希可重算）
        final_md = md_template.replace(SENT_HASH, report_hash).replace(
            SENT_CHAIN, chain_report_hash or "模拟环境未真实上链"
        )
        # 回填证据包中的报告哈希与链上存证哈希（前端拓扑/报告预览展示用）
        report_json["case"]["report_hash"] = report_hash
        report_json["case"]["chain_report_hash"] = chain_report_hash

        # 8) 更新案件为完成状态
        execute(
            """UPDATE cases SET report_hash=%s, chain_report_hash=%s, report_json=%s, report_md=%s, status='done'
               WHERE id=%s""",
            (report_hash, chain_report_hash, json.dumps(report_json, ensure_ascii=False), final_md, case_id),
        )
        return {
            "ok": True,
            "msg": "溯源取证完成，取证报告已生成并完成哈希上链存证",
            "case_id": case_id,
            "case_no": case_row["case_no"],
            "first_tx_hash": init_tx["tx_hash"],
            "report_hash": report_hash,
        }
    except NoTransactionError as e:
        execute("UPDATE cases SET status='failed', fail_reason=%s WHERE id=%s", (str(e), case_id))
        return {"ok": False, "msg": str(e), "case_id": case_id}
    except Exception as e:
        execute("UPDATE cases SET status='failed', fail_reason=%s WHERE id=%s", (str(e), case_id))
        return {"ok": False, "msg": f"溯源取证失败：{e}"}


@bp.route("/case/submit", methods=["POST"])
def submit_case_material():
    """用户在线报案：仅提交报案材料，不自动执行溯源取证（由监管端在案件溯源页触发）"""
    user = require_user()
    if not user:
        return jsonify({"ok": False, "msg": "未登录或登录已过期"}), 401
    if user["status"] != "approved":
        return jsonify({"ok": False, "msg": "实名认证未通过，无法报案"}), 403

    data = request.get_json(silent=True) or {}
    try:
        amount = float(data.get("amount", 0))
        time_start = data.get("time_start")
        time_end = data.get("time_end")
        desc = (data.get("desc") or "").strip()[:500]
    except Exception:
        return jsonify({"ok": False, "msg": "参数格式错误"}), 400

    if amount <= 0:
        return jsonify({"ok": False, "msg": "请选择涉案交易"}), 400
    if not time_start or not time_end:
        return jsonify({"ok": False, "msg": "请填写受骗时间段"}), 400
    try:
        ts_start = datetime.strptime(time_start, "%Y-%m-%dT%H:%M")
        ts_end = datetime.strptime(time_end, "%Y-%m-%dT%H:%M")
    except ValueError:
        try:
            ts_start = datetime.strptime(time_start, "%Y-%m-%d %H:%M")
            ts_end = datetime.strptime(time_end, "%Y-%m-%d %H:%M")
        except ValueError:
            return jsonify({"ok": False, "msg": "时间格式错误，应为 YYYY-MM-DD HH:MM"}), 400
    if ts_start >= ts_end:
        return jsonify({"ok": False, "msg": "受骗开始时间必须早于结束时间"}), 400
    if (ts_end - ts_start).days > 30:
        return jsonify({"ok": False, "msg": "受骗时间段跨度不能超过 30 天"}), 400

    case_no = "CASE" + datetime.now().strftime("%Y%m%d%H%M%S") + str(random.randint(1000, 9999))
    case_id = _insert_case(case_no, user["id"], user["account_id"], amount, ts_start, ts_end, desc, status="submitted")
    return jsonify({
        "ok": True,
        "msg": "报案材料已提交，等待监管方进行溯源取证",
        "case_id": case_id,
        "case_no": case_no,
        "status": "submitted",
    })


@bp.route("/case/trace", methods=["POST"])
def trace_case():
    """监管端对已提交报案执行溯源取证（生成拓扑/明细/取证报告并上链存证）"""
    admin = require_admin()
    if not admin:
        return jsonify({"ok": False, "msg": "无权限"}), 401

    case_id = (request.get_json(silent=True) or {}).get("case_id")
    if not case_id:
        return jsonify({"ok": False, "msg": "缺少案件ID"}), 400

    case = query("SELECT * FROM cases WHERE id=%s", (case_id,), one=True)
    if not case:
        return jsonify({"ok": False, "msg": "案件不存在"}), 404
    if case["status"] not in ("submitted", "processing"):
        return jsonify({"ok": False, "msg": "该案件已完成溯源取证或状态不允许再次溯源"}), 400

    reporter = query("SELECT * FROM users WHERE id=%s", (case["reporter_id"],), one=True)
    result = _trace_and_report(case_id, reporter or {"username": case["victim_account"]})
    return jsonify(result), (200 if result["ok"] else 400)


def _case_summary(c):
    """案件列表项序列化"""
    return {
        "id": c["id"],
        "case_no": c["case_no"],
        "victim_account": c["victim_account"],
        "reported_amount": float(c["reported_amount"]),
        "time_start": c["time_start"].strftime("%Y-%m-%d %H:%M:%S"),
        "time_end": c["time_end"].strftime("%Y-%m-%d %H:%M:%S"),
        "first_tx_hash": c["first_tx_hash"],
        "report_hash": c["report_hash"],
        "status": c["status"],
        "fail_reason": c["fail_reason"],
        "created_at": c["created_at"].strftime("%Y-%m-%d %H:%M:%S"),
    }


@bp.route("/case/my", methods=["GET"])
def my_cases():
    """我的报案列表（用户端）"""
    user = require_user()
    if not user:
        return jsonify({"ok": False, "msg": "未登录或登录已过期"}), 401
    rows = query(
        "SELECT * FROM cases WHERE reporter_id=%s ORDER BY created_at DESC", (user["id"],)
    )
    return jsonify({"ok": True, "cases": [_case_summary(c) for c in rows]})


@bp.route("/case/all", methods=["GET"])
def all_cases():
    """全部报案案件（监管端）"""
    admin = require_admin()
    if not admin:
        return jsonify({"ok": False, "msg": "无权限"}), 401
    rows = query("SELECT * FROM cases ORDER BY created_at DESC")
    return jsonify({"ok": True, "cases": [_case_summary(c) for c in rows]})


@bp.route("/case/detail", methods=["GET"])
def case_detail():
    """案件完整结构化证据包（报案人或监管管理员可查看）"""
    case_id = request.args.get("case_id", type=int)
    if not case_id:
        return jsonify({"ok": False, "msg": "缺少案件ID"}), 400

    user = require_user()
    admin = require_admin()
    if not user and not admin:
        return jsonify({"ok": False, "msg": "未登录"}), 401

    case = query("SELECT * FROM cases WHERE id=%s", (case_id,), one=True)
    if not case:
        return jsonify({"ok": False, "msg": "案件不存在"}), 404
    # 权限校验：仅报案人本人或监管管理员可查看
    if not admin and case["reporter_id"] != user["id"]:
        return jsonify({"ok": False, "msg": "无权查看该案件"}), 403

    if case["status"] == "failed":
        return jsonify({"ok": True, "case": _case_summary(case), "detail": None})

    detail = json.loads(case["report_json"] or "{}")
    return jsonify({"ok": True, "case": _case_summary(case), "detail": detail})


@bp.route("/case/download", methods=["GET"])
def download_report():
    """下载取证报告（Markdown 文件）"""
    case_id = request.args.get("case_id", type=int)
    if not case_id:
        return jsonify({"ok": False, "msg": "缺少案件ID"}), 400

    user = require_user()
    admin = require_admin()
    if not user and not admin:
        return jsonify({"ok": False, "msg": "未登录"}), 401

    case = query("SELECT * FROM cases WHERE id=%s", (case_id,), one=True)
    if not case:
        return jsonify({"ok": False, "msg": "案件不存在"}), 404
    if not admin and case["reporter_id"] != user["id"]:
        return jsonify({"ok": False, "msg": "无权下载该案件报告"}), 403
    if case["status"] != "done" or not case["report_md"]:
        return jsonify({"ok": False, "msg": "该案件尚未生成取证报告"}), 400

    buf = io.BytesIO(case["report_md"].encode("utf-8"))
    buf.seek(0)
    return send_file(
        buf,
        mimetype="text/markdown",
        as_attachment=True,
        download_name=f"{case['case_no']}_取证报告.md",
    )


@bp.route("/case/verify", methods=["GET"])
def verify_report():
    """
    存证报告哈希核验（监管端/用户端通用）：
      1. 从数据库取出报告哈希与报告全文；
      2. 按报告全文重算 SHA256（内容完整性核验）；
      3. 与联盟链上存证的报告哈希比对（链上存证核验）。
    """
    case_id = request.args.get("case_id", type=int)
    if not case_id:
        return jsonify({"ok": False, "msg": "缺少案件ID"}), 400

    user = require_user()
    admin = require_admin()
    if not user and not admin:
        return jsonify({"ok": False, "msg": "未登录"}), 401

    case = query("SELECT * FROM cases WHERE id=%s", (case_id,), one=True)
    if not case:
        return jsonify({"ok": False, "msg": "案件不存在"}), 404
    if not admin and case["reporter_id"] != user["id"]:
        return jsonify({"ok": False, "msg": "无权核验该案件"}), 403
    if case["status"] != "done":
        return jsonify({"ok": False, "msg": "案件尚未完成，无法核验"}), 400

    # 1) 内容重算：将最终报告正文中"哈希字段"还原为生成时的占位符，再计算 SHA256
    #    注意：链上存证哈希(0x+报告哈希)含报告哈希子串，须先替换链上存证值，再替换报告哈希
    chain_disp = case["chain_report_hash"] or "模拟环境未真实上链"
    restored = (case["report_md"] or "")
    restored = restored.replace(chain_disp, SENT_CHAIN)
    restored = restored.replace(case["report_hash"] or "", SENT_HASH)
    recomputed = hashlib.sha256(restored.encode("utf-8")).hexdigest()
    content_ok = recomputed == case["report_hash"]

    # 2) 链上核验
    on_chain_hash = chain.get_report_hash(case["case_no"])
    chain_ok = False
    if on_chain_hash:
        chain_ok = on_chain_hash.lower() == ("0x" + case["report_hash"].lower())

    return jsonify({
        "ok": True,
        "case_no": case["case_no"],
        "db_hash": case["report_hash"],
        "recomputed_hash": recomputed,
        "content_integrity_ok": content_ok,
        "on_chain_hash": on_chain_hash,
        "chain_verified": chain_ok,
        "chain_connected": on_chain_hash is not None,
        "summary": (
            "核验通过：报告内容未被篡改，且与联盟链存证哈希一致"
            if (content_ok and chain_ok)
            else (
                "内容核验通过；当前环境未连接联盟链，仅完成本地内容完整性校验"
                if (content_ok and not chain_ok and on_chain_hash is None)
                else "核验未通过：哈希不一致，请核查数据完整性！"
            )
        ),
    })

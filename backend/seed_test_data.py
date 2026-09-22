# -*- coding: utf-8 -*-
"""
成套测试数据生成脚本
==============================
一键生成模拟仿真所需的全部测试账户与多场景交易数据：

  场景A  正常交易：normal1 → normal2 → normal3（小额、无风险）
  场景B  大额拆分洗钱：victim1 → scam_a(80000) → 拆分 4×20000 分散转出（规则1、规则2命中）
  场景C  纯中转洗钱：victim1 → relay1(60000) → scam_d(56000) 全额过手（规则3命中）
  场景D  环形资金流转：victim1 → cyc_a → cyc_b → cyc_c → cyc_a（规则4命中）
  场景E  分批资金归集：scam_b1/b2/b3 → collector1 汇聚（规则6命中）
  场景F  新账户异常收款：new_acct 无转出历史、短时批量收账（规则5命中）

执行方式（在 backend/ 目录下）：
  python seed_test_data.py

说明：所有交易均走 do_transfer 统一入口（上链 + 实时风控），与线上逻辑完全一致。
"""
from uuid import uuid4

from config import INIT_BALANCE, execute, query
from transaction import do_transfer
from werkzeug.security import generate_password_hash

# 测试密码统一为 123456，监管管理员密码 admin123


def ensure_user(username, password, real_name, role="user", status="approved", balance=INIT_BALANCE):
    """创建或复用测试用户"""
    row = query("SELECT * FROM users WHERE username=%s", (username,), one=True)
    if row:
        return row
    account_id = "ACCT" + uuid4().hex[:8].upper()
    execute(
        """INSERT INTO users (username, password_hash, account_id, real_name, id_card_no, phone,
                              role, status, risk_level, balance, approved_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,%s,NOW())""",
        (
            username,
            generate_password_hash(password),
            account_id,
            real_name,
            "110101199001011234",   # 测试身份证号
            "13800000000",          # 测试手机号
            role,
            status,
            balance,
        ),
    )
    return query("SELECT * FROM users WHERE username=%s", (username,), one=True)


def transfer(from_name, to_name, amount, remark=""):
    """通过统一转账入口执行一笔交易"""
    from_user = query("SELECT * FROM users WHERE username=%s", (from_name,), one=True)
    to_user = query("SELECT * FROM users WHERE username=%s", (to_name,), one=True)
    if not from_user or not to_user:
        print(f"  [跳过] {from_name} -> {to_name}：用户不存在")
        return None
    try:
        result = do_transfer(from_user, to_user, amount, remark)
        level = result["risk_level"]
        tag = {0: "正常", 1: "低风险", 2: "中风险", 3: "高风险"}[level]
        rules = result["rule_ids"] or "无"
        print(
            f"  [OK] {from_name}({result['from_account']}) -> {to_name}({result['to_account']}) "
            f"¥{amount:,.2f}  风险:{tag}  命中规则:{rules}"
        )
        return result
    except ValueError as e:
        print(f"  [失败] {from_name} -> {to_name} ¥{amount}: {e}")
        return None


def main():
    print("=" * 70)
    print("开始生成测试数据 ...")
    print("=" * 70)

    # ---- 1. 监管管理员 ----
    ensure_user("admin", "admin123", "监管管理员", role="admin")
    print("[管理员] admin / admin123")

    # ---- 2. 正常用户 ----
    normal_users = ["normal1", "normal2", "normal3", "victim1"]
    for name in normal_users:
        ensure_user(name, "123456", f"用户{name}")
    print("[用户] normal1 / normal2 / normal3 / victim1，密码均为 123456")

    # ---- 3. 洗钱链路账户（均为已实名账户） ----
    scam_users = [
        "scam_a", "scam_b1", "scam_b2", "scam_b3", "scam_b4",
        "relay1", "scam_d", "cyc_a", "cyc_b", "cyc_c",
        "collector1", "new_acct",
    ]
    for name in scam_users:
        ensure_user(name, "123456", f"账户{name}")
    print(f"[用户] 涉案/洗钱账户 {len(scam_users)} 个已创建")

    # ================= 场景A：正常交易 =================
    print("\n>>> 场景A 正常交易（无风险）")
    transfer("normal1", "normal2", 500, "正常生活转账")
    transfer("normal2", "normal3", 300, "正常生活转账")

    # ================= 场景B：大额拆分洗钱 =================
    print("\n>>> 场景B 大额拆分洗钱（规则1、规则2）")
    transfer("victim1", "scam_a", 80000, "投资理财本金")
    transfer("scam_a", "scam_b1", 20000, "")
    transfer("scam_a", "scam_b2", 20000, "")
    transfer("scam_a", "scam_b3", 20000, "")
    transfer("scam_a", "scam_b4", 20000, "")

    # ================= 场景C：纯中转洗钱 =================
    print("\n>>> 场景C 纯中转洗钱（规则3）")
    transfer("victim1", "relay1", 60000, "借贷还款")
    transfer("relay1", "scam_d", 56000, "")

    # ================= 场景D：环形资金流转 =================
    print("\n>>> 场景D 环形资金流转（规则4）")
    transfer("victim1", "cyc_a", 30000, "购物理财")
    transfer("cyc_a", "cyc_b", 30000, "")
    transfer("cyc_b", "cyc_c", 30000, "")
    transfer("cyc_c", "cyc_a", 30000, "")

    # ================= 场景E：分批资金归集 =================
    print("\n>>> 场景E 分批资金归集（规则6）")
    transfer("scam_b1", "collector1", 20000, "")
    transfer("scam_b2", "collector1", 20000, "")
    transfer("scam_b3", "collector1", 20000, "")

    # ================= 场景F：新账户异常收款 =================
    print("\n>>> 场景F 新账户异常收款（规则5）")
    transfer("scam_d", "new_acct", 20000, "")
    transfer("collector1", "new_acct", 15000, "")
    transfer("normal3", "new_acct", 8000, "")

    # ================= 结果汇总 =================
    print("\n" + "=" * 70)
    stats = {
        "user_count": query("SELECT COUNT(*) c FROM users WHERE role='user'", one=True)["c"],
        "tx_count": query("SELECT COUNT(*) c FROM transactions", one=True)["c"],
        "risk_tx": query("SELECT COUNT(*) c FROM transactions WHERE risk_level>0", one=True)["c"],
        "risk_accounts": query("SELECT COUNT(*) c FROM users WHERE risk_level>0", one=True)["c"],
    }
    print(f"测试数据生成完毕：{stats['user_count']} 个用户，{stats['tx_count']} 笔交易，"
          f"{stats['risk_tx']} 笔风险交易，{stats['risk_accounts']} 个风险账户")
    print("=" * 70)
    print("下一步操作指引：")
    print("  1. 打开系统首页 http://127.0.0.1:5000")
    print("  2. 使用 victim1 / 123456 登录，进入「诈骗报案提交」")
    print("  3. 填写被骗金额 170000（或任意金额），受骗时间段选今天，提交后自动生成溯源报告")
    print("  4. 使用 admin / admin123 登录监管端，查看案件溯源图谱与存证报告核验")
    print("=" * 70)


if __name__ == "__main__":
    main()

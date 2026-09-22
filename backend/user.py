# -*- coding: utf-8 -*-
"""
用户模块
==============================
功能接口：
  1. POST   /api/user/register         用户注册（含实名信息，待管理员审核）
  2. POST   /api/user/login            普通用户登录
  3. GET    /api/user/me               获取当前用户信息（需登录）
  4. GET    /api/user/accounts         已实名通过账户列表（供转账选择收款方）
  5. POST   /api/admin/login           监管管理员登录
  6. GET    /api/admin/pending_users   待审核实名用户列表（监管端）
  7. POST   /api/admin/audit_user      实名审核（通过/驳回）（监管端）
  8. GET    /api/admin/statistics      监管总览统计数据（监管端）
  9. GET    /api/admin/all_users       全部用户列表（监管端）

说明：
  - 使用 itsdangerous 签名令牌（localStorage 保存）实现简单鉴权；
  - 注册后用户为 pending 状态，需监管管理员审核通过后方可转账。
"""
import random
import re
import uuid

from flask import Blueprint, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from config import INIT_BALANCE, RISK_LEVEL_TEXT, SECRET_KEY, execute, query

bp = Blueprint("user", __name__, url_prefix="/api")

# 签名令牌序列化器（有效期 7 天）
serializer = URLSafeTimedSerializer(SECRET_KEY, salt="fundtrace-auth")

# ---------------------------------------------------------------------
# 鉴权辅助函数（供其他模块复用）
# ---------------------------------------------------------------------

def _get_token():
    """从请求头中提取 Bearer Token"""
    auth = request.headers.get("Authorization", "")
    return auth.replace("Bearer ", "").strip() if auth else None


def parse_token(token):
    """解析令牌，返回 {uid, role} 或 None"""
    if not token:
        return None
    try:
        return serializer.loads(token, max_age=7 * 24 * 3600)
    except (BadSignature, SignatureExpired):
        return None


def require_user():
    """校验普通用户身份，返回用户 dict；未登录返回 None"""
    data = parse_token(_get_token())
    if not data:
        return None
    return query("SELECT * FROM users WHERE id=%s", (data["uid"],), one=True)


def require_admin():
    """校验监管管理员身份，返回管理员 dict；未登录/非管理员返回 None"""
    data = parse_token(_get_token())
    if not data:
        return None
    admin = query("SELECT * FROM users WHERE id=%s", (data["uid"],), one=True)
    if not admin or admin["role"] != "admin":
        return None
    return admin


def make_token(user):
    """生成用户登录令牌"""
    return serializer.dumps({"uid": user["id"], "role": user["role"]})


def public_user(user):
    """用户信息脱敏输出（前端展示用）"""
    return {
        "id": user["id"],
        "username": user["username"],
        "account_id": user["account_id"],
        "real_name": user["real_name"],
        "id_card_no": user["id_card_no"],
        "phone": user["phone"],
        "role": user["role"],
        "status": user["status"],
        "risk_level": user["risk_level"],
        "risk_text": RISK_LEVEL_TEXT.get(user["risk_level"], "正常"),
        "balance": float(user["balance"] or 0),
        "created_at": user["created_at"].strftime("%Y-%m-%d %H:%M:%S") if user["created_at"] else "",
    }


def _gen_account_id():
    """生成唯一账户ID：ACCT + 8位随机大写十六进制"""
    return "ACCT" + uuid.uuid4().hex[:8].upper()


# ---------------------------------------------------------------------
# 普通用户接口
# ---------------------------------------------------------------------

@bp.route("/user/register", methods=["POST"])
def register():
    """用户注册（用户名+密码+实名信息），注册后进入待审核状态"""
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    real_name = (data.get("real_name") or "").strip()
    id_card_no = (data.get("id_card_no") or "").strip()
    phone = (data.get("phone") or "").strip()

    # ---- 基础校验 ----
    if not username or not password:
        return jsonify({"ok": False, "msg": "用户名与密码不能为空"}), 400
    if len(username) < 2 or len(username) > 20:
        return jsonify({"ok": False, "msg": "用户名长度需为 2-20 位"}), 400
    if len(password) < 6:
        return jsonify({"ok": False, "msg": "密码长度至少 6 位"}), 400
    if not real_name:
        return jsonify({"ok": False, "msg": "请填写真实姓名"}), 400
    if not id_card_no or not re.match(r"^\d{17}[\dXx]$", id_card_no):
        return jsonify({"ok": False, "msg": "身份证号格式不正确（18位）"}), 400
    if not phone or not re.match(r"^1\d{10}$", phone):
        return jsonify({"ok": False, "msg": "手机号格式不正确（11位）"}), 400

    # ---- 重名检查 ----
    if query("SELECT id FROM users WHERE username=%s", (username,), one=True):
        return jsonify({"ok": False, "msg": "用户名已被注册"}), 400

    # ---- 落库 ----
    account_id = _gen_account_id()
    execute(
        """INSERT INTO users (username, password_hash, account_id, real_name, id_card_no, phone,
                              role, status, risk_level, balance)
           VALUES (%s,%s,%s,%s,%s,%s,'user','pending',0,%s)""",
        (username, generate_password_hash(password), account_id, real_name, id_card_no, phone, INIT_BALANCE),
    )
    return jsonify({"ok": True, "msg": "注册成功，请等待监管管理员实名审核通过后登录", "account_id": account_id})


@bp.route("/user/login", methods=["POST"])
def user_login():
    """普通用户登录：仅允许已审核通过的用户登录"""
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    user = query("SELECT * FROM users WHERE username=%s AND role='user'", (username,), one=True)
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"ok": False, "msg": "用户名或密码错误"}), 401

    if user["status"] == "pending":
        return jsonify({"ok": False, "msg": "实名认证尚未审核，请等待管理员审核"}), 403
    if user["status"] == "rejected":
        return jsonify({"ok": False, "msg": "实名认证未通过，请联系监管管理员"}), 403

    return jsonify({"ok": True, "msg": "登录成功", "token": make_token(user), "user": public_user(user)})


@bp.route("/user/me", methods=["GET"])
def me():
    """获取当前登录用户信息"""
    user = require_user()
    if not user:
        return jsonify({"ok": False, "msg": "未登录或登录已过期"}), 401
    return jsonify({"ok": True, "user": public_user(user)})


@bp.route("/user/accounts", methods=["GET"])
def approved_accounts():
    """已实名审核通过的普通账户列表（供转账选择收款方）"""
    users = query(
        """SELECT * FROM users WHERE role='user' AND status='approved' ORDER BY account_id""")
    return jsonify({"ok": True, "accounts": [public_user(u) for u in users]})


# ---------------------------------------------------------------------
# 监管管理员接口
# ---------------------------------------------------------------------

@bp.route("/admin/login", methods=["POST"])
def admin_login():
    """监管管理员登录（模拟警方/监管）"""
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    admin = query("SELECT * FROM users WHERE username=%s AND role='admin'", (username,), one=True)
    if not admin or not check_password_hash(admin["password_hash"], password):
        return jsonify({"ok": False, "msg": "管理员用户名或密码错误"}), 401
    if admin["status"] != "approved":
        return jsonify({"ok": False, "msg": "该管理员账户未启用"}), 403

    return jsonify({"ok": True, "msg": "登录成功", "token": make_token(admin), "user": public_user(admin)})


@bp.route("/admin/pending_users", methods=["GET"])
def pending_users():
    """获取待实名审核的用户列表"""
    admin = require_admin()
    if not admin:
        return jsonify({"ok": False, "msg": "无权限，请先以监管管理员身份登录"}), 401
    users = query("SELECT * FROM users WHERE role='user' AND status='pending' ORDER BY created_at")
    return jsonify({"ok": True, "users": [public_user(u) for u in users]})


@bp.route("/admin/audit_user", methods=["POST"])
def audit_user():
    """实名审核：approve 通过 / reject 驳回，并记录审核日志"""
    admin = require_admin()
    if not admin:
        return jsonify({"ok": False, "msg": "无权限，请先以监管管理员身份登录"}), 401

    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    action = data.get("action")
    remark = (data.get("remark") or "").strip()

    if action not in ("approve", "reject"):
        return jsonify({"ok": False, "msg": "action 参数非法"}), 400

    user = query("SELECT * FROM users WHERE id=%s AND role='user'", (user_id,), one=True)
    if not user:
        return jsonify({"ok": False, "msg": "用户不存在"}), 404
    if user["status"] != "pending":
        return jsonify({"ok": False, "msg": "该用户已审核，请勿重复操作"}), 400

    if action == "approve":
        execute("UPDATE users SET status='approved', approved_at=NOW() WHERE id=%s", (user_id,))
    else:
        execute("UPDATE users SET status='rejected' WHERE id=%s", (user_id,))

    execute("INSERT INTO audit_logs (user_id, admin_id, action, remark) VALUES (%s,%s,%s,%s)",
            (user_id, admin["id"], action.upper(), remark))
    return jsonify({"ok": True, "msg": "审核成功" if action == "approve" else "已驳回"})


@bp.route("/admin/all_users", methods=["GET"])
def all_users():
    """全部用户列表（监管端）"""
    admin = require_admin()
    if not admin:
        return jsonify({"ok": False, "msg": "无权限"}), 401
    users = query("SELECT * FROM users ORDER BY created_at DESC")
    return jsonify({"ok": True, "users": [public_user(u) for u in users]})


@bp.route("/admin/statistics", methods=["GET"])
def statistics():
    """监管数据总览大屏统计数据"""
    admin = require_admin()
    if not admin:
        return jsonify({"ok": False, "msg": "无权限"}), 401

    def _one(sql, args=None):
        row = query(sql, args, one=True)
        return row.get("c", 0) if row else 0

    stats = {
        # ---- 顶部统计卡片 ----
        "user_count": _one("SELECT COUNT(*) c FROM users WHERE role='user'"),
        "pending_user_count": _one("SELECT COUNT(*) c FROM users WHERE role='user' AND status='pending'"),
        "tx_count": _one("SELECT COUNT(*) c FROM transactions"),
        "risk_tx_count": _one("SELECT COUNT(*) c FROM transactions WHERE risk_level>0"),
        "risk_account_count": _one("SELECT COUNT(*) c FROM users WHERE role='user' AND risk_level>0"),
        "high_risk_account_count": _one("SELECT COUNT(*) c FROM users WHERE risk_level>=3"),
        "case_count": _one("SELECT COUNT(*) c FROM cases"),
        "chain_tx_count": _one("SELECT COUNT(*) c FROM transactions WHERE chain_status='confirmed'"),
        # ---- 近7日交易趋势（ECharts 折线图） ----
        "tx_trend": [
            {
                "date": r["d"].strftime("%m-%d"),
                "count": r["c"],
                "amount": float(r["amt"] or 0),
            }
            for r in query(
                """SELECT DATE(tx_time) d, COUNT(*) c, SUM(amount) amt
                   FROM transactions
                   WHERE tx_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
                   GROUP BY DATE(tx_time) ORDER BY d"""
            )
        ],
        # ---- 交易风险等级分布（饼图） ----
        "risk_dist": [
            {"name": RISK_LEVEL_TEXT.get(int(r["risk_level"]), "正常"), "value": r["c"]}
            for r in query(
                "SELECT risk_level, COUNT(*) c FROM transactions GROUP BY risk_level ORDER BY risk_level"
            )
        ],
        # ---- 六维规则命中分布（柱状图） ----
        "rule_dist": [
            {"name": r["name"], "value": r["c"]}
            for r in query(
                """SELECT CONCAT('规则', rule_ids) name, COUNT(*) c
                   FROM risk_records GROUP BY rule_ids ORDER BY c DESC LIMIT 10"""
            )
        ],
        # ---- 高风险账户 TOP（表格） ----
        "high_risk_accounts": query(
            """SELECT u.account_id, u.username, u.risk_level, u.balance,
                      (SELECT COUNT(*) FROM transactions t WHERE t.from_account=u.account_id OR t.to_account=u.account_id) tx_n
               FROM users u WHERE u.role='user' AND u.risk_level>=2 ORDER BY u.risk_level DESC, tx_n DESC LIMIT 10"""
        ),
        # ---- 最新交易（大屏滚动） ----
        "recent_txs": query(
            """SELECT tx_hash, from_account, to_account, amount, risk_level,
                      DATE_FORMAT(tx_time,'%%m-%%d %%H:%%i:%%s') tx_time
               FROM transactions ORDER BY tx_time DESC LIMIT 10"""
        ),
    }
    return jsonify({"ok": True, "stats": stats})

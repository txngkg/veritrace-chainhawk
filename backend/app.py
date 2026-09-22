# -*- coding: utf-8 -*-
"""
项目主入口（Flask 应用）
==============================
职责：
  1. 注册三大业务蓝图：用户模块 / 交易风控模块 / 报案取证模块
  2. 托管前端静态页面（frontend/ 目录）
  3. 统一 JSON 中文输出

启动方式（在 backend/ 目录下）：
  python app.py
默认监听 http://127.0.0.1:5000
"""
import os

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

from config import FRONTEND_DIR
from report import bp as case_bp
from transaction import bp as tx_bp
from user import bp as user_bp

app = Flask(__name__, static_folder=None)

# 开启跨域（CORS）：
# 允许 file:// 直接打开前端 HTML（Origin 为 null）以及跨端口预览（如 IDE 内置预览）
# 时也能调用后端 API，便于竞赛演示与本地调试。
CORS(app)

# 统一 JSON 中文直出（Flask 2.3+ / 3.x）
try:
    app.json.ensure_ascii = False
except Exception:
    app.config["JSON_AS_ASCII"] = False

# 注册蓝图（/api 前缀）
app.register_blueprint(user_bp)
app.register_blueprint(tx_bp)
app.register_blueprint(case_bp)


# ---------------------------------------------------------------------
# 前端页面托管
# ---------------------------------------------------------------------
@app.route("/")
def index():
    """系统首页"""
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    """其余前端资源：HTML / css / js / 图片等"""
    return send_from_directory(FRONTEND_DIR, filename)


@app.errorhandler(404)
def not_found(e):
    return jsonify({"ok": False, "msg": "接口或页面不存在"}), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 60)
    print("  诈骗资金风险监测与报案溯源固证辅助系统")
    print(f"  访问地址：http://127.0.0.1:{port}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=True)

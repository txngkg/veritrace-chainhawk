#!/usr/bin/env bash
# 每次容器启动时执行：启动 MySQL / Ganache / 后端，并部署合约
# 说明：
#   - Ganache 使用固定助记词（经典 candy maple），账户与合约地址每次启动可预测；
#     account[0] = 0x90F8bf6A479f320ead074411a4B0e7944Ea8c9C1，已在监管白名单中。
#   - 链数据在内存中，每次重启清空；合约随启动重新部署（地址固定）。
set +e
cd "$(dirname "$0")/.."

log() { echo "[start.sh] $*"; }

# ---------- 1. MySQL ----------
log "启动 MySQL ..."
sudo service mysql start >/dev/null 2>&1
for i in $(seq 1 30); do
  if sudo mysqladmin ping 2>/dev/null | grep -q alive; then break; fi
  sleep 1
done
log "MySQL 已就绪"

# root 密码设置（幂等：能以密码 TCP 登录则跳过）
if ! mysql -h127.0.0.1 -uroot -p'06gkG12!' -e "SELECT 1" >/dev/null 2>&1; then
  log "设置 root 密码（mysql_native_password）..."
  sudo mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '06gkG12!'; FLUSH PRIVILEGES;"
fi

# 建库导表（仅当库不存在时，避免清空已有数据）
if ! mysql -h127.0.0.1 -uroot -p'06gkG12!' -e "USE fund_trace" >/dev/null 2>&1; then
  log "导入数据库 schema ..."
  mysql -h127.0.0.1 -uroot -p'06gkG12!' < backend-js/schema.sql
fi

# ---------- 2. Ganache ----------
rpc_probe() {
  curl -s -o /dev/null -X POST -H 'Content-Type: application/json' \
    --data '{"jsonrpc":"2.0","method":"web3_clientVersion","params":[],"id":1}' \
    http://127.0.0.1:7545
}
if rpc_probe; then
  log "Ganache 已在运行，跳过启动"
else
  log "启动 Ganache (7545, chainId 1337, 经典助记词) ..."
  nohup ./blockchain/node_modules/.bin/ganache \
    --host 0.0.0.0 --port 7545 \
    --chain.chainId 1337 \
    --wallet.mnemonic "candy maple cake sugar pudding cream honey rich smooth crumble sweet treat" \
    --wallet.totalAccounts 10 \
    > /tmp/ganache.log 2>&1 &
  for i in $(seq 1 30); do rpc_probe && break; sleep 1; done
fi
log "Ganache 就绪"

# ---------- 3. 部署合约（助记词固定 => 部署者与合约地址每次一致）----------
log "部署 FundTrace 合约 ..."
(cd blockchain && node deploy.js) || log "警告：合约部署失败，后端将以降级模式启动（详见 /tmp/ganache.log）"

# ---------- 4. 后端 ----------
if curl -s -o /dev/null http://127.0.0.1:5000/api/chain/status; then
  log "后端已在运行，跳过启动"
else
  log "启动 Node 后端 (5000) ..."
  (cd backend-js && nohup node server.js > /tmp/backend.log 2>&1 &)
fi
log "全部服务启动完成：后端 http://127.0.0.1:5000"

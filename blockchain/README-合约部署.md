# FundTrace 合约部署说明（Ganache 私有链模拟）

本项目为封闭模拟仿真环境，不对接任何真实公链/金融环境。所有"上链"行为均在本地 Ganache 私有链完成。

## 一、架构说明

- **身份体系**：无账号密码，用户身份 = Ganache 钱包地址；普通用户与监管管理员统一通过 MetaMask 连接钱包登录；
- **监管管理员**：后端 `backend-js/config.js` 中 `ADMIN_WALLETS` 白名单地址（默认 Ganache account[0]）；
- **后端**：`backend-js/`（Node.js + Express + ethers.js），不代签用户转账，仅做链上回读/索引与系统级哈希存证；
- **前端**：`frontend/`（静态页面，由后端同源托管）。

## 二、环境准备

### 1. 启动 Ganache

任选其一：

- **Ganache CLI（推荐）**：

  ```bash
  npm install -g ganache
  ganache --host 127.0.0.1 --port 8545 --chain.chainId 1337
  ```

- **Ganache 图形界面**：创建 Workspace，默认端口 8545，chainId 1337。

### 2. 安装后端依赖

```bash
cd blockchain
npm install          # solc@0.8.21 + ethers@5
cd ../backend-js
npm install          # express + cors + mysql2 + ethers@5
```

### 3. 初始化 MySQL

执行 `backend-js/schema.sql` 建表（会自动清空旧架构的预置账号与假数据）：

```bash
mysql -u root -p < backend-js/schema.sql
```

## 三、编译并部署合约

```bash
cd blockchain
node deploy.js
```

脚本使用 solc 编译 `contracts/FundTrace.sol`，部署到 Ganache 第一个账户，并将 地址+ABI 写入：

- `backend-js/contracts/FundTrace.json`（Node.js 后端读取，**必需**）

部署成功后修改 `backend-js/config.js`：
- `CONTRACT_ADDRESS` 自动读取产物（无需手改）；
- `ADMIN_WALLETS` 中填入你要作为监管管理员的 Ganache 钱包地址（默认 account[0]）；
- `BACKEND_PRIVATE_KEY`：如需后端独立存证账户，填写该账户私钥（留空则使用 Ganache account[0]）。

## 四、启动后端

```bash
cd backend-js
node server.js
```

访问 `http://127.0.0.1:5000`。确保 5000 端口没有被旧服务占用（如旧 Flask 后端需先停止）。

MetaMask 需切换至本地 Ganache 网络（RPC `http://127.0.0.1:8545`，chainId 1337），点击页面「连接钱包登录」完成授权。

## 五、合约函数一览

| 函数 | 说明 |
| --- | --- |
| `transferEth(to, nonce, signature)` | 用户 MetaMask 签名转账，合约验签后转发 ETH，记录发送方/接收方/金额(wei)/时间戳/风险等级/签名 |
| `riskLevelFromAmount(amountWei)` | 金额阈值风险判定：>=0.2 ETH 高，0.05~0.2 ETH 中，<0.05 ETH 低 |
| `recordRealName(account, nameHash)` | 实名信息哈希上链存证（明文绝不上链） |
| `recordCaseEvidence(caseNo, evidenceHash)` | 报案材料/溯源报告整体哈希上链存证 |
| `getTransaction(txId)` | 按交易ID查询链上交易完整数据（含风险等级与签名） |
| `getTxCount()` | 链上交易总数（遍历全量交易） |
| `getRealNameHash(account)` | 查询实名哈希 |
| `getCaseEvidenceHash(caseNo)` | 查询案件证据哈希 |

## 六、核心业务流

1. **转账上链**：用户输入 ETH 金额 → 后端 ETH 转 wei、签发 nonce 与签名消息 → MetaMask `personal_sign` → 前端 `eth_sendTransaction` 调用合约 `transferEth` 广播至 Ganache → 合约验签、自动判定风险等级并上链 → 后端回读登记；
2. **报案**：用户选择本人转出交易并提交 MetaMask 签名 → 后端计算证据哈希上链存证（不自动溯源）；
3. **审核**：监管端比对链上原始交易签名与报案提交签名，一致通过，不一致驳回并标记恶意报案；
4. **溯源取证**：仅监管端在案件审核通过后手动发起，自动遍历链上资金链路生成结构化证据包并上链存证。

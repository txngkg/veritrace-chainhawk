# 基于联盟链的诈骗资金风险监测与报案溯源固证辅助系统

> 学生竞赛仿真项目 · 封闭模拟交易环境 · 不对接真实银行 / 第三方支付 / 真实公链
> 系统无执法权限，不冻结账户、不自动追回资金，仅负责：**监测风险 · 留存不可篡改记录 · 案发后结构化固定警方可用证据 · 可视化溯源链路 · 生成可核验存证报告**

## ⚡ 在线演示（评审入口）

| 方式 | 链接 | 说明 |
| --- | --- | --- |
| 界面预览版 | https://\<你的GitHub用户名\>.github.io/veritrace-chainhawk/ | GitHub Pages 静态托管，永久有效，可浏览全部页面 |
| 完整功能在线版 | `<部署后替换为 5000 端口的 *.app.github.dev 地址>` | Codespaces 全栈真实运行（MySQL + Ganache + Node 后端），评审期保持运行 |
| 本地完整部署 | 见「四、一键部署步骤」 | 推荐方式：可体验 MetaMask 签名转账全流程 |

> 在线版若因云端环境休眠暂时不可访问，请通过大赛渠道联系团队启动，或按本地部署步骤约 5 分钟完成搭建。

## 一、项目简介

本系统构建"**事前实时风险监测 + 事后报案精准取证固证**"双业务闭环：

| 阶段 | 说明 |
| --- | --- |
| 事前（日常交易） | 每笔转账实时上链，六维规则引擎自动检测异常洗钱行为，账户风险自动分级，全程链上留痕、不可篡改 |
| 事后（诈骗案发） | 用户在线报案，以涉案首笔交易为起点自动穿透多层中转链路，生成警方可直接办案研判的结构化证据包 |

**技术栈**：Solidity 智能合约 + Ganache 本地联盟链模拟 · Python Flask · HTML/CSS/JS + ECharts · MySQL

**风险识别**：纯六维规则引擎（无 AI / 机器学习），命中规则数映射四级风险等级（0 正常 / 1 低 / 2 中 / ≥3 高）。

## 二、项目目录结构

```
d:\project
├── blockchain/                    # 区块链合约部分
│   ├── contracts/
│   │   └── FundTrace.sol          # 主智能合约：交易上链 / 风险上链 / 报告哈希存证
│   ├── deploy.js                  # 合约部署脚本（Ganache）
│   ├── package.json               # Node 依赖（solc / web3）
│   └── README-合约部署.md          # 合约编译、部署、验证说明
├── backend/                       # Python Flask 后端
│   ├── app.py                     # 项目主入口、路由、静态托管
│   ├── config.py                  # 数据库 / 区块链 / 规则阈值配置 + DB 访问函数
│   ├── blockchain.py              # 区块链对接模块（Ganache + 降级模拟哈希）
│   ├── user.py                    # 用户注册 / 登录 / 实名审核 / 监管登录
│   ├── transaction.py             # 转账上链 + 六维风控规则引擎（6 条规则完整代码）
│   ├── report.py                  # 报案受理 / 资金溯源 / 存证报告生成 / 哈希核验
│   ├── db.sql                     # MySQL 建库建表脚本
│   ├── seed_test_data.py          # 成套测试数据生成脚本
│   ├── requirements.txt           # Python 依赖
│   └── contracts.json             # （部署合约后自动生成）合约地址 + ABI
├── frontend/                      # 前端页面（14 个页面，覆盖需求 15 个页面）
│   ├── index.html                 # 1 系统首页
│   ├── register.html              # 2 用户注册实名页
│   ├── login.html                 # 3 用户登录页
│   ├── user-center.html           # 4 个人中心
│   ├── transfer.html              # 5 转账操作页
│   ├── record.html                # 6 个人交易记录
│   ├── report-case.html           # 7 诈骗报案提交页
│   ├── case-result.html           # 8 我的报案与溯源结果（ECharts 拓扑图）
│   ├── admin-login.html           # 9 监管管理员登录页
│   ├── admin-dashboard.html       # 10 监管数据总览大屏
│   ├── admin-audit.html           # 11 用户实名审核页
│   ├── admin-transaction.html     # 12 全网交易查询页
│   ├── admin-risk.html            # 13 风险账户管理页
│   ├── admin-case.html            # 14 案件溯源可视化 + 15 存证报告预览/下载/核验
│   ├── css/style.css              # 全局样式
│   └── js/
│       ├── common.js              # 请求封装 / 令牌管理 / 通用渲染
│       └── charts.js              # ECharts 拓扑图与大屏绘图
├── README.md                      # 本文件：部署教程
└── test-data.md                   # 成套测试数据与操作指引
```

> 说明：页面 14（案件溯源可视化）与页面 15（存证报告预览、下载、核验）合并于 `admin-case.html`，通过 Tab 切换呈现，功能完整。

## 三、环境依赖

| 组件 | 版本建议 | 说明 |
| --- | --- | --- |
| Python | 3.9+ | 后端运行环境 |
| Node.js | 14+（推荐 18/20 LTS） | 合约编译部署 |
| MySQL | 5.7 / 8.0 | 业务数据库 |
| Ganache | CLI 或 Desktop | 本地联盟链模拟 |
| 浏览器 | Chrome / Edge | 访问前端（需联网加载 ECharts CDN） |

## 四、一键部署步骤

### 第 1 步：初始化 MySQL

```bash
mysql -u root -p < backend/db.sql
```

创建数据库 `fund_trace` 及全部数据表。若本机 MySQL 账号密码不同，请修改 [backend/config.py](file:///d:/project/backend/config.py) 中的 `DB_CONFIG`。

### 第 2 步：启动 Ganache 联盟链

```bash
npm install -g ganache
ganache --host 127.0.0.1 --port 8545 --chain.chainId 1337
```

> 提示：若无 Node 环境或不想启用真实链，可跳过本步与第 3 步，系统将自动降级为"模拟哈希"模式（不影响全功能演示，仅链上核验显示未连接）。

### 第 3 步：编译部署智能合约

```bash
cd blockchain
npm install
node deploy.js
```

部署成功后自动生成 `backend/contracts.json`（含合约地址与 ABI）。详见 [blockchain/README-合约部署.md](file:///d:/project/blockchain/README-合约部署.md)。

### 第 4 步：启动 Flask 后端

```bash
cd backend
pip install -r requirements.txt
python app.py
```

启动输出：

```
访问地址：http://127.0.0.1:5000
```

### 第 5 步：生成测试数据（可选但推荐）

```bash
cd backend
python seed_test_data.py
```

自动创建测试账户并执行多场景交易（正常交易 / 大额拆分 / 纯中转 / 环形流转 / 资金归集 / 新账户异常收款）。

## 五、访问地址与测试账号

| 页面 | 地址 |
| --- | --- |
| 系统首页 | http://127.0.0.1:5000/index.html |
| 用户登录 | http://127.0.0.1:5000/login.html |
| 监管登录 | http://127.0.0.1:5000/admin-login.html |

| 角色 | 账号 | 密码 | 说明 |
| --- | --- | --- | --- |
| 普通用户（受害方） | victim1 | 123456 | 已被骗，用于报案演示 |
| 普通用户 | normal1 / normal2 / normal3 | 123456 | 正常交易用户 |
| 监管管理员 | admin | admin123 | 实名审核 / 监控 / 案件取证 |

## 六、核心业务流程演示

1. **用户注册** → 监管后台「实名审核」通过 → 登录个人中心；
2. **转账** → 实时上链 + 六维风控扫描，命中规则即时展示；
3. **报案**（用 victim1 登录）→ 填写被骗金额与时间段 → 自动溯源取证；
4. **监管端查看**（admin 登录）→ 案件溯源拓扑图 / 交易证据表 / 风险账户清单 / 报告预览下载 / 哈希核验；
5. **核验**：交易哈希链上核验、报告哈希三重核验（库内哈希 = 内容重算 = 链上存证）。

## 七、风险规则与等级对照

| 规则 | 名称 | 命中对象 |
| --- | --- | --- |
| 规则1 | 大额拆分转出 | 转出方 |
| 规则2 | 短时高频转账 | 转出方 |
| 规则3 | 纯中转洗钱账户 | 转出方 |
| 规则4 | 环形资金流转 | 整笔交易（双方） |
| 规则5 | 新账户异常收款 | 接收方 |
| 规则6 | 分批资金归集 | 接收方 |

命中 0 条=正常 · 1 条=低风险 · 2 条=中风险 · ≥3 条=高风险；所有风险行为、命中规则、风险等级全部上链存证。

## 八、取证报告五大部分（核心）

1. **案件基础证据信息**：报案人、报案时间、涉案起始交易哈希、涉案时间段、初始涉案金额；
2. **完整多级资金流转拓扑图**：节点按风险四色展示，悬浮查看角色与命中规则；
3. **标准化涉案交易明细证据表**：序号 / 收发账户 / 金额 / 时间 / 链上哈希 / 风险 / 规则，逐笔可核验；
4. **涉案可疑风险账户汇总清单**：中高风险账户 + 触发规则 + 链路位置；
5. **存证报告哈希固化与核验**：报告整体 SHA256 上链，监管端一键核验未篡改。

## 九、常见问题

| 问题 | 处理 |
| --- | --- |
| 提示"未连接联盟链" | 启动 Ganache 并执行 `node deploy.js` 后重启后端 |
| 登录提示待审核 | 用 admin 进入「用户实名审核」通过后重登 |
| 报案失败"未找到涉案交易" | 受骗时间段需覆盖实际转账时间 |
| ECharts 图不显示 | 页面需联网加载 ECharts CDN，或改用本地 echarts.min.js |
| MySQL 密码不同 | 修改 `backend/config.py` 中 `DB_CONFIG` |

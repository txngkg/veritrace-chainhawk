/**
 * 公共 JS 工具库（钱包身份体系）
 * ==============================
 * 功能：钱包管理、请求封装、MetaMask 交互、风险徽章、Toast、导航守卫、下拉菜单
 *
 * 身份体系说明：
 *   - 系统不再使用账号密码，用户身份 = Ganache 钱包地址；
 *   - 普通用户与监管管理员统一通过 MetaMask 连接钱包登录；
 *   - 后端校验钱包地址：命中白名单管理员地址 -> 监管工作台权限。
 */
const WALLET_KEY = "ft_wallet";
const USER_KEY = "ft_user";
const CONTRACT_KEY = "ft_contract";

/**
 * API 基地址
 * - 通过 http://127.0.0.1:5000 访问时：同源，使用相对路径（默认）
 * - 通过 file:// 直接打开 HTML 时：必须使用绝对地址
 */
const API_BASE = (typeof location !== "undefined" && location.protocol === "file:")
  ? "http://127.0.0.1:5000"
  : "";

/* ---------------- 钱包与用户信息管理 ---------------- */
function getWallet() { return localStorage.getItem(WALLET_KEY) || ""; }
function setWallet(address, user) {
  if (address) localStorage.setItem(WALLET_KEY, address);
  localStorage.setItem(USER_KEY, JSON.stringify(user || {}));
}
function getUser() {
  try { return JSON.parse(localStorage.getItem(USER_KEY) || "{}"); }
  catch (e) { return {}; }
}
function clearAuth() {
  localStorage.removeItem(WALLET_KEY);
  localStorage.removeItem(USER_KEY);
}
function logout() {
  clearAuth();
  location.href = "login.html";
}

/* ---------------- 合约信息缓存（地址 + ABI，供 MetaMask 调用转账合约） ---------------- */
async function getContractInfo() {
  let cached = null;
  try { cached = JSON.parse(localStorage.getItem(CONTRACT_KEY) || "null"); } catch (e) { cached = null; }
  // 优先拉取最新合约信息（合约可能重新部署），仅在请求失败时回退到缓存
  try {
    const res = await fetch(API_BASE + "/api/chain/status");
    const data = await res.json().catch(() => ({}));
    const addr = data && data.contract_address;
    if (addr && addr !== "0x0000000000000000000000000000000000000000") {
      const info = { contract_address: addr, abi: data.abi };
      localStorage.setItem(CONTRACT_KEY, JSON.stringify(info));
      return info;
    }
  } catch (e) { /* 后端不可达：回退缓存 */ }
  if (cached && cached.contract_address
    && cached.contract_address !== "0x0000000000000000000000000000000000000000") return cached;
  return null;
}

/* ---------------- 统一 API 请求封装 ---------------- */
/**
 * @param {string} path   接口路径（自动加 /api 前缀）
 * @param {object} opts   { method, body, auth }
 */
async function api(path, opts = {}) {
  const { method = "GET", body = null, auth = true } = opts;
  const headers = { "Content-Type": "application/json" };
  if (auth && getWallet()) headers["X-Wallet-Address"] = getWallet();
  let res;
  try {
    res = await fetch(API_BASE + "/api" + path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : null,
    });
  } catch (e) {
    return { ok: false, msg: "无法连接后端服务器，请通过 http://127.0.0.1:5000 访问本系统" };
  }
  let data;
  try { data = await res.json(); }
  catch (e) { data = { ok: false, msg: "服务器响应异常" }; }
  return data;
}

/* ---------------- MetaMask 交互（钱包连接 / 签名 / 转账） ---------------- */

/** Ganache 私有链参数（与 backend-js/config.js 保持一致） */
const GANACHE_CHAIN_ID_HEX = "0x539";            // 1337
const GANACHE_RPC_URL = "http://127.0.0.1:7545"; // Ganache GUI 默认 RPC

/** 检查 MetaMask 是否安装 */
function hasMetaMask() {
  return !!(window.ethereum && window.ethereum.isMetaMask);
}

/**
 * 确保 MetaMask 当前网络为 Ganache 私有链（chainId 1337）；
 * 未配置时自动引导添加并切换，避免因网络配置错误导致连接失败。
 */
async function ensureGanacheChain() {
  let chainId = "";
  try {
    chainId = await window.ethereum.request({ method: "eth_chainId" });
  } catch (e) {
    throw new Error("无法读取 MetaMask 网络状态，请解锁 MetaMask 插件后重试");
  }
  if (chainId === GANACHE_CHAIN_ID_HEX) return;
  try {
    await window.ethereum.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: GANACHE_CHAIN_ID_HEX }],
    });
  } catch (e) {
    const code = e && (e.code || (e.data && e.data.originalError && e.data.originalError.code));
    if (code === 4902) {
      // 该链未添加：引导添加 Ganache 网络
      try {
        await window.ethereum.request({
          method: "wallet_addEthereumChain",
          params: [{
            chainId: GANACHE_CHAIN_ID_HEX,
            chainName: "Ganache 本地私有链 (7545)",
            rpcUrls: [GANACHE_RPC_URL],
            nativeCurrency: { name: "ETH", symbol: "ETH", decimals: 18 },
          }],
        });
      } catch (e2) {
        if (e2 && e2.code === 4001) throw new Error("您拒绝了添加 Ganache 网络请求，无法连接钱包登录");
        throw new Error("添加 Ganache 网络失败，请在 MetaMask 中手动添加 RPC：" + GANACHE_RPC_URL);
      }
    } else if (code === 4001) {
      throw new Error("您在 MetaMask 中拒绝了切换网络，请手动切换到 Ganache（chainId 1337）后重试");
    } else {
      throw new Error("切换网络失败，请在 MetaMask 中手动切换到 Ganache（chainId 1337）后重试");
    }
  }
}

/** 唤起 MetaMask 连接钱包并切换至 Ganache 私有链，返回当前地址 */
async function connectMetaMask() {
  if (!hasMetaMask()) {
    throw new Error("未检测到 MetaMask，请先安装并启用 MetaMask 浏览器插件");
  }
  await ensureGanacheChain();
  let accounts;
  try {
    await window.ethereum.request({ method: "eth_requestAccounts" });
    accounts = await window.ethereum.request({ method: "eth_accounts" });
  } catch (e) {
    if (e && e.code === 4001) throw new Error("您在 MetaMask 弹窗中拒绝了连接请求，请重试并点击「同意」");
    if (e && e.code === -32002) throw new Error("MetaMask 已有待处理的连接请求，请点击浏览器右上角 MetaMask 图标完成授权后重试");
    throw new Error("MetaMask 连接失败：" + ((e && e.message) || "请确认插件已解锁"));
  }
  if (!accounts || !accounts.length) {
    throw new Error("未获得钱包授权，请允许连接");
  }
  return accounts[0];
}

/**
 * 登录页钱包状态自检：{ installed, unlocked, chainOk, chainId, accountCount, chainMatched }
 * chainMatched：MetaMask 当前网络是否就是后端连接的那条 Ganache（以后端合约地址上有无代码为准），
 * 用于发现"MetaMask 网络指向了另一条同 chainId 的链"导致的余额不同步。
 */
async function getWalletStatus() {
  if (!hasMetaMask()) return { installed: false };
  try {
    const chainId = await window.ethereum.request({ method: "eth_chainId" });
    let accounts = [];
    try { accounts = await window.ethereum.request({ method: "eth_accounts" }) || []; } catch (e) { accounts = []; }
    let chainMatched = null;
    try {
      const info = await getContractInfo();
      if (info && info.contract_address) {
        const code = await window.ethereum.request({
          method: "eth_getCode",
          params: [info.contract_address, "latest"],
        });
        chainMatched = !!(code && code !== "0x");
      }
    } catch (e) { chainMatched = null; } // RPC 不可达：多半是 MetaMask 网络 RPC 配错或已失效
    return {
      installed: true,
      chainId,
      chainOk: chainId === GANACHE_CHAIN_ID_HEX,
      accountCount: accounts.length,
      chainMatched,
    };
  } catch (e) {
    return { installed: true, unlocked: false };
  }
}

/** 获取 MetaMask 已授权的全部账户列表（供登录页切换不同账户登录） */
async function listMetaMaskAccounts() {
  if (!hasMetaMask()) {
    throw new Error("未检测到 MetaMask，请先安装并启用 MetaMask 浏览器插件");
  }
  await window.ethereum.request({ method: "eth_requestAccounts" });
  const accounts = await window.ethereum.request({ method: "eth_accounts" });
  return accounts || [];
}

/**
 * 尽力让 MetaMask 将活动账户切换为指定地址（静默执行，失败不影响登录流程），
 * 用于保证"登录身份"与"转账签名账户"一致。
 */
async function switchMetaMaskAccount(address) {
  if (!hasMetaMask()) return false;
  try {
    const cur = await window.ethereum.request({ method: "eth_accounts" });
    if (cur && cur[0] && cur[0].toLowerCase() === String(address).toLowerCase()) return true;
    // 重新唤起账户选择弹窗，由用户在 MetaMask 中确认切换
    await window.ethereum.request({
      method: "wallet_requestPermissions",
      params: [{ eth_accounts: {} }],
    });
    const after = await window.ethereum.request({ method: "eth_accounts" });
    return !!(after && after[0] && after[0].toLowerCase() === String(address).toLowerCase());
  } catch (e) {
    return false; // 用户取消或钱包不支持时静默跳过
  }
}

/**
 * 统一连接钱包登录：MetaMask 授权 -> 后端校验身份 -> 保存本地状态
 * @param {string} [address] 可选：指定以某个已授权账户登录；不传则使用 MetaMask 当前账户
 * @returns {{address:string, is_admin:boolean, role:string}}
 */
async function connectWallet(address) {
  const addr = address || (await connectMetaMask());
  const res = await fetch(API_BASE + "/api/wallet/verify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ address: addr }),
  });
  const data = await res.json();
  if (!data.ok) throw new Error(data.msg || "钱包校验失败");
  setWallet(data.address, { address: data.address, is_admin: data.is_admin, role: data.role });
  // 若登录账户与 MetaMask 活动账户不一致，尝试唤起切换（保证签名账户与身份一致）
  if (address) { try { await switchMetaMaskAccount(address); } catch (e) { /* 静默 */ } }
  // 预取合约信息缓存
  try { await getContractInfo(); } catch (e) { /* 忽略 */ }
  return data;
}

/* ---------------- Toast 提示 ---------------- */
function toast(msg, type = "info", duration = 3200) {
  let box = document.getElementById("toast-box");
  if (!box) {
    box = document.createElement("div");
    box.id = "toast-box";
    document.body.appendChild(box);
  }
  const el = document.createElement("div");
  el.className = "toast " + type;
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .4s"; }, duration);
  setTimeout(() => el.remove(), duration + 400);
}

/* ---------------- 渲染辅助 ---------------- */
/** 风险等级徽章 HTML（1低 / 2中 / 3高） */
function riskBadge(level, text) {
  const lv = Number(level) || 1;
  const map = { 1: "低风险", 2: "中风险", 3: "高风险" };
  return `<span class="badge badge-${lv}">${text || map[lv]}</span>`;
}
/** 案件状态徽章 */
function caseBadge(status) {
  const map = {
    submitted: ["badge-warn", "待审核"],
    approved: ["badge-blue", "已通过"],
    rejected: ["badge-3", "已驳回"],
    malicious: ["badge-3", "恶意报案"],
    traced: ["badge-green", "已溯源"],
  };
  const [cls, txt] = map[status] || ["badge-dim", status];
  return `<span class="badge ${cls}">${txt}</span>`;
}
/** 金额格式化（元） */
function fmtMoney(v) { return Number(v || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2 }); }
/** ETH 金额显示 */
function fmtEth(weiOrEth) {
  if (weiOrEth == null || weiOrEth === "") return "—";
  const s = String(weiOrEth);
  if (s.indexOf(".") >= 0) return s;              // 已是 ETH 字符串
  if (typeof window.ethers !== "undefined") {
    try { return window.ethers.utils.formatEther(s); } catch (e) { return s; }
  }
  return s;
}
/** 地址截断显示 */
function shortAddr(addr, len = 6) {
  if (!addr) return "—";
  return addr.length > len * 2 + 2 ? addr.slice(0, len + 2) + "…" + addr.slice(-len) : addr;
}
/** 交易哈希截断显示 */
function shortHash(h, len = 12) {
  if (!h) return "—";
  return h.length > len * 2 + 2 ? h.slice(0, len + 2) + "…" + h.slice(-len) : h;
}

/* ---------------- 页面鉴权守卫 ---------------- */
/** 未连接钱包跳转 */
function guardLogin() {
  if (!getWallet()) { location.href = "login.html"; return false; }
  return true;
}
/** 非监管管理员跳转（白名单钱包才有监管权限） */
function guardAdmin() {
  const u = getUser();
  if (!getWallet() || !u.is_admin) { location.href = "login.html"; return false; }
  return true;
}

/* ---------------- 下拉菜单（点击弹出 · 点击空白自动收起） ---------------- */
function toggleDropdown(id) {
  const panel = document.getElementById(id);
  if (!panel) return;
  const isOpen = panel.classList.contains("show");
  document.querySelectorAll(".dropdown-panel.show").forEach((p) => p.classList.remove("show"));
  if (!isOpen) panel.classList.add("show");
}
document.addEventListener("click", (e) => {
  if (!e.target.closest(".dropdown")) {
    document.querySelectorAll(".dropdown-panel.show").forEach((p) => p.classList.remove("show"));
  }
});

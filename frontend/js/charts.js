/**
 * ECharts 绘图模块
 * 1. drawTopology：资金流转拓扑图（账户节点按风险等级四色展示，悬浮显示详情）
 * 2. drawRiskDist：链上交易风险等级分布（环形饼图）
 * 3. drawCaseStatus：报案案件状态分布（环形饼图）
 */
/* eslint-disable no-unused-vars */

/** 账户节点风险色映射（绿/黄/橙/红，对应风险1低/2中/3高） */
const RISK_COLORS = ["#10b981", "#f59e0b", "#f97316", "#ef4444"];

/**
 * 资金流向拓扑图（有向图 + 悬浮详情）
 * @param {HTMLElement} dom  图表容器
 * @param {object} topology  { nodes: [{id,name,riskLevel,riskText,role,depth,txCount}], links: [{source,target,amountEth,timestamp,riskLevel,riskText,signature,traceDepth}] }
 */
function drawTopology(dom, topology) {
  if (!dom || !topology) return;
  const chart = echarts.init(dom);

  // 节点按风险等级分桶（绿=低 / 黄=中 / 红=高）
  const groups = [[], [], [], []];
  topology.nodes.forEach((n) => {
    const lv = Math.min(Math.max(Number(n.riskLevel) || 1, 1), 3);
    groups[lv].push(n);
  });

  const option = {
    backgroundColor: "transparent",
    tooltip: {
      trigger: "item",
      backgroundColor: "#ffffff",
      borderColor: "#e5e7eb",
      textStyle: { color: "#1f2937", fontSize: 12 },
      formatter: function (params) {
        if (params.dataType === "node") {
          const n = params.data;
          return [
            `<b style="font-size:14px">${n.name}</b>`,
            `角色：${n.role || "—"}`,
            `风险等级：${n.riskText || "—"}`,
            `参与交易笔数：${n.txCount || 0}`,
            `溯源深度：第${n.depth}层`,
          ].join("<br/>");
        }
        if (params.dataType === "edge") {
          const e = params.data;
          return [
            `<b>${e.source} → ${e.target}</b>`,
            `金额：${e.amountEth || e.value} ETH`,
            `时间：${e.timestamp ? new Date(e.timestamp * 1000).toLocaleString() : "—"}`,
            `风险：${e.riskText || "—"}`,
            `链上交易ID：#${e.txId}`,
          ].join("<br/>");
        }
        return params.name;
      },
    },
    legend: {
      data: ["低风险账户", "中风险账户", "高风险账户"],
      textStyle: { color: "#6b7280" },
      top: 0,
    },
    series: [
      {
        type: "graph",
        layout: "force",
        roam: true,
        draggable: true,
        label: { show: true, position: "bottom", fontSize: 10, color: "#6b7280", formatter: (p) => p.name },
        edgeSymbol: ["none", "arrow"],
        edgeSymbolSize: [0, 9],
        lineStyle: { color: "#3b82f6", opacity: 0.65, width: 2, curveness: 0.12 },
        force: { repulsion: 320, edgeLength: [70, 150], gravity: 0.08 },
        emphasis: { focus: "adjacency", lineStyle: { width: 3.5, color: "#0ea5e9" } },
        categories: [
          { name: "低风险账户", itemStyle: { color: RISK_COLORS[1] } },
          { name: "中风险账户", itemStyle: { color: RISK_COLORS[2] } },
          { name: "高风险账户", itemStyle: { color: RISK_COLORS[3] } },
        ],
        data: topology.nodes.map((n) => {
          const lv = Math.min(Math.max(Number(n.riskLevel) || 1, 1), 3);
          return {
            name: n.name,
            category: lv - 1,
            symbolSize: 30 + (n.role && n.role.indexOf("归集终点") >= 0 ? 8 : 0),
            itemStyle: {
              color: RISK_COLORS[lv],
              shadowBlur: 8 + lv * 6,
              shadowColor: RISK_COLORS[lv],
            },
            ...n,
          };
        }),
        links: (topology.links || []).map((e) => ({
          source: e.source,
          target: e.target,
          value: e.amountEth,
          lineStyle: {
            color: Number(e.riskLevel) >= 3 ? "#ef4444" : Number(e.riskLevel) >= 2 ? "#f97316" : "#3b82f6",
            opacity: Number(e.riskLevel) >= 2 ? 0.9 : 0.5,
          },
          ...e,
        })),
      },
    ],
  };
  chart.setOption(option);
  const resize = () => chart.resize();
  window.addEventListener("resize", resize);
  return chart;
}

/**
 * 链上交易风险等级分布（环形饼图）
 * @param {HTMLElement} dom 图表容器
 * @param {Array} data [{name, value}]
 */
function drawRiskDist(dom, data) {
  const chart = echarts.init(dom);
  chart.setOption({
    tooltip: { trigger: "item", backgroundColor: "#ffffff", borderColor: "#e5e7eb", textStyle: { color: "#1f2937" } },
    legend: { orient: "vertical", right: 0, top: "center", textStyle: { color: "#6b7280" } },
    color: ["#f59e0b", "#f97316", "#ef4444"],
    series: [
      {
        name: "风险等级",
        type: "pie",
        radius: ["45%", "70%"],
        center: ["38%", "50%"],
        label: { color: "#6b7280", formatter: "{b}: {c}" },
        data: data || [],
      },
    ],
  });
  const resize = () => chart.resize();
  window.addEventListener("resize", resize);
  return chart;
}

/**
 * 报案案件状态分布（环形饼图）
 * @param {HTMLElement} dom 图表容器
 * @param {Array} data [{name, value}]
 */
function drawCaseStatus(dom, data) {
  const chart = echarts.init(dom);
  chart.setOption({
    tooltip: { trigger: "item", backgroundColor: "#ffffff", borderColor: "#e5e7eb", textStyle: { color: "#1f2937" } },
    legend: { orient: "vertical", right: 0, top: "center", textStyle: { color: "#6b7280" } },
    color: ["#f59e0b", "#3b82f6", "#94a3b8", "#ef4444", "#10b981"],
    series: [
      {
        name: "案件状态",
        type: "pie",
        radius: ["45%", "70%"],
        center: ["38%", "50%"],
        label: { color: "#6b7280", formatter: "{b}: {c}" },
        data: data || [],
      },
    ],
  });
  const resize = () => chart.resize();
  window.addEventListener("resize", resize);
  return chart;
}

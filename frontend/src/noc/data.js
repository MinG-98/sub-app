export function healthRows(d) {
  const c = d.collector || {};
  const agentNodes = (d.latency?.nodes || []).filter((n) => (n.entry?.state || "") === "waiting_agent").length;
  const row = (name, ok, detail, at, text) => ({
    name,
    tone: ok === null ? "" : ok ? "is-ok" : "is-bad",
    text: text || (ok === null ? "未知" : ok ? "正常" : "异常"),
    detail,
    at,
  });
  const collectorStatus = String(c.status || "").toLowerCase();
  const collectorAt = c.finished_at || c.started_at || c.last_success_at;
  const hasCollectorCounts = Number.isFinite(Number(c.nodes_total)) && Number.isFinite(Number(c.samples_written));
  const collectorCounts = hasCollectorCounts ? `已写入 ${Number(c.samples_written)}/${Number(c.nodes_total)} 个采样` : "";
  let collectorRow;

  switch (collectorStatus) {
    case "success":
      collectorRow = row("哪吒采集", true, collectorCounts || "采集成功", collectorAt, "正常");
      break;
    case "partial":
      collectorRow = row("哪吒采集", false, c.error || collectorCounts || "部分节点采集失败", collectorAt, "部分成功");
      break;
    case "running":
      collectorRow = row("哪吒采集", null, collectorCounts || "采集任务运行中", collectorAt, "采集中");
      break;
    case "never_run":
      collectorRow = row(
        "哪吒采集",
        null,
        c.configured === true ? "已配置，等待首次采集" : "未配置哪吒采集凭据",
        collectorAt,
        "未运行",
      );
      break;
    case "unconfigured":
      collectorRow = row("哪吒采集", null, c.error || "未配置哪吒采集凭据", collectorAt, "未配置");
      break;
    case "error":
      collectorRow = row("哪吒采集", false, c.error || "采集失败", collectorAt, "异常");
      break;
    default:
      collectorRow = row("哪吒采集", null, "采集状态不可识别", collectorAt, "未知");
  }
  return [
    row("应用服务", true, "控制面响应正常", d.latency?.finished_at),
    row("数据库", c.database === undefined ? true : !!c.database, "读写正常", d.latency?.finished_at),
    collectorRow,
    row(
      "代理统计",
      c.proxy ? c.proxy.state !== "error" : null,
      c.proxy?.reason || "流量计数来源",
      c.proxy?.checked_at,
    ),
    row(
      "远端 Agent",
      agentNodes ? false : true,
      agentNodes ? `${agentNodes} 个节点等待 Agent 上线` : "全部节点已上报",
      d.latency?.finished_at,
    ),
  ];
}

export function activityByUser(devices = []) {
  const map = new Map();
  for (const d of devices) {
    const k = d.friend_uid || "—";
    const cur = map.get(k) || { uid: k, fetches: 0, devices: 0 };
    cur.fetches += Number(d.fetch_count) || 0;
    cur.devices += 1;
    map.set(k, cur);
  }
  return [...map.values()].sort((a, b) => b.fetches - a.fetches);
}

export const isAnnouncement = (n) => n.server === "127.0.0.1";
export const isRealNode = (n) => n.server !== "127.0.0.1";

export function nodeStatus(n) {
  if (!n.enabled) return { tone: "", text: "已停用" };
  if (!n.collector || !n.collector.mapped) return { tone: "", text: "未接入监控" };
  if (n.collector.online) return { tone: "is-ok", text: "在线" };
  return { tone: "is-warn", text: "监控中 · 离线" };
}

export function probeState(node) {
  const e = node.entry || {};
  const p = node.proxy || {};
  if (e.state === "pending" || p.state === "pending") return { key: "pending", tone: "is-info", text: "探测中" };
  if (e.state === "waiting_agent" || p.state === "waiting_agent") return { key: "waiting", tone: "is-accent", text: "等待 Agent" };
  if (!e.state && !p.state) return { key: "untested", tone: "", text: "尚未测试" };
  if (e.state === "ok" && p.state === "ok") return { key: "connected", tone: "is-ok", text: "已连接" };
  if (e.state === "ok" && p.state !== "ok") {
    if ((p.reason || p.value || "").includes("超时")) return { key: "timeout", tone: "is-warn", text: "出口探测超时" };
    return { key: "exit_fail", tone: "is-warn", text: "入口正常 · 出口失败" };
  }
  if (e.state !== "ok") return { key: "unreachable", tone: "is-bad", text: "入口不可达" };
  return { key: "untested", tone: "", text: "尚未测试" };
}

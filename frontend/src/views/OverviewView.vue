<script setup>
import { ref, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api.js";
import { pushToast } from "../store.js";
import { formatBytes, timeAgo } from "../format.js";

const router = useRouter();
const loading = ref(true);
const stats = ref(null);
const nodes = ref([]);
const latency = ref(null);
const probing = ref(false);

// Announcement entries (e.g. "请更新订阅") are stored as real node rows so
// they show up in subscription clients, but they point at 127.0.0.1 and are
// never wired to a collector. Real proxy nodes never use loopback as their
// server, so that's what distinguishes them here — the overview is about
// operational node health, not messages meant for end users.
const realNodes = computed(() => nodes.value.filter((n) => n.server !== "127.0.0.1"));

function nodeStatus(node) {
  if (!node.enabled) return { cls: "off", dot: "off", text: "已停用" };
  if (!node.collector || !node.collector.mapped) return { cls: "unknown", dot: "unknown", text: "未接入监控 · 状态未知" };
  if (node.collector.online) return { cls: "online", dot: "online", text: "在线 · 已接入哪吒监控" };
  return { cls: "warn", dot: "warn", text: "已接入监控 · 当前离线" };
}

function probeMeter(entry) {
  if (!entry) return { pct: 0, cls: "critical", label: "未测试" };
  if (entry.state === "ok" && entry.ms != null) {
    const pct = Math.min(100, Math.round((entry.ms / 400) * 100));
    const cls = entry.ms < 150 ? "ok" : entry.ms < 400 ? "warn" : "critical";
    return { pct, cls, label: `${entry.ms}ms` };
  }
  if (entry.state === "pending") return { pct: 30, cls: "warn", label: "探测中" };
  return { pct: 0, cls: "critical", label: entry.value || "未测试" };
}

async function load() {
  loading.value = true;
  try {
    const [s, n, l] = await Promise.all([api.stats(), api.nodes(), api.latencyStatus()]);
    stats.value = s;
    nodes.value = n;
    latency.value = l;
  } catch (e) {
    pushToast(e.message || "加载概览失败");
  } finally {
    loading.value = false;
  }
}

async function runProbe() {
  probing.value = true;
  try {
    await api.triggerLatencyProbe();
    pushToast("已触发延迟探测，稍后刷新查看结果", "success");
    setTimeout(load, 4000);
  } catch (e) {
    pushToast(e.message || "触发探测失败");
  } finally {
    probing.value = false;
  }
}

onMounted(load);
</script>

<template>
  <section v-if="loading" class="panel-empty">加载中…</section>
  <section v-else>
    <div class="hero">
      <div>
        <div class="eyebrow">Operations Overview</div>
        <h1>把状态看清，把问题提前发现。</h1>
        <p class="lede">整合节点健康、用户流量、设备审计与 Agent 心跳 — 一屏看完，而不是四处点。</p>
      </div>
      <div class="hero-actions">
        <button class="btn primary" @click="load">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3"/><path d="M18 4v4h-4M6 20v-4h4"/></svg>
          立即刷新
        </button>
      </div>
    </div>

    <div class="kpis">
      <div class="kpi">
        <div class="kpi-label">节点 <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3.5" y="4" width="17" height="7" rx="1.6"/><rect x="3.5" y="13" width="17" height="7" rx="1.6"/></svg></div>
        <div class="kpi-value mono">{{ stats.nodes }}</div>
        <div class="kpi-sub">{{ nodes.filter(n => n.collector && n.collector.online).length }} 个在线</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">用户 <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="8" r="3.2"/><path d="M5 20c0-3.6 3.1-5.8 7-5.8s7 2.2 7 5.8"/></svg></div>
        <div class="kpi-value mono">{{ stats.friends }}</div>
        <div class="kpi-sub">订阅账户</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">设备 <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="5" y="3.5" width="14" height="17" rx="2"/></svg></div>
        <div class="kpi-value mono">{{ stats.devices }}</div>
        <div class="kpi-sub">{{ stats.active_devices_24h }} 个 24h 内活跃</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">24h 流量 <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 15l5-6 4 4 7-9"/></svg></div>
        <div class="kpi-value" v-html="formatBytes(stats.flow_24h_bytes, true)"></div>
        <div class="kpi-sub">近 24 小时</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">24h 拉取 <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg></div>
        <div class="kpi-value mono">{{ stats.fetch_24h }}</div>
        <div class="kpi-sub">订阅请求</div>
      </div>
    </div>

    <div class="grid2">
      <div class="panel" style="margin-bottom:0;">
        <div class="panel-head">
          <div>
            <div class="panel-eyebrow">Node Inventory</div>
            <div class="panel-title">节点状态</div>
          </div>
          <button class="panel-link" @click="router.push({ name: 'nodes' })">查看全部 →</button>
        </div>
        <div v-if="!realNodes.length" class="panel-empty">尚未添加任何节点</div>
        <div v-else class="nodes">
          <div v-for="n in realNodes.slice(0, 4)" :key="n.id" class="node-card" :style="!n.enabled ? 'opacity:0.6' : ''">
            <div class="node-top">
              <div class="node-id">
                <span class="dot" :class="nodeStatus(n).dot"></span>
                <div><div class="node-name">{{ n.name }}</div><div class="node-server">{{ n.server }} · {{ n.port }}</div></div>
              </div>
              <span class="proto-tag">{{ n.protocol }}</span>
            </div>
            <div class="node-status-line" :class="'is-' + nodeStatus(n).cls">{{ nodeStatus(n).text }}</div>
            <div class="node-stats">
              <div><div class="node-stat-label">24H 流量</div><div class="node-stat-value" v-html="formatBytes(n.traffic['24h'].total)"></div></div>
              <div><div class="node-stat-label">已分配</div><div class="node-stat-value">{{ n.allocated_to }} 人</div></div>
            </div>
          </div>
        </div>
      </div>

      <div class="panel" style="margin-bottom:0;">
        <div class="panel-head">
          <div><div class="panel-eyebrow">Latency Probe</div><div class="panel-title">延迟探针</div></div>
          <button class="panel-link" :disabled="probing" @click="runProbe">{{ probing ? "触发中…" : "重新探测" }}</button>
        </div>
        <div class="probe-row">
          <div class="probe-name">控制面</div>
          <div class="probe-meter"><div class="probe-fill" :class="probeMeter(latency.control).cls" :style="{ width: probeMeter(latency.control).pct + '%' }"></div></div>
          <div class="probe-value mono">{{ probeMeter(latency.control).label }}</div>
        </div>
        <div class="probe-row">
          <div class="probe-name">节点入口</div>
          <div class="probe-meter"><div class="probe-fill" :class="probeMeter({ state: latency.summary?.entry_avg_ms != null ? 'ok' : 'bad', ms: latency.summary?.entry_avg_ms }).cls" :style="{ width: probeMeter({ state: latency.summary?.entry_avg_ms != null ? 'ok' : 'bad', ms: latency.summary?.entry_avg_ms }).pct + '%' }"></div></div>
          <div class="probe-value mono">{{ probeMeter({ state: latency.summary?.entry_avg_ms != null ? 'ok' : 'bad', ms: latency.summary?.entry_avg_ms }).label }}</div>
        </div>
        <div class="probe-row">
          <div class="probe-name">代理出口</div>
          <div class="probe-meter"><div class="probe-fill" :class="probeMeter({ state: latency.summary?.proxy_avg_ms != null ? 'ok' : 'bad', ms: latency.summary?.proxy_avg_ms }).cls" :style="{ width: probeMeter({ state: latency.summary?.proxy_avg_ms != null ? 'ok' : 'bad', ms: latency.summary?.proxy_avg_ms }).pct + '%' }"></div></div>
          <div class="probe-value mono">{{ probeMeter({ state: latency.summary?.proxy_avg_ms != null ? 'ok' : 'bad', ms: latency.summary?.proxy_avg_ms }).label }}</div>
        </div>
        <div class="probe-footnote">
          入口为节点端口握手，出口为通过节点访问 {{ latency.target?.url || "外部探测目标" }}；不使用普通网页延迟冒充代理延迟。
          <template v-if="latency.finished_at">上次探测：{{ timeAgo(latency.finished_at) }}</template>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head">
        <div><div class="panel-eyebrow">Status Semantics</div><div class="panel-title">状态色彩含义</div></div>
      </div>
      <div class="legend">
        <div class="legend-item"><span class="dot2" style="background:var(--accent)"></span><div class="legend-copy"><b>信号青 · 交互色</b><span>品牌 / 可点击元素，不代表健康状态</span></div></div>
        <div class="legend-item"><span class="dot2" style="background:var(--ok)"></span><div class="legend-copy"><b>绿 · 正常</b><span>已确认在线，或凭据生效中</span></div></div>
        <div class="legend-item"><span class="dot2" style="background:var(--warn)"></span><div class="legend-copy"><b>琥珀 · 需要关注</b><span>宽限期、配额接近上限、拉取行为异常</span></div></div>
        <div class="legend-item"><span class="dot2" style="background:var(--critical)"></span><div class="legend-copy"><b>红 · 异常</b><span>已吊销、超额、探测失败、已封锁</span></div></div>
        <div class="legend-item"><span class="dot2" style="background:var(--slate)"></span><div class="legend-copy"><b>石板灰 · 未知 / 中性</b><span>未接入监控时用灰色，不用绿色占位</span></div></div>
      </div>
    </div>
  </section>
</template>

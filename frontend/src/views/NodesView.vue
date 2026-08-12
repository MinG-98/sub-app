<script setup>
import { computed, onMounted, ref } from "vue";
import { api } from "../api.js";
import { formatBytes, timeAgo } from "../format.js";
import { pushToast } from "../store.js";
import Modal from "../components/Modal.vue";
import { isRealNode, nodeStatus } from "../noc/data.js";

const loading = ref(true);
const error = ref("");
const nodes = ref([]);
const search = ref("");
const statusFilter = ref("all");
const sortKey = ref("name");
const sortDir = ref("asc");

const showBulkModal = ref(false);
const bulkText = ref("");
const bulkName = ref("");
const bulkBusy = ref(false);
const bulkError = ref("");

const editing = ref(null);
const editForm = ref({ name: "", enabled: true, nezha_server_id: "", per_user_enabled: false, uri: "" });
const editBusy = ref(false);
const editError = ref("");
const preparing = ref(false);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    nodes.value = (await api.nodes()).filter(isRealNode);
  } catch (e) {
    error.value = e.message || "节点加载失败";
  } finally {
    loading.value = false;
  }
}

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase();
  return nodes.value.filter((node) => {
    const status = nodeStatus(node);
    const statusMatch = statusFilter.value === "all"
      || (statusFilter.value === "online" && status.text === "在线")
      || (statusFilter.value === "attention" && status.text !== "在线");
    const queryMatch = !q || [node.name, node.protocol, node.server].some((value) => String(value || "").toLowerCase().includes(q));
    return statusMatch && queryMatch;
  });
});

function toggleSort(key) {
  if (sortKey.value === key) sortDir.value = sortDir.value === "asc" ? "desc" : "asc";
  else {
    sortKey.value = key;
    sortDir.value = "asc";
  }
}

function sortArrow(key) {
  return sortKey.value === key ? (sortDir.value === "asc" ? "▲" : "▼") : "";
}

function nodeSortValue(node, key) {
  if (key === "traffic") return Number(node.traffic?.["24h"]?.total || 0);
  if (key === "status") return nodeStatus(node).text;
  return String(key === "protocol" ? node.protocol : node.name || "").toLowerCase();
}

const sortedNodes = computed(() => {
  const direction = sortDir.value === "asc" ? 1 : -1;
  return [...filtered.value].sort((a, b) => {
    const left = nodeSortValue(a, sortKey.value);
    const right = nodeSortValue(b, sortKey.value);
    if (typeof left === "number" && typeof right === "number") return (left - right) * direction;
    return String(left).localeCompare(String(right), "zh-CN") * direction;
  });
});

const maxTraffic = computed(() => Math.max(1, ...sortedNodes.value.map((node) => Number(node.traffic?.["24h"]?.total || 0))));

function adapterText(node) {
  return node.per_user_capability?.ready ? "已适配" : "无";
}

function statusTone(node) {
  const tone = nodeStatus(node).tone;
  return tone === "is-ok" ? "ok" : tone === "is-warn" ? "warn" : "idle";
}

async function submitBulk() {
  if (!bulkText.value.trim()) {
    bulkError.value = "请输入至少一条节点链接";
    return;
  }
  bulkBusy.value = true;
  bulkError.value = "";
  try {
    const result = await api.createNodes({ bulk: bulkText.value, name: bulkName.value || undefined });
    pushToast(`已创建 ${result.created.length} 个节点${result.skipped.length ? `，跳过 ${result.skipped.length} 条` : ""}`, "success");
    showBulkModal.value = false;
    bulkText.value = "";
    bulkName.value = "";
    await load();
  } catch (e) {
    bulkError.value = e.message || "创建失败";
  } finally {
    bulkBusy.value = false;
  }
}

function openEdit(node) {
  editing.value = node;
  editForm.value = {
    name: node.name,
    enabled: node.enabled,
    nezha_server_id: node.nezha_server_id ?? "",
    per_user_enabled: node.per_user_enabled,
    uri: "",
  };
  editError.value = "";
}

async function submitEdit() {
  if (!editing.value) return;
  editBusy.value = true;
  editError.value = "";
  try {
    const payload = {
      name: editForm.value.name,
      enabled: editForm.value.enabled,
      per_user_enabled: editForm.value.per_user_enabled,
      nezha_server_id: editForm.value.nezha_server_id === "" ? null : Number(editForm.value.nezha_server_id),
    };
    if (editForm.value.uri.trim()) payload.uri = editForm.value.uri.trim();
    await api.updateNode(editing.value.id, payload);
    pushToast("节点已更新", "success");
    editing.value = null;
    await load();
  } catch (e) {
    editError.value = e.message || "更新失败";
  } finally {
    editBusy.value = false;
  }
}

async function prepareCredentials() {
  if (!editing.value || preparing.value) return;
  preparing.value = true;
  try {
    const result = await api.prepareNodeCredentials(editing.value.id);
    pushToast(result.activated ? "独立凭据已同步" : "同步已执行，仍有凭据待处理", result.activated ? "success" : "error");
    await load();
  } catch (e) {
    pushToast(e.message || "同步凭据失败");
  } finally {
    preparing.value = false;
  }
}

async function removeNode() {
  if (!editing.value || !window.confirm(`确定删除节点「${editing.value.name}」？此操作不可撤销。`)) return;
  editBusy.value = true;
  try {
    await api.deleteNode(editing.value.id);
    pushToast("节点已删除", "success");
    editing.value = null;
    await load();
  } catch (e) {
    editError.value = e.message || "删除失败";
  } finally {
    editBusy.value = false;
  }
}

onMounted(load);
</script>

<template>
  <header class="view-hd">
    <div class="view-copy"><span class="lbl lbl-am">Node inventory</span><h2>节点管理</h2><p>查看真实节点状态、流量、分配关系与用户级适配能力。</p></div>
    <div class="view-actions">
      <label class="search"><span aria-hidden="true">/</span><span class="visually-hidden">搜索节点</span><input v-model="search" placeholder="搜索节点、协议或服务器"></label>
      <button class="b b-am" @click="showBulkModal = true">批量添加</button>
    </div>
  </header>

  <div class="filter-line p">
    <div class="seg" role="group" aria-label="节点状态筛选">
      <button :aria-pressed="statusFilter === 'all'" @click="statusFilter = 'all'">全部</button>
      <button :aria-pressed="statusFilter === 'online'" @click="statusFilter = 'online'">在线</button>
      <button :aria-pressed="statusFilter === 'attention'" @click="statusFilter = 'attention'">待关注</button>
    </div>
    <span class="lbl-cn">{{ filtered.length }} / {{ nodes.length }} 个真实节点</span>
  </div>

  <div v-if="loading" class="loading-lines"><div v-for="index in 8" :key="index" class="sk sk-line"></div></div>
  <section v-else-if="error" class="state-wrap"><div class="st" role="alert"><b>节点加载失败</b><span>{{ error }}</span><button class="b b-am" @click="load">重试</button></div></section>
  <section v-else-if="!filtered.length" class="state-wrap"><div class="st"><b>没有匹配的节点</b><span>调整搜索词或筛选条件后再试。</span></div></section>

  <div v-else class="entity-grid node-table">
    <div class="table-head">
      <button class="table-head-cell" type="button" :aria-sort="sortKey === 'name' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'" @click="toggleSort('name')">节点 <span>{{ sortArrow('name') }}</span></button>
      <button class="table-head-cell" type="button" :aria-sort="sortKey === 'protocol' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'" @click="toggleSort('protocol')">协议 / 入口 <span>{{ sortArrow('protocol') }}</span></button>
      <button class="table-head-cell" type="button" :aria-sort="sortKey === 'traffic' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'" @click="toggleSort('traffic')">24h 流量 <span>{{ sortArrow('traffic') }}</span></button>
      <button class="table-head-cell" type="button" :aria-sort="sortKey === 'status' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'" @click="toggleSort('status')">状态 <span>{{ sortArrow('status') }}</span></button>
      <span class="table-head-label">操作</span>
    </div>
    <article v-for="node in sortedNodes" :key="node.id" class="entity table-row" :class="{ 'is-muted': !node.enabled }">
      <div class="table-cell table-main">
        <span class="status-dot" :class="statusTone(node)"></span>
        <div class="table-main-copy"><h3>{{ node.name }}</h3><span>ID {{ node.id }} · 已分配 {{ node.allocated_to || 0 }} 人</span></div>
      </div>
      <div class="table-cell"><strong>{{ node.protocol }}</strong><span>{{ node.server }}:{{ node.port }}</span></div>
      <div class="table-cell table-flow"><strong>{{ formatBytes(node.traffic?.['24h']?.total || 0) }}</strong><div class="bar table-bar"><i :style="{ '--meter': `${Math.max(2, Math.round((Number(node.traffic?.['24h']?.total || 0) / maxTraffic) * 100))}%` }"></i></div></div>
      <div class="table-cell"><strong class="table-state" :class="statusTone(node)">{{ nodeStatus(node).text }}</strong><span>{{ node.collector?.last_active ? timeAgo(node.collector.last_active) : "尚未上报" }}</span></div>
      <div class="table-actions"><button class="b b-am" @click="openEdit(node)">查看与编辑</button></div>
    </article>
  </div>

  <Modal v-if="showBulkModal" title="批量添加节点" @close="showBulkModal = false">
    <p v-if="bulkError" class="form-error" role="alert">{{ bulkError }}</p>
    <div class="form-row"><label for="bulk-uri">节点链接（每行一条）</label><textarea id="bulk-uri" v-model="bulkText" placeholder="vless://…&#10;hysteria2://…"></textarea></div>
    <div class="form-row"><label for="bulk-name">名称（只粘贴一条时可选）</label><input id="bulk-name" v-model="bulkName" placeholder="留空则使用链接内名称"></div>
    <div class="modal-foot"><button class="b" @click="showBulkModal = false">取消</button><button class="b b-am" :disabled="bulkBusy" @click="submitBulk">{{ bulkBusy ? "创建中…" : "创建" }}</button></div>
  </Modal>

  <Modal v-if="editing" :title="editing.name" @close="editing = null">
    <p v-if="editError" class="form-error" role="alert">{{ editError }}</p>
    <dl class="detail-list">
      <div class="detail-item"><dt>节点 ID</dt><dd class="mono">{{ editing.id }}</dd></div>
      <div class="detail-item"><dt>协议 / 入口</dt><dd class="mono">{{ editing.protocol.toUpperCase() }} · {{ editing.server }}:{{ editing.port }}</dd></div>
      <div class="detail-item"><dt>24h / 7d / 30d</dt><dd class="mono">{{ formatBytes(editing.traffic?.['24h']?.total) }} · {{ formatBytes(editing.traffic?.['7d']?.total) }} · {{ formatBytes(editing.traffic?.['30d']?.total) }}</dd></div>
      <div class="detail-item"><dt>用户级适配器</dt><dd>{{ adapterText(editing) }}</dd></div>
    </dl>
    <div class="form-row"><label for="node-name">名称</label><input id="node-name" v-model="editForm.name"></div>
    <div class="check"><input id="node-enabled" v-model="editForm.enabled" type="checkbox"><label for="node-enabled">启用节点</label></div>
    <div class="form-row"><label for="nezha-id">哪吒 Server ID（留空表示未关联）</label><input id="nezha-id" v-model="editForm.nezha_server_id" type="number" placeholder="未关联"></div>
    <div class="check"><input id="node-peruser" v-model="editForm.per_user_enabled" type="checkbox" :disabled="!editing.per_user_capability?.ready"><label for="node-peruser">启用用户级独立凭据{{ editing.per_user_capability?.ready ? "" : "（适配器无）" }}</label></div>
    <div class="form-row"><label for="node-uri">更新节点链接（留空不改）</label><input id="node-uri" v-model="editForm.uri" autocomplete="off" placeholder="vless://… / hysteria2://…"></div>
    <div class="modal-foot between">
      <div class="modal-foot-group"><button class="b b-bad" :disabled="editBusy" @click="removeNode">删除节点</button><button v-if="['vless','hysteria2'].includes(editing.protocol)" class="b" :disabled="preparing" @click="prepareCredentials">{{ preparing ? "同步中…" : "同步独立凭据" }}</button></div>
      <div class="modal-foot-group"><button class="b" @click="editing = null">取消</button><button class="b b-am" :disabled="editBusy" @click="submitEdit">{{ editBusy ? "保存中…" : "保存" }}</button></div>
    </div>
  </Modal>
</template>

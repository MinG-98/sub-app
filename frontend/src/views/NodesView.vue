<script setup>
import { ref, computed, onMounted } from "vue";
import { api } from "../api.js";
import { pushToast } from "../store.js";
import { formatBytes } from "../format.js";
import Modal from "../components/Modal.vue";

const loading = ref(true);
const nodes = ref([]);
const search = ref("");

const showBulkModal = ref(false);
const bulkText = ref("");
const bulkName = ref("");
const bulkBusy = ref(false);
const bulkError = ref("");

const editing = ref(null); // node object being edited, or null
const editForm = ref({ name: "", enabled: true, nezha_server_id: "", per_user_enabled: false, uri: "" });
const editBusy = ref(false);
const editError = ref("");
const preparing = ref(false);

async function load() {
  loading.value = true;
  try {
    nodes.value = await api.nodes();
  } catch (e) {
    pushToast(e.message || "加载节点失败");
  } finally {
    loading.value = false;
  }
}
onMounted(load);

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase();
  if (!q) return nodes.value;
  return nodes.value.filter(
    (n) => n.name.toLowerCase().includes(q) || n.protocol.toLowerCase().includes(q) || (n.server || "").toLowerCase().includes(q)
  );
});

function nodeStatus(node) {
  if (!node.enabled) return { dot: "off", cls: "off", text: "已停用" };
  if (!node.collector || !node.collector.mapped) return { dot: "unknown", cls: "unknown", text: "未接入监控 · 状态未知" };
  if (node.collector.online) return { dot: "online", cls: "online", text: "在线 · 已接入哪吒监控" };
  return { dot: "warn", cls: "warn", text: "已接入监控 · 当前离线" };
}

async function submitBulk() {
  if (!bulkText.value.trim()) {
    bulkError.value = "请输入至少一条节点链接";
    return;
  }
  bulkBusy.value = true;
  bulkError.value = "";
  try {
    const res = await api.createNodes({ bulk: bulkText.value, name: bulkName.value || undefined });
    pushToast(`已创建 ${res.created.length} 个节点${res.skipped.length ? `，跳过 ${res.skipped.length} 条` : ""}`, "success");
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
    };
    payload.nezha_server_id = editForm.value.nezha_server_id === "" ? null : Number(editForm.value.nezha_server_id);
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
  if (!editing.value) return;
  preparing.value = true;
  try {
    const res = await api.prepareNodeCredentials(editing.value.id);
    pushToast(res.activated ? "已为已启用用户激活独立凭据" : "已尝试同步，部分凭据仍待处理", res.activated ? "success" : "error");
  } catch (e) {
    pushToast(e.message || "同步凭据失败");
  } finally {
    preparing.value = false;
  }
}

async function removeNode() {
  if (!editing.value) return;
  if (!window.confirm(`确定删除节点「${editing.value.name}」？此操作不可撤销。`)) return;
  editBusy.value = true;
  try {
    await api.deleteNode(editing.value.id);
    pushToast("节点已删除", "success");
    editing.value = null;
    await load();
  } catch (e) {
    pushToast(e.message || "删除失败");
  } finally {
    editBusy.value = false;
  }
}
</script>

<template>
  <section>
    <div class="hero">
      <div>
        <div class="eyebrow">Node Inventory</div>
        <h1 class="sm">节点管理</h1>
        <p class="lede">查看哪吒状态、流量、用户级适配器和历史趋势。</p>
      </div>
      <div class="hero-actions">
        <input class="search-input" v-model="search" placeholder="搜索节点、协议或服务器" />
        <button class="btn primary" @click="showBulkModal = true">批量添加</button>
      </div>
    </div>

    <section v-if="loading" class="panel-empty">加载中…</section>
    <section v-else-if="!filtered.length" class="panel-empty">没有匹配的节点</section>
    <div v-else class="nodes" style="grid-template-columns:repeat(2,1fr);">
      <div v-for="n in filtered" :key="n.id" class="node-card" :style="!n.enabled ? 'opacity:0.6' : ''">
        <div class="node-top">
          <div class="node-id"><span class="dot" :class="nodeStatus(n).dot"></span><div><div class="node-name">{{ n.name }}</div><div class="node-server">{{ n.server }} · {{ n.port }}</div></div></div>
          <span class="proto-tag">{{ n.protocol }}</span>
        </div>
        <div class="node-status-line" :class="'is-' + nodeStatus(n).cls">{{ nodeStatus(n).text }}</div>
        <div class="node-stats">
          <div><div class="node-stat-label">24H 流量</div><div class="node-stat-value" v-html="formatBytes(n.traffic['24h'].total)"></div></div>
          <div><div class="node-stat-label">已分配</div><div class="node-stat-value">{{ n.allocated_to }} 人</div></div>
        </div>
        <div class="node-foot">
          <button class="btn" @click="openEdit(n)">查看详情</button>
          <button class="btn" @click="openEdit(n)">编辑</button>
        </div>
      </div>
    </div>

    <Modal v-if="showBulkModal" title="批量添加节点" @close="showBulkModal = false">
      <p v-if="bulkError" class="form-error">{{ bulkError }}</p>
      <div class="form-row">
        <label>节点链接（每行一条，支持 vless / hysteria2 / vmess / trojan 等 URI）</label>
        <textarea v-model="bulkText" placeholder="vless://...&#10;hysteria2://..."></textarea>
      </div>
      <div class="form-row">
        <label>名称（可选，留空则使用链接自带名称）</label>
        <input type="text" v-model="bulkName" placeholder="仅在只粘贴一条链接时生效" />
      </div>
      <div class="modal-foot">
        <button class="btn" @click="showBulkModal = false">取消</button>
        <button class="btn primary" :disabled="bulkBusy" @click="submitBulk">{{ bulkBusy ? "创建中…" : "创建" }}</button>
      </div>
    </Modal>

    <Modal v-if="editing" :title="editing.name" @close="editing = null">
      <p v-if="editError" class="form-error">{{ editError }}</p>
      <div class="form-row">
        <label>名称</label>
        <input type="text" v-model="editForm.name" />
      </div>
      <div class="form-row checkbox-row">
        <input id="node-enabled" type="checkbox" v-model="editForm.enabled" />
        <label for="node-enabled" style="margin:0;">启用节点</label>
      </div>
      <div class="form-row">
        <label>哪吒 Server ID（用于状态采集，留空表示未接入）</label>
        <input type="number" v-model="editForm.nezha_server_id" placeholder="留空 = 不接入监控" />
      </div>
      <div class="form-row checkbox-row">
        <input id="node-peruser" type="checkbox" v-model="editForm.per_user_enabled" :disabled="!editing.per_user_capability?.ready" />
        <label for="node-peruser" style="margin:0;">启用用户级独立凭据{{ editing.per_user_capability?.ready ? "" : "（适配器未就绪）" }}</label>
      </div>
      <div class="form-row">
        <label>更新节点链接（可选，留空则不改）</label>
        <input type="text" v-model="editForm.uri" placeholder="vless://... / hysteria2://..." />
      </div>
      <div class="form-row">
        <label>流量统计</label>
        <div class="node-stats">
          <div><div class="node-stat-label">24H</div><div class="node-stat-value" v-html="formatBytes(editing.traffic['24h'].total)"></div></div>
          <div><div class="node-stat-label">7D</div><div class="node-stat-value" v-html="formatBytes(editing.traffic['7d'].total)"></div></div>
          <div><div class="node-stat-label">30D</div><div class="node-stat-value" v-html="formatBytes(editing.traffic['30d'].total)"></div></div>
          <div><div class="node-stat-label">已分配</div><div class="node-stat-value">{{ editing.allocated_to }} 人</div></div>
        </div>
      </div>
      <div class="modal-foot" style="justify-content:space-between;">
        <div>
          <button v-if="['vless','hysteria2'].includes(editing.protocol)" class="btn sm" :disabled="preparing" @click="prepareCredentials">
            {{ preparing ? "同步中…" : "同步独立凭据" }}
          </button>
          <button class="btn sm danger" :disabled="editBusy" @click="removeNode">删除节点</button>
        </div>
        <button class="btn primary" :disabled="editBusy" @click="submitEdit">{{ editBusy ? "保存中…" : "保存" }}</button>
      </div>
    </Modal>
  </section>
</template>

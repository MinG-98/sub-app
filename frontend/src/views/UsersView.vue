<script setup>
import { ref, computed, onMounted } from "vue";
import { api } from "../api.js";
import { pushToast } from "../store.js";
import { formatBytes } from "../format.js";
import Modal from "../components/Modal.vue";

const loading = ref(true);
const friends = ref([]);
const nodes = ref([]);
const search = ref("");

const nodeName = (id) => nodes.value.find((n) => n.id === id)?.name || `#${id}`;

async function load() {
  loading.value = true;
  try {
    const [f, n] = await Promise.all([api.friends(), api.nodes()]);
    friends.value = f;
    nodes.value = n;
  } catch (e) {
    pushToast(e.message || "加载用户失败");
  } finally {
    loading.value = false;
  }
}
onMounted(load);

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase();
  if (!q) return friends.value;
  return friends.value.filter((f) => f.uid.toLowerCase().includes(q) || (f.remark || "").toLowerCase().includes(q));
});

function authChip(f) {
  if (!f.per_user_credentials) return { cls: "slate", text: "共享凭据" };
  const rows = f.credential_status || [];
  if (!rows.length) return { cls: "slate", text: "专属 · 待同步" };
  if (rows.some((r) => r.status === "active")) return { cls: "ok", text: "专属 · 生效中" };
  if (rows.some((r) => r.status === "grace")) return { cls: "warn", text: "专属 · 宽限期" };
  if (rows.some((r) => r.status === "error")) return { cls: "critical", text: "专属 · 同步失败" };
  return { cls: "slate", text: "专属 · 待同步" };
}

function quotaCls(f) {
  if (f.flow_alert === "over") return "var(--critical)";
  if (f.flow_alert === "warning") return "var(--warn)";
  return "var(--accent)";
}

async function copyLink(f, target) {
  const url = f.links[target];
  try {
    await navigator.clipboard.writeText(url);
    pushToast(`已复制 ${target === "clash" ? "Clash" : "V2Ray"} 订阅链接`, "success");
  } catch {
    pushToast(url, "success");
  }
}

// ---- create/edit modal ----
const showCreate = ref(false);
const editing = ref(null); // friend or null when creating
const form = ref(emptyForm());
const busy = ref(false);
const error = ref("");
const credentials = ref([]);
const credBusy = ref({});

function emptyForm() {
  return { uid: "", remark: "", flow_limit_gb: 0, device_limit: 0, per_user_credentials: false, node_ids: [] };
}

function openCreate() {
  editing.value = null;
  form.value = emptyForm();
  error.value = "";
  credentials.value = [];
  showCreate.value = true;
}

function openEdit(f) {
  editing.value = f;
  form.value = {
    uid: f.uid,
    remark: f.remark || "",
    flow_limit_gb: f.flow_limit_gb,
    device_limit: f.device_limit,
    per_user_credentials: f.per_user_credentials,
    node_ids: [...f.node_ids],
    rotate_token: false,
  };
  error.value = "";
  credentials.value = f.credential_status || [];
  showCreate.value = true;
}

async function submit() {
  busy.value = true;
  error.value = "";
  try {
    if (editing.value) {
      const payload = {
        remark: form.value.remark,
        flow_limit_gb: Number(form.value.flow_limit_gb) || 0,
        device_limit: Number(form.value.device_limit) || 0,
        per_user_credentials: form.value.per_user_credentials,
        node_ids: form.value.node_ids,
      };
      if (form.value.rotate_token) payload.rotate_token = true;
      await api.updateFriend(editing.value.id, payload);
      pushToast("用户已更新", "success");
    } else {
      if (!form.value.uid.trim()) {
        error.value = "UID 不能为空";
        busy.value = false;
        return;
      }
      await api.createFriend({
        uid: form.value.uid.trim(),
        remark: form.value.remark,
        flow_limit_gb: Number(form.value.flow_limit_gb) || 0,
        device_limit: Number(form.value.device_limit) || 0,
        per_user_credentials: form.value.per_user_credentials,
        node_ids: form.value.node_ids,
      });
      pushToast("用户已创建", "success");
    }
    showCreate.value = false;
    await load();
  } catch (e) {
    error.value = e.message || "保存失败";
  } finally {
    busy.value = false;
  }
}

async function toggleEnabled(f) {
  try {
    await api.updateFriend(f.id, { enabled: !f.enabled });
    pushToast(f.enabled ? "用户已停用" : "用户已启用", "success");
    await load();
  } catch (e) {
    pushToast(e.message || "操作失败");
  }
}

async function removeFriend(f) {
  if (!window.confirm(`确定删除用户「${f.uid}」？此操作不可撤销。`)) return;
  try {
    await api.deleteFriend(f.id);
    pushToast("用户已删除", "success");
    await load();
  } catch (e) {
    pushToast(e.message || "删除失败");
  }
}

async function credAction(row, action) {
  credBusy.value = { ...credBusy.value, [row.id]: true };
  try {
    const fn = { sync: api.syncCredential, rotate: api.rotateCredential, revoke: api.revokeCredential }[action];
    await fn(row.id);
    pushToast("凭据操作已完成", "success");
    if (editing.value) credentials.value = await api.friendCredentials(editing.value.id);
  } catch (e) {
    pushToast(e.message || "凭据操作失败");
  } finally {
    const next = { ...credBusy.value };
    delete next[row.id];
    credBusy.value = next;
  }
}
</script>

<template>
  <section>
    <div class="hero">
      <div>
        <div class="eyebrow">Subscribers</div>
        <h1 class="sm">用户管理</h1>
        <p class="lede">用户级凭据、配额状态和订阅入口集中管理。</p>
      </div>
      <div class="hero-actions">
        <input class="search-input" v-model="search" placeholder="搜索 UID 或备注" />
        <button class="btn primary" @click="openCreate">+ 新建用户</button>
      </div>
    </div>

    <section v-if="loading" class="panel-empty">加载中…</section>
    <div v-else class="panel">
      <div v-if="!filtered.length" class="panel-empty">没有匹配的用户</div>
      <div v-else class="table-wrap">
        <table>
          <thead><tr><th>用户</th><th>节点</th><th>认证</th><th>流量配额</th><th>设备</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="f in filtered" :key="f.id">
              <td><div class="cell-title">{{ f.uid }}</div><div class="cell-sub">{{ f.remark || "—" }}</div></td>
              <td class="mono">{{ f.node_ids.length }} 个节点</td>
              <td><span class="chip" :class="authChip(f).cls">{{ authChip(f).text }}</span></td>
              <td>
                <div class="quota-text mono" v-html="formatBytes(f.flow_used_bytes) + ' / ' + (f.flow_limit_bytes ? formatBytes(f.flow_limit_bytes) : '<span style=\'color:var(--text-faint)\'>不限</span>')"></div>
                <div v-if="f.flow_limit_bytes" class="quota-bar"><div class="quota-fill" :style="{ width: Math.min(100, f.flow_percent) + '%', background: quotaCls(f) }"></div></div>
              </td>
              <td class="mono">{{ f.device_count }}{{ f.device_limit ? ` / ${f.device_limit}` : "" }}</td>
              <td><span class="chip" :class="f.enabled ? 'ok' : 'slate'">{{ f.enabled ? "启用" : "停用" }}</span></td>
              <td>
                <div class="row-actions">
                  <button @click="openEdit(f)">编辑</button>
                  <button @click="copyLink(f, 'clash')">复制 Clash</button>
                  <button @click="copyLink(f, 'v2ray')">复制 V2Ray</button>
                  <button @click="toggleEnabled(f)">{{ f.enabled ? "停用" : "启用" }}</button>
                  <button class="danger" @click="removeFriend(f)">删除</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <Modal v-if="showCreate" :title="editing ? `编辑用户 · ${editing.uid}` : '新建用户'" @close="showCreate = false">
      <p v-if="error" class="form-error">{{ error }}</p>
      <div class="form-row" v-if="!editing">
        <label>UID</label>
        <input type="text" v-model="form.uid" placeholder="唯一标识，如 alice" />
      </div>
      <div class="form-row">
        <label>备注</label>
        <input type="text" v-model="form.remark" placeholder="可选" />
      </div>
      <div class="form-row">
        <label>流量配额（GB，0 表示不限）</label>
        <input type="number" min="0" v-model="form.flow_limit_gb" />
      </div>
      <div class="form-row">
        <label>设备上限（0 表示不限）</label>
        <input type="number" min="0" v-model="form.device_limit" />
      </div>
      <div class="form-row checkbox-row">
        <input id="peruser" type="checkbox" v-model="form.per_user_credentials" />
        <label for="peruser" style="margin:0;">启用用户级独立凭据（支持的节点会为该用户单独签发凭据）</label>
      </div>
      <div class="form-row">
        <label>分配节点</label>
        <div class="checkbox-grid">
          <label v-for="n in nodes" :key="n.id">
            <input type="checkbox" :value="n.id" v-model="form.node_ids" />
            {{ n.name }} <span class="mono" style="color:var(--text-faint)">· {{ n.protocol }}</span>
          </label>
          <div v-if="!nodes.length" style="color:var(--text-faint);font-size:12px;">暂无可分配节点</div>
        </div>
      </div>
      <div class="form-row checkbox-row" v-if="editing">
        <input id="rotate" type="checkbox" v-model="form.rotate_token" />
        <label for="rotate" style="margin:0;">重置订阅 Token（旧订阅链接将失效）</label>
      </div>

      <div v-if="editing && credentials.length" class="form-row">
        <label>独立凭据状态</label>
        <div class="checkbox-grid" style="max-height:180px;">
          <div v-for="row in credentials" :key="row.id" class="credential-row">
            <span>{{ nodeName(row.node_id) }} <span class="mono" style="color:var(--text-faint)">v{{ row.version }}</span></span>
            <span class="chip" :class="{ active: 'ok', grace: 'warn', pending: 'slate', revoked: 'slate', error: 'critical' }[row.status] || 'slate'">{{ row.status }}</span>
            <div class="row-actions">
              <button :disabled="credBusy[row.id]" @click="credAction(row, 'sync')">同步</button>
              <button :disabled="credBusy[row.id]" @click="credAction(row, 'rotate')">轮换</button>
              <button class="danger" :disabled="credBusy[row.id]" @click="credAction(row, 'revoke')">吊销</button>
            </div>
          </div>
        </div>
      </div>

      <div class="modal-foot">
        <button class="btn" @click="showCreate = false">取消</button>
        <button class="btn primary" :disabled="busy" @click="submit">{{ busy ? "保存中…" : "保存" }}</button>
      </div>
    </Modal>
  </section>
</template>

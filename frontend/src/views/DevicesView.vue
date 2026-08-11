<script setup>
import { ref, computed, onMounted } from "vue";
import { api } from "../api.js";
import { pushToast } from "../store.js";
import { timeAgo } from "../format.js";
import Modal from "../components/Modal.vue";

const loading = ref(true);
const devices = ref([]);
const friends = ref([]);
const filter = ref("all");

const ATTENTION_THRESHOLD = 500;

async function load() {
  loading.value = true;
  try {
    const [d, f] = await Promise.all([api.devices(), api.friends()]);
    devices.value = d;
    friends.value = f;
  } catch (e) {
    pushToast(e.message || "加载设备失败");
  } finally {
    loading.value = false;
  }
}
onMounted(load);

const filtered = computed(() => {
  if (filter.value === "attention") return devices.value.filter((d) => !d.blocked && d.fetch_count > ATTENTION_THRESHOLD);
  if (filter.value === "blocked") return devices.value.filter((d) => d.blocked);
  return devices.value;
});

function identityChip(d) {
  if (d.device_link_active) return { cls: "accent", text: "设备令牌" };
  if (d.identity_source === "device_link") return { cls: "critical", text: "令牌已撤销" };
  return { cls: "slate", text: "UA+IP 指纹" };
}

function statusChip(d) {
  if (d.blocked) return { cls: "critical", text: "已封锁" };
  if (d.fetch_count > ATTENTION_THRESHOLD) return { cls: "warn", text: "拉取异常频繁" };
  return { cls: "ok", text: "正常" };
}

// ---- create device link ----
const showCreate = ref(false);
const createFriendId = ref("");
const createLabel = ref("");
const createBusy = ref(false);
const createError = ref("");
const createdLinks = ref(null);

function openCreate() {
  createFriendId.value = friends.value[0]?.id || "";
  createLabel.value = "";
  createError.value = "";
  createdLinks.value = null;
  showCreate.value = true;
}

async function submitCreate() {
  if (!createFriendId.value) {
    createError.value = "请选择用户";
    return;
  }
  createBusy.value = true;
  createError.value = "";
  try {
    const res = await api.createDeviceLink(createFriendId.value, createLabel.value);
    createdLinks.value = res.links;
    pushToast("设备链接已创建", "success");
    await load();
  } catch (e) {
    createError.value = e.message || "创建失败";
  } finally {
    createBusy.value = false;
  }
}

// ---- detail modal ----
const detail = ref(null);
const detailForm = ref({ label: "", blocked: false });
const detailBusy = ref(false);
const detailLinks = ref(null);

function openDetail(d) {
  detail.value = d;
  detailForm.value = { label: d.label || "", blocked: d.blocked };
  detailLinks.value = null;
}

async function saveDetail() {
  detailBusy.value = true;
  try {
    await api.updateDevice(detail.value.id, { label: detailForm.value.label, blocked: detailForm.value.blocked });
    pushToast("设备已更新", "success");
    detail.value = null;
    await load();
  } catch (e) {
    pushToast(e.message || "更新失败");
  } finally {
    detailBusy.value = false;
  }
}

async function rotateLink() {
  detailBusy.value = true;
  try {
    const res = await api.rotateDeviceLink(detail.value.id);
    detailLinks.value = res.links;
    pushToast("链接已轮换，旧链接失效", "success");
    await load();
  } catch (e) {
    pushToast(e.message || "轮换失败");
  } finally {
    detailBusy.value = false;
  }
}

async function revokeLink() {
  if (!window.confirm("确定撤销该设备的链接？")) return;
  detailBusy.value = true;
  try {
    await api.revokeDeviceLink(detail.value.id);
    pushToast("链接已撤销", "success");
    detail.value = null;
    await load();
  } catch (e) {
    pushToast(e.message || "撤销失败");
  } finally {
    detailBusy.value = false;
  }
}

async function removeDevice() {
  if (!window.confirm(`确定删除设备「${detail.value.label || detail.value.fingerprint}」？`)) return;
  detailBusy.value = true;
  try {
    await api.deleteDevice(detail.value.id);
    pushToast("设备已删除", "success");
    detail.value = null;
    await load();
  } catch (e) {
    pushToast(e.message || "删除失败");
  } finally {
    detailBusy.value = false;
  }
}
</script>

<template>
  <section>
    <div class="hero">
      <div>
        <div class="eyebrow">Device Audit</div>
        <h1 class="sm">设备管理</h1>
        <p class="lede">识别指纹、拉取行为与异常设备排查。</p>
      </div>
      <div class="hero-actions">
        <div class="filter-chips">
          <button class="filter-chip" :class="{ active: filter === 'all' }" @click="filter = 'all'">全部</button>
          <button class="filter-chip" :class="{ active: filter === 'attention' }" @click="filter = 'attention'">需要关注</button>
          <button class="filter-chip" :class="{ active: filter === 'blocked' }" @click="filter = 'blocked'">已封锁</button>
        </div>
        <button class="btn primary" @click="openCreate">+ 创建设备链接</button>
      </div>
    </div>

    <section v-if="loading" class="panel-empty">加载中…</section>
    <div v-else class="panel">
      <div v-if="!filtered.length" class="panel-empty">没有匹配的设备</div>
      <div v-else class="table-wrap">
        <table>
          <thead><tr><th>设备</th><th>归属用户</th><th>识别方式</th><th>最近 IP</th><th>User-Agent</th><th>拉取次数</th><th>最近活跃</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="d in filtered" :key="d.id">
              <td><div class="cell-title">{{ d.label || `设备 #${d.id}` }}</div></td>
              <td class="mono">{{ d.friend_uid }}</td>
              <td><span class="chip" :class="identityChip(d).cls">{{ identityChip(d).text }}</span></td>
              <td class="mono">{{ d.last_ip || "—" }}</td>
              <td><div class="cell-sub" style="margin-top:0;max-width:160px;">{{ d.user_agent || "—" }}</div></td>
              <td class="mono" :style="d.fetch_count > ATTENTION_THRESHOLD ? 'color:var(--warn)' : ''">{{ d.fetch_count }}</td>
              <td class="mono">{{ timeAgo(d.last_seen) }}</td>
              <td><span class="chip" :class="statusChip(d).cls">{{ statusChip(d).text }}</span></td>
              <td>
                <div class="row-actions">
                  <button @click="openDetail(d)">详情</button>
                  <button class="danger" v-if="!d.blocked" @click="detail = d; detailForm = { label: d.label || '', blocked: true }; saveDetail()">封锁</button>
                  <button v-else @click="detail = d; detailForm = { label: d.label || '', blocked: false }; saveDetail()">解封</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <Modal v-if="showCreate" title="创建设备链接" @close="showCreate = false">
      <template v-if="createdLinks">
        <p class="form-hint">链接只在本次显示一次，请立即复制发给用户；数据库仅保存哈希。</p>
        <div class="form-row"><label>Clash</label><div class="link-box">{{ createdLinks.clash }}</div></div>
        <div class="form-row"><label>V2Ray</label><div class="link-box">{{ createdLinks.v2ray }}</div></div>
        <div class="modal-foot"><button class="btn primary" @click="showCreate = false">完成</button></div>
      </template>
      <template v-else>
        <p v-if="createError" class="form-error">{{ createError }}</p>
        <div class="form-row">
          <label>归属用户</label>
          <select v-model="createFriendId">
            <option v-for="f in friends" :key="f.id" :value="f.id">{{ f.uid }}</option>
          </select>
        </div>
        <div class="form-row">
          <label>设备标签（可选）</label>
          <input type="text" v-model="createLabel" placeholder="如 iPhone 15" />
        </div>
        <div class="modal-foot">
          <button class="btn" @click="showCreate = false">取消</button>
          <button class="btn primary" :disabled="createBusy" @click="submitCreate">{{ createBusy ? "创建中…" : "创建" }}</button>
        </div>
      </template>
    </Modal>

    <Modal v-if="detail" :title="detail.label || `设备 #${detail.id}`" @close="detail = null">
      <template v-if="detailLinks">
        <p class="form-hint">新链接只显示一次，旧链接已失效。</p>
        <div class="form-row"><label>Clash</label><div class="link-box">{{ detailLinks.clash }}</div></div>
        <div class="form-row"><label>V2Ray</label><div class="link-box">{{ detailLinks.v2ray }}</div></div>
        <div class="modal-foot"><button class="btn primary" @click="detail = null">完成</button></div>
      </template>
      <template v-else>
        <div class="form-row">
          <label>归属用户</label>
          <div class="mono">{{ detail.friend_uid }}</div>
        </div>
        <div class="form-row">
          <label>标签</label>
          <input type="text" v-model="detailForm.label" />
        </div>
        <div class="form-row checkbox-row">
          <input id="dev-blocked" type="checkbox" v-model="detailForm.blocked" />
          <label for="dev-blocked" style="margin:0;">封锁该设备</label>
        </div>
        <div class="form-row">
          <label>识别方式</label>
          <div><span class="chip" :class="identityChip(detail).cls">{{ identityChip(detail).text }}</span></div>
        </div>
        <div class="form-row">
          <label>最近活跃 / 拉取次数</label>
          <div class="mono">{{ timeAgo(detail.last_seen) }} · {{ detail.fetch_count }} 次</div>
        </div>
        <div class="modal-foot" style="justify-content:space-between;">
          <div>
            <button v-if="detail.identity_source === 'device_link'" class="btn sm" :disabled="detailBusy" @click="rotateLink">轮换链接</button>
            <button v-if="detail.device_link_active" class="btn sm" :disabled="detailBusy" @click="revokeLink">撤销链接</button>
            <button class="btn sm danger" :disabled="detailBusy" @click="removeDevice">删除设备</button>
          </div>
          <button class="btn primary" :disabled="detailBusy" @click="saveDetail">保存</button>
        </div>
      </template>
    </Modal>
  </section>
</template>

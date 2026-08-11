<script setup>
import { ref, onMounted, onUnmounted, computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api.js";
import { auth } from "../store.js";

const route = useRoute();
const router = useRouter();

const navItems = [
  { name: "overview", label: "概览", icon: "overview" },
  { name: "nodes", label: "节点", icon: "nodes" },
  { name: "users", label: "用户", icon: "users" },
  { name: "devices", label: "设备", icon: "devices" },
];
const labels = { overview: "概览", nodes: "节点", users: "用户", devices: "设备" };
const current = computed(() => labels[route.name] || "");

const nodesOnline = ref(0);
const nodesTotal = ref(0);
const lastSync = ref("");
let timer = null;

async function refreshSignal() {
  try {
    const nodes = await api.nodes();
    nodesTotal.value = nodes.length;
    nodesOnline.value = nodes.filter((n) => n.collector && n.collector.online).length;
    lastSync.value = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  } catch {
    // signal chip is best-effort; ignore transient failures
  }
}

onMounted(() => {
  refreshSignal();
  timer = setInterval(refreshSignal, 30000);
});
onUnmounted(() => {
  if (timer) clearInterval(timer);
});

async function logout() {
  try {
    await api.logout();
  } finally {
    auth.authenticated = false;
    router.push({ name: "login" });
  }
}

const nowLabel = computed(() => {
  const d = new Date();
  return d.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
});
</script>

<template>
  <div class="shell">
    <aside class="rail">
      <div class="brand">
        <div class="brand-mark">
          <svg viewBox="0 0 24 24" fill="none" stroke="#56d2c4" stroke-width="1.6"><path d="M4 12l5-7 5 7-5 7-5-7z"/><path d="M14 12l3-4.5 3 4.5-3 4.5-3-4.5z" opacity="0.55"/></svg>
        </div>
        <div>
          <div class="brand-name">SUB APP</div>
          <div class="brand-sub">Control Center</div>
        </div>
      </div>
      <nav class="nav">
        <router-link
          v-for="item in navItems"
          :key="item.name"
          :to="{ name: item.name }"
          class="nav-item"
          :class="{ active: route.name === item.name }"
        >
          <svg v-if="item.icon === 'overview'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2.4" fill="currentColor" stroke="none"/></svg>
          <svg v-else-if="item.icon === 'nodes'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3.5" y="4" width="17" height="7" rx="1.6"/><rect x="3.5" y="13" width="17" height="7" rx="1.6"/><circle cx="7" cy="7.5" r="0.8" fill="currentColor" stroke="none"/><circle cx="7" cy="16.5" r="0.8" fill="currentColor" stroke="none"/></svg>
          <svg v-else-if="item.icon === 'users'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="8.2" r="3.4"/><path d="M4.8 20c0-3.9 3.2-6.2 7.2-6.2s7.2 2.3 7.2 6.2"/></svg>
          <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="5" y="3.5" width="14" height="17" rx="2"/><line x1="9.5" y1="18" x2="14.5" y2="18"/></svg>
          {{ item.label }}
        </router-link>
      </nav>
      <div class="rail-foot">
        <div class="signal-chip">
          <span class="signal-dot" :class="{ off: nodesOnline === 0 }"></span>
          <span class="signal-text"><b>{{ nodesOnline }} / {{ nodesTotal }}</b> 节点在线</span>
        </div>
        <div class="rail-meta">LAST SYNC · {{ lastSync || "—" }}</div>
        <button class="rail-exit" @click="logout">退出登录</button>
      </div>
    </aside>

    <main class="main">
      <div class="topline">
        <span class="path">CONTROL CENTER <span style="color:var(--text-faint)">/</span> <b>{{ current }}</b></span>
        <span>{{ nowLabel }}</span>
      </div>
      <slot />
    </main>

    <nav class="mnav">
      <router-link
        v-for="item in navItems"
        :key="item.name"
        :to="{ name: item.name }"
        class="mnav-item"
        :class="{ active: route.name === item.name }"
      >
        <svg v-if="item.icon === 'overview'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2.4" fill="currentColor" stroke="none"/></svg>
        <svg v-else-if="item.icon === 'nodes'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3.5" y="4" width="17" height="7" rx="1.6"/><rect x="3.5" y="13" width="17" height="7" rx="1.6"/></svg>
        <svg v-else-if="item.icon === 'users'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="8.2" r="3.4"/><path d="M4.8 20c0-3.9 3.2-6.2 7.2-6.2s7.2 2.3 7.2 6.2"/></svg>
        <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="5" y="3.5" width="14" height="17" rx="2"/><line x1="9.5" y1="18" x2="14.5" y2="18"/></svg>
        {{ item.label }}
      </router-link>
    </nav>
  </div>
</template>

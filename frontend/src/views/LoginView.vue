<script setup>
import { ref } from "vue";
import { useRouter, useRoute } from "vue-router";
import { api } from "../api.js";
import { auth } from "../store.js";

const password = ref("");
const error = ref("");
const submitting = ref(false);
const router = useRouter();
const route = useRoute();

async function submit() {
  if (!password.value) return;
  submitting.value = true;
  error.value = "";
  try {
    await api.login(password.value);
    auth.authenticated = true;
    auth.checked = true;
    const redirect = route.query.redirect;
    router.push(typeof redirect === "string" ? redirect : { name: "overview" });
  } catch (e) {
    error.value = e.message || "登录失败";
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="login-screen">
    <div class="login-card">
      <div class="login-brand">
        <div class="brand-mark">
          <svg viewBox="0 0 24 24" fill="none" stroke="#56d2c4" stroke-width="1.6"><path d="M4 12l5-7 5 7-5 7-5-7z"/><path d="M14 12l3-4.5 3 4.5-3 4.5-3-4.5z" opacity="0.55"/></svg>
        </div>
        <div>
          <div class="brand-name">SUB APP</div>
          <div class="brand-sub">Control Center</div>
        </div>
      </div>
      <div class="login-eyebrow">Operator Access</div>
      <h2 class="login-title">节点订阅管理</h2>
      <p class="login-lede">管理节点、用户、设备与流量状态</p>
      <label class="field-label" for="pw">管理员密码</label>
      <input
        id="pw"
        v-model="password"
        class="field-input"
        type="password"
        placeholder="请输入管理员密码"
        autofocus
        @keydown.enter="submit"
      />
      <p v-if="error" class="login-error">{{ error }}</p>
      <button class="login-submit" :disabled="submitting" @click="submit">
        {{ submitting ? "验证中…" : "进入控制台" }}
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14M13 6l6 6-6 6"/></svg>
      </button>
    </div>
  </div>
</template>

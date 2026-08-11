<script setup>
import { computed } from "vue";
import { useRoute } from "vue-router";
import AppShell from "./components/AppShell.vue";
import ToastHost from "./components/ToastHost.vue";

const route = useRoute();
const isLogin = computed(() => route.meta.public);
</script>

<template>
  <template v-if="isLogin">
    <router-view />
  </template>
  <AppShell v-else>
    <router-view />
  </AppShell>
  <ToastHost />
</template>

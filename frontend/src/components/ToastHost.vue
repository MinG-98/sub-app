<script setup>
import { toasts } from "../store.js";
</script>

<template>
  <div class="toast" v-if="toasts.length">
    <div v-for="t in toasts" :key="t.id" class="toast-item" :class="t.kind">
      {{ t.message }}
    </div>
  </div>
</template>

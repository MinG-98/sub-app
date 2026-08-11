<script setup>
defineProps({ title: { type: String, required: true } });
const emit = defineEmits(["close"]);
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <div class="modal">
      <div class="modal-head">
        <div class="modal-title">{{ title }}</div>
        <button class="modal-close" @click="emit('close')">&times;</button>
      </div>
      <slot />
    </div>
  </div>
</template>

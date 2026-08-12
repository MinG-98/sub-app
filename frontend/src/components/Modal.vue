<script setup>
import { nextTick, onMounted, onUnmounted, ref } from "vue";

defineProps({ title: { type: String, required: true } });
const emit = defineEmits(["close"]);
const panel = ref(null);
let previousOverflow = "";
let previousFocus = null;

function onKeydown(event) {
  if (event.key === "Escape") emit("close");
  if (event.key !== "Tab" || !panel.value) return;
  const focusable = [...panel.value.querySelectorAll('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])')];
  if (!focusable.length) {
    event.preventDefault();
    panel.value.focus();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

onMounted(async () => {
  previousFocus = document.activeElement;
  previousOverflow = document.body.style.overflow;
  document.body.style.overflow = "hidden";
  window.addEventListener("keydown", onKeydown);
  await nextTick();
  panel.value?.focus();
});

onUnmounted(() => {
  document.body.style.overflow = previousOverflow;
  window.removeEventListener("keydown", onKeydown);
  previousFocus?.focus?.();
});
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <section ref="panel" class="modal" role="dialog" aria-modal="true" :aria-label="title" tabindex="-1">
      <header class="modal-head">
        <h2 class="modal-title">{{ title }}</h2>
        <button class="modal-close" aria-label="关闭" @click="emit('close')">×</button>
      </header>
      <div class="modal-body"><slot /></div>
    </section>
  </div>
</template>

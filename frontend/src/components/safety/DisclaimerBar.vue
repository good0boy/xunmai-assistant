<script setup lang="ts">
defineProps<{
  level: string
  text: string
}>()
</script>

<template>
  <div class="disc" :class="level">
    <span class="ico">!</span>
    <span>{{ text }}</span>
  </div>
</template>

<style scoped>
.disc {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  margin-top: 14px;
  padding: 10px 12px;
  border-radius: var(--xm-radius-sm);
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--xm-text-secondary);
  background: var(--xm-bg-sunken);
  border: 1px solid var(--xm-border);
}
.disc.severe {
  background: var(--xm-warn-soft);
  border-color: #edd9b0;
  color: #7a5210;
}
.disc.emergency {
  background: var(--xm-danger-soft);
  border-color: #f0c4bf;
  color: var(--xm-danger);
  font-weight: 500;
}
.ico {
  flex: 0 0 auto;
  width: 16px;
  height: 16px;
  margin-top: 2px;
  border-radius: 50%;
  border: 1.5px solid currentColor;
  font-size: 11px;
  font-weight: 700;
  line-height: 13px;
  text-align: center;
}
</style>

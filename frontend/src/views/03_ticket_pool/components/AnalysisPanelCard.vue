<template>
  <article :class="['analysis-panel-card', tone]">
    <header>
      <div>
        <small>{{ eyebrow }}</small>
        <h3>{{ title }}</h3>
        <p v-if="subtitle">{{ subtitle }}</p>
      </div>
      <div class="panel-actions">
        <select v-if="options?.length" :value="modelValue" @change="$emit('update:modelValue', ($event.target as HTMLSelectElement).value)">
          <option v-for="opt in options" :key="opt.key" :value="opt.key">{{ opt.label }}</option>
        </select>
        <button v-if="showReset" @click="$emit('reset')">返回</button>
        <button v-if="showZoom" @click="$emit('zoom')">放大</button>
      </div>
    </header>
    <slot />
  </article>
</template>

<script setup lang="ts">
defineProps<{
  title: string
  eyebrow?: string
  subtitle?: string
  tone?: string
  modelValue?: string
  options?: { key: string; label: string }[]
  showReset?: boolean
  showZoom?: boolean
}>()
defineEmits<{ 'update:modelValue': [value: string]; reset: []; zoom: [] }>()
</script>

<style scoped>
.analysis-panel-card { min-height: 280px; background: #fff; border: 1px solid #e7edf6; border-radius: 18px; padding: 16px; box-shadow: 0 12px 34px rgba(15,23,42,.06); overflow: hidden; }
.analysis-panel-card header { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; margin-bottom:14px; }
.analysis-panel-card small { display:block; color:#64748b; font-weight:900; letter-spacing:.03em; }
.analysis-panel-card h3 { margin:3px 0 0; color:#0f172a; font-size:17px; }
.analysis-panel-card p { margin:5px 0 0; color:#64748b; font-size:12px; line-height:1.45; }
.panel-actions { display:flex; gap:8px; align-items:center; flex-wrap:wrap; justify-content:flex-end; }
.panel-actions select,.panel-actions button { border:1px solid #dbeafe; background:#eff6ff; color:#2563eb; border-radius:10px; padding:7px 10px; font-weight:800; cursor:pointer; }
.panel-actions select { max-width: 170px; background:#fff; color:#334155; }
.analysis-panel-card.good { border-color:#bbf7d0; }
.analysis-panel-card.warn { border-color:#fed7aa; }
.analysis-panel-card.bad { border-color:#fecaca; }
</style>

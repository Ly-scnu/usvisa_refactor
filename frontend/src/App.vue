<template>
  <div class="ops-shell">
    <aside class="ops-rail">
      <div class="rail-logo" title="OpenSands"><img src="./assets/opensands-logo.svg" alt="OpenSands" /></div>
      <button
        v-for="item in nav"
        :key="item.key"
        :class="['rail-item', { active: page === item.key }]"
        @click="page = item.key"
      >
        <span class="rail-icon">{{ item.icon }}</span>
        <span>{{ item.label }}</span>
      </button>
      <button class="rail-collapse">‹<span>收起</span></button>
    </aside>

    <main class="ops-main">
      <header class="ops-header">
        <div class="crumbs">
          <h1>本地自动化总控台</h1>
          <span>|</span>
          <b>{{ activeNav?.order }} {{ activeNav?.label }}</b>
          <span>|</span>
          <p>本地自动化总控台运行总览</p>
        </div>

        <div class="status-strip">
          <span :class="['status-dot-text', status?.system.pipeline_running ? 'green' : 'red']">
            <i></i>{{ status?.system.pipeline_running ? '运行中' : '已停止' }}
          </span>
          <span>槽位概览：总 <b>{{ status?.slot_policy.total_slots ?? '-' }}</b></span>
          <span>正常 <b>{{ normalCount }}</b></span>
          <span>恢复中 <b>{{ recoveringCount }}</b></span>
          <span>待人工 <b>{{ manualCount }}</b></span>
          <span>当前模式：<b class="mode-pill">{{ modeLabel }}</b></span>
          <span>地点：<b>{{ status?.target.post_name || '北京' }}</b></span>
          <span>日期范围：<b>{{ dateRange }}</b></span>
          <span :class="['ws-mini', store.connected ? 'ok' : 'warn']"><i></i>WebSocket {{ store.connected ? '已连接' : '重连中' }}</span>
        </div>
      </header>

      <div class="control-row">
        <span>配置版本：<b>cfg-v2026.06.12-r3</b></span>
        <span>数据新鲜度：<b>{{ freshnessText }}</b></span>
        <span v-if="store.error" class="inline-error">API Error: {{ store.error }}</span>
        <div class="control-actions">
          <button class="btn start" :disabled="store.busy" @click="store.pipeline('start')">▶ 启动</button>
          <button class="btn ghost" disabled>Ⅱ 暂停</button>
          <button class="btn primary" :disabled="store.busy" @click="store.pipeline('restart')">▶ 继续/重启</button>
          <button class="btn danger" :disabled="store.busy" @click="store.pipeline('stop')">■ 停止</button>
          <button class="btn ghost" :disabled="store.busy" @click="store.pipeline('restart')">⟳ 重启</button>
        </div>
      </div>

      <component :is="currentView" />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useSystemStore } from './store/system'
import Overview from './views/01_overview/index.vue'
import Diagnose from './views/02_diagnose/index.vue'
import TicketPool from './views/03_ticket_pool/index.vue'
import Settings from './views/04_settings/index.vue'
import Cleanup from './views/05_cleanup/index.vue'

const store = useSystemStore()
const page = ref('overview')
const nav = [
  { key: 'overview', order: '01', label: '总览', icon: '⌂' },
  { key: 'settings', order: '02', label: '配置', icon: '⚙' },
  { key: 'diagnose', order: '03', label: '诊断', icon: '♙' },
  { key: 'pool', order: '04', label: '分析', icon: '▤' },
  { key: 'cleanup', order: '05', label: '清理', icon: '⌫' }
]

const status = computed(() => store.status)
const activeNav = computed(() => nav.find((item) => item.key === page.value) || nav[0])
const views: Record<string, unknown> = { overview: Overview, diagnose: Diagnose, pool: TicketPool, settings: Settings, cleanup: Cleanup }
const currentView = computed(() => views[page.value] || Overview)
const modeLabel = computed(() => store.config?.booking?.armed ? 'armed' : 'dry-run')
const dateRange = computed(() => {
  const target = status.value?.target
  return `${target?.start_date || 'now'} ~ ${target?.cutoff_date || target?.end_date || '-'}`
})
const freshnessText = computed(() => {
  if (!status.value?.ts) return '-'
  const diff = Math.max(0, Math.round((Date.now() - new Date(status.value.ts).getTime()) / 1000))
  return `${diff} 秒前`
})
const normalCount = computed(() => (status.value?.slots || []).filter((s) => !s.last_reason && !s.stale).length)
const recoveringCount = computed(() => (status.value?.slots || []).filter((s) => String(s.last_reason || '').includes('recover') || String(s.stage || '').includes('cf_gate')).length)
const manualCount = computed(() => (status.value?.slots || []).filter((s) => /人工|manual|failed|失败|卡住/.test(`${s.last_reason_zh || ''}${s.last_reason || ''}`)).length)

onMounted(async () => {
  await store.refresh()
  store.connectWs()
})
</script>


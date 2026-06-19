<template>
  <section class="sla-panel">
    <header>
      <div>
        <h3>智能调度 / SLA</h3>
        <p>围绕目标成功查询间隔，根据热会话、等待室、CF、风控事件动态决定槽位压力。</p>
      </div>
      <span :class="['level', levelClass]">{{ pressureLevel }}</span>
    </header>
    <div class="sla-grid">
      <article><small>目标间隔</small><b>{{ fmtSeconds(summary.target_interval_seconds || decision.target_interval_seconds) }}</b><span>后续改 60/30 都走配置</span></article>
      <article><small>下一目标查询</small><b>{{ fmtTime(summary.next_target_query_at || decision.next_target_query_at) }}</b><span>距目标 {{ fmtSeconds(summary.seconds_to_target ?? decision.seconds_to_target) }}</span></article>
      <article><small>提前启动</small><b>{{ fmtSeconds(summary.query_launch_lead_seconds || latency.lead_seconds) }}</b><span>{{ latency.source === 'events' ? '按实测耗时预估' : '按配置兜底' }}</span></article>
      <article><small>高峰策略</small><b>{{ peakModeZh }}</b><span>{{ peakMode.reason || '普通时段' }}</span></article>
      <article><small>槽位压力</small><b>{{ pool.active_slots ?? evidence.active_slots ?? '-' }} / {{ decision.desired_active_slots ?? '-' }}</b><span>当前 / 建议</span></article>
      <article><small>热会话</small><b>{{ pool.hot_sessions || 0 }} + {{ pool.query_wait_sessions || 0 }}</b><span>hot + query_wait</span></article>
      <article><small>冷却/生产</small><b>{{ pool.cooling_sessions || 0 }} / {{ pool.producing_sessions || 0 }}</b><span>cooling / producing</span></article>
      <article><small>瓶颈判断</small><b>{{ bottleneckZh }}</b><span>{{ pressure.significant_events || 0 }} 个近期关键事件</span></article>
    </div>
    <div class="inventory-grid">
      <article :class="{ warn: inventory.hot_deficit > 0 }">
        <small>热查询池</small>
        <b>{{ inventory.hot_query_sessions || 0 }} / {{ inventory.target_hot_query_sessions || 4 }}</b>
        <span>已登录且成功查过，用于60秒轮转</span>
      </article>
      <article :class="{ warn: inventory.login_standby_deficit > 0 }">
        <small>登录备用池</small>
        <b>{{ inventory.login_standby_sessions || 0 }} / {{ inventory.target_login_standby_sessions || 2 }}</b>
        <span>已到登录/密保边界，失效时快速补位</span>
      </article>
      <article :class="{ warn: inventory.candidate_deficit > 0 }">
        <small>候选生产池</small>
        <b>{{ inventory.candidate_sessions || 0 }} / {{ inventory.target_candidate_sessions || 2 }}</b>
        <span>代理/CF/等待室中的替补产能</span>
      </article>
      <article :class="['health', inventory.health_level || 'unknown']">
        <small>库存状态</small>
        <b>{{ inventoryHealthZh }}</b>
        <span>{{ inventory.reason || '等待库存统计' }}</span>
      </article>
    </div>
    <div class="reason-row">
      <b>策略原因</b>
      <span>{{ summary.reason || decision.reason || '暂无调度决策' }}</span>
    </div>
    <div class="pressure-bars">
      <div v-for="item in pressureRows" :key="item.key">
        <label>{{ item.label }}</label>
        <span><i :style="{ width: `${Math.round(item.value * 100)}%` }"></i></span>
        <em>{{ Math.round(item.value * 100) }}%</em>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ sla?: Record<string, any> }>()
const decision = computed(() => props.sla?.decision || {})
const pool = computed(() => props.sla?.pool || {})
const pressure = computed(() => props.sla?.pressure || {})
const summary = computed(() => props.sla?.summary || {})
const evidence = computed(() => decision.value?.evidence || {})
const latency = computed(() => props.sla?.latency || decision.value?.latency || {})
const peakMode = computed(() => props.sla?.peak_mode || summary.value.peak_mode || decision.value?.peak_mode || {})
const inventory = computed(() => props.sla?.inventory || summary.value.inventory || pool.value.inventory || evidence.value.inventory || {})
const pressureLevel = computed(() => summary.value.pressure_level || decision.value.pressure_level || 'normal')
const levelClass = computed(() => String(pressureLevel.value).replace(/[^a-z0-9_-]/gi, '_'))
const bottleneckMap: Record<string, string> = { normal: '正常', waiting_room: '等待室', cf_challenge: 'CF/人机风暴', login: '登录回跳', network: '网络/代理', risk: '1015/阻断', rate_limit: '429/限流', churn: '重启抖动' }
const bottleneckZh = computed(() => bottleneckMap[String(summary.value.bottleneck || decision.value.bottleneck || pressure.value.bottleneck || 'normal')] || String(summary.value.bottleneck || '-'))
const peakMap: Record<string, string> = { normal: '普通', prewarm: '预热', peak: '高峰', cooldown: '排水' }
const peakModeZh = computed(() => peakMap[String(peakMode.value.mode || 'normal')] || String(peakMode.value.mode || '普通'))
const inventoryHealthMap: Record<string, string> = { healthy: '达标', hot_low: '热池不足', login_standby_low: '登录备用不足', candidate_low: '候选不足', unknown: '统计中' }
const inventoryHealthZh = computed(() => inventoryHealthMap[String(inventory.value.health_level || 'unknown')] || String(inventory.value.health_level || '统计中'))
const pressureRows = computed(() => [
  { key: 'wr', label: '等待室', value: Number(pressure.value.waiting_room_pressure || 0) },
  { key: 'cf', label: 'CF', value: Number(pressure.value.cf_pressure || 0) },
  { key: 'login', label: '登录', value: Number(pressure.value.login_pressure || 0) },
  { key: '429', label: '429', value: Number(pressure.value.rate_limit_pressure || 0) },
  { key: 'risk', label: '风控', value: Number(pressure.value.risk_pressure || 0) },
  { key: 'churn', label: '抖动', value: Number(pressure.value.churn_pressure || 0) },
])
function fmtSeconds(v?: number) { const n = Math.max(0, Math.round(Number(v || 0))); if (n < 60) return `${n}s`; return `${Math.floor(n / 60)}m${n % 60}s` }
function fmtTime(ts?: string) { if (!ts) return '—'; const d = new Date(ts); return Number.isNaN(d.getTime()) ? String(ts) : d.toLocaleTimeString('zh-CN', { hour12: false }) }
</script>

<style scoped>
.sla-panel{background:rgba(255,255,255,.94);border:1px solid #dbeafe;border-radius:18px;padding:16px 18px;box-shadow:0 12px 34px rgba(37,99,235,.08);display:grid;gap:14px;overflow:hidden;min-width:0}.sla-panel *{box-sizing:border-box;min-width:0}.sla-panel header{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.sla-panel h3{margin:0;color:#0f172a}.sla-panel p{margin:4px 0 0;color:#64748b}.level{flex:0 0 auto;border-radius:999px;padding:6px 12px;background:#eef2ff;color:#2563eb;font-weight:900;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.level.risk_hold,.level.rate_limit_hold,.level.cf_storm_hold,.level.churn_hold,.level.network_hold{background:#fee2e2;color:#dc2626}.level.waiting_room_pressure,.level.cf_prewarm,.level.sla_missed,.level.critical_gap,.level.peak_prewarm,.level.peak,.level.inventory_fill,.level.inventory_hot_low,.level.inventory_login_standby_low,.level.inventory_candidate_low{background:#ffedd5;color:#ea580c}.level.stable_reduce,.level.peak_cooldown_drain{background:#dcfce7;color:#16a34a}.sla-grid,.inventory-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}.sla-grid article,.inventory-grid article{background:#f8fafc;border:1px solid #e7edf6;border-radius:14px;padding:12px;overflow:hidden}.inventory-grid article{background:#fff;border-color:#e2e8f0}.inventory-grid article.warn{background:#fff7ed;border-color:#fed7aa}.inventory-grid article.health.healthy{background:#f0fdf4;border-color:#bbf7d0}.inventory-grid article.health.hot_low,.inventory-grid article.health.login_standby_low,.inventory-grid article.health.candidate_low{background:#fff7ed;border-color:#fdba74}.sla-grid small,.inventory-grid small{display:block;color:#64748b;white-space:nowrap}.sla-grid b,.inventory-grid b{display:block;margin-top:4px;font-size:20px;color:#0f172a;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;word-break:keep-all}.sla-grid span,.inventory-grid span{display:block;margin-top:2px;color:#94a3b8;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;word-break:keep-all}.reason-row{display:flex;align-items:center;gap:12px;background:#eff6ff;border:1px solid #dbeafe;border-radius:12px;padding:10px 12px;color:#475569;min-height:48px;overflow:hidden}.reason-row b{flex:0 0 auto;color:#2563eb;white-space:nowrap}.reason-row span{flex:1 1 auto;display:block;line-height:1.45;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;word-break:keep-all;overflow-wrap:normal}.pressure-bars{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}.pressure-bars div{display:grid;grid-template-columns:52px minmax(80px,1fr) 42px;gap:8px;align-items:center;color:#64748b;font-size:12px}.pressure-bars span{height:8px;border-radius:999px;background:#e2e8f0;overflow:hidden}.pressure-bars i{display:block;height:100%;border-radius:999px;background:linear-gradient(90deg,#60a5fa,#2563eb)}.pressure-bars em{font-style:normal;text-align:right}@media(max-width:760px){.sla-panel header{display:grid}.reason-row{align-items:flex-start}.reason-row span{white-space:normal;word-break:normal;overflow-wrap:break-word}.pressure-bars{grid-template-columns:1fr}}
</style>


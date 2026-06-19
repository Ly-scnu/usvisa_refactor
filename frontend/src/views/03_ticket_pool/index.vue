<template>
  <section class="analysis-page">
    <header class="analysis-hero">
      <div>
        <h2>票池 / 策略分析总览</h2>
        <p>管理所有会话票产出的生命周期、查询结果、代理表现、阶段耗时和放票趋势，用于找出最稳、最快、最容易复用的路线。</p>
      </div>
      <div class="analysis-actions">
        <span :class="['live-refresh', refreshError ? 'bad' : isRefreshing ? 'syncing' : 'ok']">
          <i></i>{{ refreshStatusText }}
        </span>
        <button :disabled="isRefreshing" @click="loadAnalytics(true)">⟳ {{ isRefreshing ? '同步中' : '刷新分析' }}</button>
        <button @click="exportJson">⇩ 导出 JSON</button>
      </div>
    </header>

    <section class="metric-grid">
      <article v-for="card in metricCards" :key="card.key" class="metric-card">
        <i :class="card.tone">{{ card.icon }}</i>
        <div><small>{{ card.label }}</small><b>{{ card.value }}</b><span>{{ card.hint }}</span></div>
      </article>
    </section>

    <SlaOrchestratorPanel :sla="analytics.sla_orchestrator || summary.sla_orchestrator" />

    <section :class="['analysis-layout', { 'analysis-full': activeTab === 'analysis' }]">
      <main class="analysis-main-card">
        <nav class="pool-tabs">
          <button v-for="tab in tabs" :key="tab.key" :class="{ active: activeTab === tab.key }" @click="activeTab = tab.key">{{ tab.label }}</button>
        </nav>
        <AnalysisDashboard v-if="activeTab === 'analysis'" :analytics="analytics" @open-session="openDetail" />
        <template v-else>
        <div class="filter-row">
          <input v-model.trim="keyword" placeholder="搜索会话 / 票ID / 槽位 / 代理 / 结果" />
          <select v-model="statusFilter"><option value="all">全部状态</option><option value="using">使用中</option><option value="paused">暂停中</option><option value="success">查询成功</option><option value="failed">已失败</option><option value="hit">命中</option></select>
          <select v-model="slotFilter"><option value="all">全部槽位</option><option v-for="slot in slotOptions" :key="slot" :value="slot">{{ displaySlot(slot) }}</option></select>
          <select v-model="proxyFilter"><option value="all">全部代理</option><option v-for="proxy in proxyOptions" :key="proxy" :value="proxy">{{ proxy }}</option></select>
        </div>

        <div v-if="activeTab === 'query_records'" class="analysis-table-wrap">
          <table class="analysis-table query-record-table">
            <thead><tr><th>查询记录ID</th><th>绑定票/会话</th><th>槽位</th><th>查询成功时间</th><th>成功间隔</th><th>最近日期</th><th>日期数/预览</th><th>代理/路线</th><th>产生流程</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="rec in pagedRecords" :key="rec.query_success_id">
                <td><b>{{ rec.query_success_id }}</b><small>live {{ rec.live_round || '-' }}</small></td>
                <td><a @click="openRecordDetail(rec)">{{ rec.ticket_id }}</a><small>{{ rec.session_label || rec.session_id }}</small></td>
                <td>{{ displaySlot(rec.slot_id) }}</td>
                <td>{{ fmt(rec.queried_at || rec.query_success_time) }}</td>
                <td><span class="interval-pill">{{ intervalText(rec.interval_since_previous_success_seconds) }}</span></td>
                <td class="date-result-cell"><b>{{ rec.nearest_date || '无日期' }}</b><small>只表示本次查询返回的最早日期</small></td>
                <td class="date-result-cell"><b>{{ rec.days_count || 0 }} 个日期</b><small>{{ (rec.days_preview || []).slice(0, 6).join(', ') }}</small></td>
                <td><b>{{ rec.proxy_display || '-' }}</b><small>{{ rec.route || '-' }}</small></td>
                <td class="flow-summary"><small>{{ rec.production_flow_summary || rec.source || '-' }}</small></td>
                <td><button class="tiny" @click="openRecordDetail(rec)">会话详情</button></td>
              </tr>
              <tr v-if="!filteredRecords.length"><td colspan="10" class="empty-cell">暂无查询成功记录；每一次成功拿到 days 都会独立生成一条记录。</td></tr>
            </tbody>
          </table>
        </div>

        <div v-else class="analysis-table-wrap">
          <table class="analysis-table">
            <thead><tr><th>产出会话</th><th>票ID</th><th>槽位</th><th>代理/路线</th><th>创建时间</th><th>当前状态</th><th>查询次数</th><th>最近查询</th><th>最近查询日期</th><th>存活</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="row in pagedSessions" :key="row.key">
                <td><a @click="openDetail(row)">{{ sessionLabel(row) }}</a><small>{{ row.session_id }}</small></td>
                <td>{{ row.ticket_id }}</td>
                <td>{{ displaySlot(row.slot_id) }}</td>
                <td><b>{{ row.proxy_display || '-' }}</b><small>{{ row.route || '-' }}</small></td>
                <td>{{ fmt(row.created_at) }}</td>
                <td><span :class="['status-pill', row.status]">{{ row.status_zh }}</span></td>
                <td>{{ row.uses_count || row.query_success_count || 0 }}</td>
                <td>{{ fmt(row.last_query_at || row.updated_at) }}</td>
                <td class="date-result-cell">
                  <b>{{ latestDateText(row) }}</b>
                  <small v-if="row.query_success_count">
                    {{ row.last_query_days_count || row.days_count || 0 }} 个日期 · 最后查于 {{ fmt(row.last_query_at || row.updated_at) }}
                  </small>
                  <small v-else>{{ row.last_result }}</small>
                </td>
                <td>{{ duration(row.alive_seconds) }}</td>
                <td><button class="tiny" @click="openDetail(row)">详情</button></td>
              </tr>
              <tr v-if="!filteredSessions.length"><td colspan="11" class="empty-cell">暂无匹配票池数据；成功查到 days 后会自动进入票池历史。</td></tr>
            </tbody>
          </table>
        </div>
        <footer class="table-foot">
          <span>显示 {{ currentFilteredLength ? pageStart + 1 : 0 }}-{{ pageEnd }} / 筛选 {{ currentFilteredLength }} 条；总计 {{ currentTotalLength }} 条。</span>
          <div class="pager">
            <button :disabled="currentPage <= 1" @click="goPage(1)">首页</button>
            <button :disabled="currentPage <= 1" @click="goPage(currentPage - 1)">上一页</button>
            <button
              v-for="p in visiblePages"
              :key="p"
              :class="{ active: p === currentPage }"
              @click="goPage(p)"
            >{{ p }}</button>
            <button :disabled="currentPage >= totalPages" @click="goPage(currentPage + 1)">下一页</button>
            <button :disabled="currentPage >= totalPages" @click="goPage(totalPages)">末页</button>
            <select v-model.number="pageSize">
              <option :value="10">10/页</option>
              <option :value="15">15/页</option>
              <option :value="20">20/页</option>
              <option :value="30">30/页</option>
              <option :value="50">50/页</option>
            </select>
          </div>
        </footer>
        </template>
      </main>

      <aside v-if="activeTab !== 'analysis'" class="analysis-side">
        <article class="side-card health-card">
          <h3>票池健康度</h3>
          <div class="donut" :style="{ '--pct': `${healthPct}%` }"><b>{{ healthPct }}%</b></div>
          <ul><li><i class="green"></i>查询成功 {{ summary.query_success || 0 }}</li><li><i class="blue"></i>使用/暂停 {{ activeOrPausedCount }}</li><li><i class="orange"></i>恢复事件 {{ summary.recovery_events || 0 }}</li><li><i class="red"></i>失败 {{ summary.failed || 0 }}</li></ul>
        </article>

        <article class="side-card">
          <h3>今日命中/放票趋势</h3>
          <div class="sparkline">
            <span v-for="h in hourly" :key="h.hour" :title="`${h.hour} 查询 ${h.queries} / 新日期 ${h.new_dates}`" :style="{ height: `${barHeight(h)}%` }"></span>
          </div>
          <div class="hour-labels"><small>00:00</small><small>12:00</small><small>24:00</small></div>
        </article>

        <article class="side-card">
          <h3>最近放票/日期变化</h3>
          <div class="release-list">
            <div v-for="item in releaseEvents.slice().reverse().slice(0, 4)" :key="`${item.queried_at}-${item.round_id}`">
              <b>{{ fmt(item.queried_at) }}</b><span>{{ displaySlot(item.slot_id || '') }} {{ item.round_id || '' }}</span><em>新增 {{ item.new_dates?.length || 0 }} 日期：{{ (item.new_dates || []).slice(0, 3).join(', ') }}</em>
            </div>
            <p v-if="!releaseEvents.length" class="muted">暂无可比较的多轮日期变化。</p>
          </div>
        </article>

        <article class="side-card">
          <h3>代理/路线表现</h3>
          <div class="proxy-rank">
            <div v-for="proxy in byProxy.slice(0, 5)" :key="proxy.proxy_display">
              <b>{{ proxy.proxy_display }}</b><span>成功率 {{ proxy.success_rate }}% · 成功 {{ proxy.query_success }}/{{ proxy.total }} · 平均存活 {{ duration(proxy.avg_alive_seconds) }}</span>
            </div>
          </div>
        </article>
      </aside>
    </section>

    <div v-if="detail" class="analysis-modal" @click.self="detail = null">
      <section>
        <header><div><h2>{{ sessionLabel(detail) }} 完整生命周期</h2><p>{{ detail.ticket_id }} · {{ detail.proxy_display || '-' }} · {{ detail.status_zh }}</p></div><button @click="detail = null">×</button></header>
        <div class="detail-grid">
          <span>创建时间<b>{{ fmt(detail.created_at) }}</b></span><span>最近查询<b>{{ fmt(detail.last_query_at || detail.updated_at) }}</b></span><span>最近查询日期<b>{{ latestDateText(detail) }}</b></span><span>存活时间<b>{{ duration(detail.alive_seconds) }}</b></span><span>复用次数<b>{{ detail.uses_count || 0 }}</b></span><span>查到日期<b>{{ detail.last_query_days_count || detail.days_count || 0 }}</b></span><span>命中次数<b>{{ detail.hit_count || 0 }}</b></span>
        </div>
        <h3>阶段耗时</h3>
        <div class="stage-bars"><div v-for="item in stageDurationRows(detail)" :key="item.stage"><b>{{ stageLabel(item.stage) }}</b><span><i :style="{ width: `${item.pct}%` }"></i></span><em>{{ duration(item.seconds) }}</em></div></div>
        <h3>每次查询结果</h3>
        <div class="query-result-list"><div v-for="q in detail.query_results || []" :key="q.queried_at"><b>{{ fmt(q.queried_at) }}</b><span>{{ q.post_name || '-' }} · 最近 {{ q.nearest_date || '无日期' }} · {{ q.days_count || 0 }} 日期 · 命中 {{ q.target_hit ? '是' : '否' }} · 点击日期 {{ q.clicked_date ? '是' : '否' }}</span><em>{{ (q.days || []).slice(0, 8).join(', ') }}</em></div><p v-if="!(detail.query_results || []).length" class="muted">该票没有成功查询记录，只保留失败流程。</p></div>
        <h3>完整事件链</h3>
        <p class="muted">这里只展示语义阶段切换和错误事件；10秒实时快照不进入历史链，避免截图被覆盖后误判。</p>
        <div class="flow-list"><div v-for="ev in detail.flow || []" :key="`${ev.ts}-${ev.event_type}-${ev.stage}-${ev.title}`"><time>{{ fmt(ev.ts) }}</time><b>{{ ev.title || stageLabel(ev.stage || ev.event_type) }}</b><span>{{ ev.message || ev.event_type }}</span><a v-if="ev.screenshot" :href="storageHref(ev.screenshot)" target="_blank">截图</a></div></div>
      </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { API_BASE, apiGet } from '../../api/request'
import { useSystemStore } from '../../store/system'
import AnalysisDashboard from './components/AnalysisDashboard.vue'
import SlaOrchestratorPanel from './components/SlaOrchestratorPanel.vue'

type Analytics = Record<string, any>
const store = useSystemStore()
const CACHE_KEY = 'usvisa.ticket_analytics.cache.v4'
const analytics = ref<Analytics>({ summary: {}, sessions: [], query_success_records: [], by_proxy: [], hourly: [], release_events: [] })
const isRefreshing = ref(false)
const refreshError = ref('')
const lastRefreshAt = ref('')
const lastRefreshMs = ref(0)
const lastRefreshCostMs = ref(0)
let refreshTimer: number | null = null
let recordRefreshTimer: number | null = null
let pendingTimer: number | null = null
let inFlight: Promise<void> | null = null
let recordInFlight: Promise<void> | null = null
const lastRecordSeq = ref(0)
const activeTab = ref('all')
const keyword = ref('')
const statusFilter = ref('all')
const slotFilter = ref('all')
const proxyFilter = ref('all')
const detail = ref<any | null>(null)
const currentPage = ref(1)
const pageSize = ref(15)
const tabs = [{ key: 'all', label: '全部票' }, { key: 'using', label: '使用/暂停中' }, { key: 'success', label: '会话成功' }, { key: 'query_records', label: '查询成功记录' }, { key: 'failed', label: '已失败' }, { key: 'hit', label: '命中目标' }, { key: 'analysis', label: '分析面板' }]
const summary = computed(() => analytics.value.summary || {})
const sessions = computed<any[]>(() => analytics.value.sessions || [])
const queryRecords = computed<any[]>(() => analytics.value.query_success_records || [])
const byProxy = computed<any[]>(() => analytics.value.by_proxy || [])
const hourly = computed<any[]>(() => analytics.value.hourly || Array.from({ length: 24 }, (_, i) => ({ hour: `${String(i).padStart(2, '0')}:00`, queries: 0, hits: 0, new_dates: 0 })))
const releaseEvents = computed<any[]>(() => analytics.value.release_events || [])
const refreshStatusText = computed(() => {
  if (refreshError.value) return `同步失败：${refreshError.value}`
  if (isRefreshing.value) return '实时同步中'
  if (!lastRefreshAt.value) return '等待首次同步'
  const ago = Math.max(0, Math.round((Date.now() - new Date(lastRefreshAt.value).getTime()) / 1000))
  const cost = lastRefreshCostMs.value ? ` · ${Math.max(0.1, lastRefreshCostMs.value / 1000).toFixed(1)}s` : ''
  return `实时同步 · ${ago}s 前${cost}`
})
const activeOrPausedCount = computed(() => sessions.value.filter((x) => ['using', 'paused'].includes(x.status)).length)
const healthPct = computed(() => Math.round(Number(summary.value.success_rate || 0)))
const slotOptions = computed(() => [...new Set([...sessions.value, ...queryRecords.value].map((x) => x.slot_id).filter(Boolean))])
const proxyOptions = computed(() => [...new Set([...sessions.value, ...queryRecords.value].map((x) => x.proxy_display).filter(Boolean))])
const metricCards = computed(() => [
  { key: 'total', label: '会话产出数', value: summary.value.total_sessions || 0, hint: '每条=一次完整生产流程', icon: '◉', tone: 'blue' },
  { key: 'query', label: '查询成功', value: summary.value.query_success || 0, hint: `成功率 ${summary.value.success_rate || 0}%`, icon: '✓', tone: 'green' },
  { key: 'used', label: '成功会话数', value: summary.value.used || 0, hint: `复用概率 ${summary.value.reuse_probability || 0}%`, icon: '▣', tone: 'blue' },
  { key: 'hit', label: '命中目标', value: summary.value.hit || 0, hint: '截止日前命中', icon: '◎', tone: 'green' },
  { key: 'signal', label: '平均存活', value: duration(summary.value.avg_alive_seconds || 0), hint: '从产出到失败/关闭', icon: '⌁', tone: 'orange' },
  { key: 'failed', label: '已失败数', value: summary.value.failed || 0, hint: '用于定位短板', icon: '!', tone: 'red' }
])
const filteredSessions = computed(() => sessions.value.filter((r) => {
  const hay = `${r.ticket_id} ${r.session_id} ${r.session_label || ''} ${r.slot_id} ${r.proxy_display} ${r.route} ${r.last_result} ${r.last_nearest_date || ''} ${(r.last_query_dates_preview || []).join(' ')}`.toLowerCase()
  if (keyword.value && !hay.includes(keyword.value.toLowerCase())) return false
  if (statusFilter.value !== 'all' && r.status !== statusFilter.value) return false
  if (slotFilter.value !== 'all' && r.slot_id !== slotFilter.value) return false
  if (proxyFilter.value !== 'all' && r.proxy_display !== proxyFilter.value) return false
  if (activeTab.value === 'using') return ['using', 'paused'].includes(r.status)
  if (activeTab.value === 'success') return r.query_success_count > 0
  if (activeTab.value !== 'all') return r.status === activeTab.value
  return true
}))
const filteredRecords = computed(() => queryRecords.value.filter((r) => {
  const hay = `${r.query_success_id} ${r.ticket_id} ${r.session_id} ${r.session_label || ''} ${r.slot_id} ${r.proxy_display} ${r.route} ${r.nearest_date || ''} ${(r.days_preview || []).join(' ')}`.toLowerCase()
  if (keyword.value && !hay.includes(keyword.value.toLowerCase())) return false
  if (slotFilter.value !== 'all' && r.slot_id !== slotFilter.value) return false
  if (proxyFilter.value !== 'all' && r.proxy_display !== proxyFilter.value) return false
  return true
}))
const isRecordTab = computed(() => activeTab.value === 'query_records')
const currentFilteredLength = computed(() => isRecordTab.value ? filteredRecords.value.length : filteredSessions.value.length)
const currentTotalLength = computed(() => isRecordTab.value ? queryRecords.value.length : sessions.value.length)
const totalPages = computed(() => Math.max(1, Math.ceil(currentFilteredLength.value / pageSize.value)))
const pageStart = computed(() => currentFilteredLength.value ? (currentPage.value - 1) * pageSize.value : 0)
const pageEnd = computed(() => Math.min(currentFilteredLength.value, pageStart.value + pageSize.value))
const pagedSessions = computed(() => filteredSessions.value.slice(pageStart.value, pageEnd.value))
const pagedRecords = computed(() => filteredRecords.value.slice(pageStart.value, pageEnd.value))
const visiblePages = computed(() => {
  const total = totalPages.value, cur = currentPage.value
  const start = Math.max(1, Math.min(cur - 2, total - 4))
  const end = Math.min(total, start + 4)
  return Array.from({ length: end - start + 1 }, (_, i) => start + i)
})
watch(activeTab, () => { statusFilter.value = 'all'; currentPage.value = 1 })
watch([keyword, statusFilter, slotFilter, proxyFilter, pageSize], () => { currentPage.value = 1 })
watch(totalPages, (n) => { if (currentPage.value > n) currentPage.value = n })
watch(() => store.status?.ts, () => scheduleAnalyticsRefresh('ws'), { flush: 'post' })
onMounted(() => {
  restoreCachedAnalytics()
  loadAnalytics(true)
  loadQuerySuccessRecords(true)
  refreshTimer = window.setInterval(() => scheduleAnalyticsRefresh('timer'), 15000)
  recordRefreshTimer = window.setInterval(() => loadQuerySuccessRecords(false), 1000)
  document.addEventListener('visibilitychange', onVisibilityChange)
})
onUnmounted(() => {
  if (refreshTimer) window.clearInterval(refreshTimer)
  if (recordRefreshTimer) window.clearInterval(recordRefreshTimer)
  if (pendingTimer) window.clearTimeout(pendingTimer)
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
function onVisibilityChange() {
  if (!document.hidden) {
    loadQuerySuccessRecords(false)
    scheduleAnalyticsRefresh('visible', true)
  }
}
function scheduleAnalyticsRefresh(_reason = 'auto', soon = false) {
  if (document.hidden) return
  const minGap = soon ? 500 : 3500
  const elapsed = Date.now() - lastRefreshMs.value
  if (elapsed < minGap) return
  if (pendingTimer) window.clearTimeout(pendingTimer)
  pendingTimer = window.setTimeout(() => loadAnalytics(false), soon ? 80 : 350)
}
function restoreCachedAnalytics() {
  try {
    const raw = sessionStorage.getItem(CACHE_KEY)
    if (!raw) return
    const cached = JSON.parse(raw)
    if (cached?.analytics) {
      analytics.value = cached.analytics
      syncLastRecordSeq()
      lastRefreshAt.value = cached.saved_at || cached.analytics.generated_at || ''
      // Do not treat cache restore as a real refresh.  The ticket pool is used
      // to verify sub-second/second-level query production, so the first mount
      // must immediately ask the backend for fresh analytics.
      lastRefreshMs.value = 0
    }
  } catch {}
}
function cacheAnalytics(payload: Analytics) {
  try {
    sessionStorage.setItem(CACHE_KEY, JSON.stringify({ saved_at: new Date().toISOString(), analytics: payload }))
  } catch {}
}
function syncLastRecordSeq() {
  const seqs = (analytics.value.query_success_records || []).map((x: any) => Number(x.seq || 0)).filter((x: number) => x > 0)
  lastRecordSeq.value = Math.max(lastRecordSeq.value, ...seqs, 0)
}
function shortTimeFromTs(ts?: string) {
  if (!ts) return 'unknown'
  const d = new Date(ts)
  if (!Number.isNaN(d.getTime())) {
    return `${String(d.getHours()).padStart(2, '0')}${String(d.getMinutes()).padStart(2, '0')}${String(d.getSeconds()).padStart(2, '0')}`
  }
  return String(ts).replace(/[^0-9]/g, '').slice(-6) || 'unknown'
}
function stableSessionTicketId(rec: any) {
  const slot = String(rec.slot_id || '').replace('slot_', 'S') || 'S--'
  const round = String(rec.round_id || '').replace('round_', '') || '----'
  const suffix = shortTimeFromTs(rec.round_started_at || rec.queried_at || rec.ts)
  return `T-${round}-${slot}-${suffix}`
}
function stableSessionId(rec: any) {
  const slot = rec.slot_id || '-'
  const round = rec.round_id || '-'
  return `${slot}-${round}-${shortTimeFromTs(rec.round_started_at || rec.queried_at || rec.ts)}`
}
function recomputeRecordIntervals(records: any[]) {
  const asc = [...records].sort((a, b) => String(a.queried_at || a.ts || '').localeCompare(String(b.queried_at || b.ts || '')))
  let prev = ''
  for (const rec of asc) {
    const cur = rec.queried_at || rec.ts || ''
    if (!prev) {
      rec.interval_since_previous_success_seconds = null
    } else {
      const a = new Date(prev).getTime()
      const b = new Date(cur).getTime()
      rec.interval_since_previous_success_seconds = Number.isFinite(a) && Number.isFinite(b) ? Math.max(0, Math.round((b - a) / 1000)) : null
    }
    prev = cur
  }
  return asc.sort((a, b) => String(b.queried_at || b.ts || '').localeCompare(String(a.queried_at || a.ts || '')))
}
function normalizeRealtimeRecord(rec: any) {
  const qid = rec.ticket_query_id || rec.query_success_id || `seq-${rec.seq || rec.queried_at || rec.ts || Math.random()}`
  const ticketId = rec.ticket_id && !String(rec.ticket_id).startsWith('live_') ? rec.ticket_id : stableSessionTicketId(rec)
  const sessionId = rec.session_id && !String(rec.session_id).includes('undefined') ? rec.session_id : stableSessionId(rec)
  return {
    ...rec,
    query_success_id: rec.query_success_id || `Q-${String(rec.seq || '').padStart(6, '0')}`,
    ticket_id: ticketId,
    session_id: sessionId,
    session_key: `${rec.slot_id || '-'}/${rec.round_id || '-'}/${rec.round_started_at || ''}`,
    session_label: `${displaySlot(rec.slot_id || '')} / ${rec.round_id || '-'} / ${shortTimeFromTs(rec.round_started_at || rec.queried_at || rec.ts)}`,
    query_success_time: rec.queried_at || rec.ts,
    ticket_query_id: qid,
    days_preview: rec.days_preview || [],
    proxy_display: rec.proxy_display || '-',
    route: rec.route || '-',
    production_flow_summary: rec.production_flow_summary || rec.flow_summary || '',
    source: rec.source || 'query_success_records'
  }
}
function mergeQueryRecords(records: any[]) {
  if (!records.length) return
  const existing = new Map<string, any>()
  for (const rec of analytics.value.query_success_records || []) {
    existing.set(String(rec.ticket_query_id || rec.query_success_id), rec)
  }
  for (const raw of records) {
    const rec = normalizeRealtimeRecord(raw)
    existing.set(String(rec.ticket_query_id || rec.query_success_id), rec)
    lastRecordSeq.value = Math.max(lastRecordSeq.value, Number(rec.seq || 0))
  }
  const merged = recomputeRecordIntervals([...existing.values()])
  analytics.value = {
    ...analytics.value,
    query_success_records: merged,
    summary: {
      ...(analytics.value.summary || {}),
      query_success: Math.max(Number(analytics.value.summary?.query_success || 0), merged.length)
    }
  }
  lastRefreshAt.value = new Date().toISOString()
  lastRefreshMs.value = Date.now()
  cacheAnalytics(analytics.value)
}
async function loadQuerySuccessRecords(force = false) {
  if (document.hidden && !force) return
  if (recordInFlight) return recordInFlight
  const after = force ? 0 : lastRecordSeq.value
  recordInFlight = apiGet<any>(`/api/tickets/query-success-records?limit=200&after_seq=${after}`)
    .then((payload) => {
      mergeQueryRecords(payload.records || [])
      if (payload.last_seq) lastRecordSeq.value = Math.max(lastRecordSeq.value, Number(payload.last_seq || 0))
      refreshError.value = ''
    })
    .catch((err: any) => {
      refreshError.value = err?.message || String(err)
    })
    .finally(() => {
      recordInFlight = null
    })
  return recordInFlight
}
async function loadAnalytics(force = false) {
  if (inFlight) return force ? inFlight : undefined
  if (!force && Date.now() - lastRefreshMs.value < 2500) return
  const started = performance.now()
  isRefreshing.value = true
  refreshError.value = ''
  inFlight = apiGet<Analytics>('/api/tickets/analytics?limit=3000')
    .then((payload) => {
      const normalizedRecords = recomputeRecordIntervals((payload.query_success_records || []).map(normalizeRealtimeRecord))
      analytics.value = { ...payload, query_success_records: normalizedRecords }
      syncLastRecordSeq()
      lastRefreshAt.value = payload.generated_at || new Date().toISOString()
      lastRefreshMs.value = Date.now()
      lastRefreshCostMs.value = Math.round(performance.now() - started)
      cacheAnalytics(analytics.value)
    })
    .catch((err: any) => {
      refreshError.value = err?.message || String(err)
    })
    .finally(() => {
      isRefreshing.value = false
      inFlight = null
    })
  return inFlight
}
function openDetail(row: any) { detail.value = row }
function openRecordDetail(rec: any) {
  const row = sessions.value.find((x) => x.key === rec.session_key || x.ticket_id === rec.ticket_id || x.session_id === rec.session_id)
  detail.value = row || null
}
function goPage(page: number) { currentPage.value = Math.min(totalPages.value, Math.max(1, page)) }
function sessionLabel(row: any) { return row?.session_label || `${displaySlot(row?.slot_id || '')} / ${row?.round_id || row?.session_id || '-'}` }
function fmt(ts?: string) { if (!ts) return '—'; const d = new Date(ts); return Number.isNaN(d.getTime()) ? String(ts) : d.toLocaleString('zh-CN', { hour12: false }) }
function duration(sec?: number) { const s = Math.round(Number(sec || 0)); if (s < 60) return `${s}s`; const m = Math.floor(s / 60), r = s % 60; if (m < 60) return `${m}m${r}s`; return `${Math.floor(m / 60)}h${m % 60}m` }
function intervalText(sec?: number | null) { if (sec === null || sec === undefined) return '第一条'; return duration(sec) }
function displaySlot(slot: string) { return slot ? slot.replace('slot_', 'Slot ') : '-' }
function latestDateText(row: any) {
  if (!row) return '—'
  if (row.last_nearest_date) return row.last_nearest_date
  if (row.last_selected_date) return row.last_selected_date
  if (row.query_success_count) return '无可用日期'
  return '—'
}
function barHeight(h: any) { const max = Math.max(1, ...hourly.value.map((x) => Number(x.queries || 0) + Number(x.new_dates || 0))); return Math.max(4, Math.round((Number(h.queries || 0) + Number(h.new_dates || 0)) / max * 100)) }
function stageLabel(stage: string) { return ({ proxy_acquire: '获取代理', cf_gate: 'CF 校验', waiting_room: '等待室', login: '登录/密保', business_query: '查日期/时间段', booking_submit: '提交抢票', stage_enter: '进入阶段', stage_exit: '阶段完成', round_start: '本轮开始', round_finish: '本轮结束' } as Record<string, string>)[stage] || stage || '-' }
function storageHref(v: string) { return v.startsWith('http') ? v : `${API_BASE}/${v.replace(/^\/+/, '')}` }
function stageDurationRows(row: any) { const d = row.stage_durations || {}; const max = Math.max(1, ...Object.values(d).map((x: any) => Number(x || 0))); return Object.entries(d).map(([stage, seconds]) => ({ stage, seconds: Number(seconds), pct: Math.max(4, Math.round(Number(seconds) / max * 100)) })) }
function exportJson() { const blob = new Blob([JSON.stringify(analytics.value, null, 2)], { type: 'application/json' }); const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `ticket_analytics_${Date.now()}.json`; a.click(); URL.revokeObjectURL(a.href) }
</script>

<style scoped>
.analysis-page { display: flex; flex-direction: column; gap: 16px; }
.analysis-hero, .analysis-main-card, .side-card { background: rgba(255,255,255,.92); border: 1px solid #e5edf7; border-radius: 18px; box-shadow: 0 12px 34px rgba(15,23,42,.06); }
.analysis-hero { display:flex; justify-content:space-between; align-items:flex-start; padding:18px 22px; }
.analysis-hero h2 { margin:0; font-size:22px; color:#0f172a; }.analysis-hero p { margin:6px 0 0; color:#64748b; }
.analysis-actions { display:flex; gap:10px; align-items:center; }.analysis-actions button,.tiny { border:1px solid #dbeafe; background:#eff6ff; color:#2563eb; border-radius:10px; padding:9px 13px; font-weight:700; cursor:pointer; }.analysis-actions button:disabled{opacity:.55;cursor:not-allowed}.tiny { padding:5px 9px; }.live-refresh{display:inline-flex;align-items:center;gap:7px;border:1px solid #dbeafe;background:#f8fafc;color:#64748b;border-radius:999px;padding:8px 11px;font-size:12px;font-weight:800;white-space:nowrap;max-width:260px;overflow:hidden;text-overflow:ellipsis}.live-refresh i{width:8px;height:8px;border-radius:50%;background:#22c55e;box-shadow:0 0 0 4px rgba(34,197,94,.12)}.live-refresh.syncing i{background:#3b82f6;animation:pulse 1s infinite}.live-refresh.bad{background:#fff1f2;border-color:#fecdd3;color:#dc2626}.live-refresh.bad i{background:#ef4444}@keyframes pulse{50%{opacity:.35;transform:scale(.82)}}
.metric-grid { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:14px; }.metric-card { background:#fff; border:1px solid #e7edf6; border-radius:16px; padding:18px; display:flex; gap:14px; align-items:center; box-shadow:0 10px 26px rgba(15,23,42,.05); }.metric-card i { width:42px; height:42px; border-radius:50%; display:grid; place-items:center; font-style:normal; font-weight:900; }.metric-card i.blue{background:#eaf2ff;color:#2563eb}.metric-card i.green{background:#dcfce7;color:#16a34a}.metric-card i.orange{background:#ffedd5;color:#f97316}.metric-card i.red{background:#fee2e2;color:#ef4444}.metric-card small{display:block;color:#64748b}.metric-card b{display:block;font-size:26px;color:#0f172a}.metric-card span{font-size:12px;color:#64748b}
.analysis-layout { display:grid; grid-template-columns:minmax(0,1fr) 330px; gap:18px; }.analysis-layout.analysis-full{grid-template-columns:1fr}.analysis-main-card { padding:16px; min-width:0; }.pool-tabs{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap}.pool-tabs button{border:0;background:#f1f5f9;color:#475569;border-radius:10px;padding:9px 16px;font-weight:800;cursor:pointer}.pool-tabs button.active{background:#eaf2ff;color:#2563eb}.filter-row{display:grid;grid-template-columns:1.8fr repeat(3,1fr);gap:10px;margin-bottom:12px}.filter-row input,.filter-row select{border:1px solid #dbe3ef;border-radius:10px;padding:10px 12px;background:#fff;color:#334155}.analysis-table-wrap{overflow:auto;border:1px solid #e7edf6;border-radius:14px}.analysis-table{width:100%;border-collapse:collapse;min-width:1120px}.analysis-table th,.analysis-table td{padding:12px 14px;border-bottom:1px solid #edf2f7;text-align:left;font-size:13px;color:#475569}.analysis-table th{background:#f8fafc;color:#64748b;font-weight:800}.analysis-table td a{color:#2563eb;font-weight:800;cursor:pointer}.analysis-table td small{display:block;color:#94a3b8;margin-top:3px}.status-pill{display:inline-flex;border-radius:999px;padding:4px 9px;font-weight:800;font-size:12px}.status-pill.using,.status-pill.available{background:#dcfce7;color:#16a34a}.status-pill.success,.status-pill.used{background:#dbeafe;color:#2563eb}.status-pill.paused{background:#f1f5f9;color:#475569}.status-pill.failed{background:#fee2e2;color:#dc2626}.status-pill.recovering{background:#ffedd5;color:#ea580c}.status-pill.hit{background:#bbf7d0;color:#15803d}.empty-cell{text-align:center!important;color:#94a3b8!important}.table-foot{padding:12px 4px 0;color:#64748b;font-size:13px;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.pager{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.pager button,.pager select{border:1px solid #dbeafe;background:#fff;color:#475569;border-radius:9px;padding:6px 10px;font-weight:800;cursor:pointer}.pager button.active{background:#2563eb;color:#fff;border-color:#2563eb}.pager button:disabled{opacity:.42;cursor:not-allowed}.pager select{background:#f8fafc}
.date-result-cell b{display:block;color:#0f172a;font-size:14px}.date-result-cell small{max-width:220px;white-space:normal;line-height:1.35}
.query-record-table{min-width:1280px}.interval-pill{display:inline-flex;border-radius:999px;padding:4px 9px;background:#f1f5f9;color:#334155;font-weight:900}.flow-summary small{max-width:260px;white-space:normal;line-height:1.35;color:#64748b}
.analysis-side{display:flex;flex-direction:column;gap:14px}.side-card{padding:18px}.side-card h3{margin:0 0 12px;color:#0f172a}.health-card{display:grid;grid-template-columns:120px 1fr;gap:12px;align-items:center}.health-card h3{grid-column:1/-1}.donut{--pct:0%;width:110px;height:110px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(#22c55e var(--pct),#dbeafe var(--pct) 72%,#e2e8f0 0);position:relative}.donut:after{content:"";position:absolute;width:72px;height:72px;border-radius:50%;background:#fff}.donut b{position:relative;z-index:1;font-size:24px}.health-card ul{list-style:none;padding:0;margin:0;display:grid;gap:8px;color:#475569}.health-card li i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px}.green{background:#22c55e}.blue{background:#3b82f6}.orange{background:#f97316}.red{background:#ef4444}.sparkline{height:110px;display:flex;align-items:end;gap:4px;border-bottom:1px solid #e5e7eb;background:linear-gradient(#f8fafc 1px,transparent 1px);background-size:100% 28px;padding:4px}.sparkline span{flex:1;background:linear-gradient(180deg,#60a5fa,#2563eb);border-radius:5px 5px 0 0;min-height:4px}.hour-labels{display:flex;justify-content:space-between;color:#94a3b8}.release-list,.proxy-rank{display:grid;gap:10px}.release-list div,.proxy-rank div{border-bottom:1px solid #edf2f7;padding-bottom:10px}.release-list b,.proxy-rank b{display:block;color:#334155}.release-list span,.proxy-rank span{display:block;color:#64748b;font-size:12px}.release-list em{display:block;color:#16a34a;font-style:normal;font-size:12px}.muted{color:#94a3b8}
.analysis-modal{position:fixed;inset:0;background:rgba(15,23,42,.42);z-index:100;display:grid;place-items:center;padding:30px}.analysis-modal>section{width:min(1180px,96vw);max-height:88vh;overflow:auto;background:#fff;border-radius:22px;padding:22px;box-shadow:0 30px 80px rgba(15,23,42,.25)}.analysis-modal header{display:flex;justify-content:space-between;align-items:flex-start}.analysis-modal header h2{margin:0;color:#0f172a}.analysis-modal header p{margin:5px 0;color:#64748b}.analysis-modal header button{border:0;background:#f1f5f9;border-radius:50%;width:36px;height:36px;font-size:22px;cursor:pointer}.detail-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:16px 0}.detail-grid span{background:#f8fafc;border:1px solid #e7edf6;border-radius:12px;padding:10px;color:#64748b}.detail-grid b{display:block;color:#0f172a;margin-top:4px}.stage-bars{display:grid;gap:9px}.stage-bars div{display:grid;grid-template-columns:140px 1fr 70px;gap:10px;align-items:center}.stage-bars span{height:10px;background:#e2e8f0;border-radius:999px;overflow:hidden}.stage-bars i{display:block;height:100%;background:#3b82f6;border-radius:999px}.stage-bars em{font-style:normal;color:#64748b}.query-result-list,.flow-list{display:grid;gap:8px}.query-result-list div,.flow-list div{border:1px solid #e7edf6;border-radius:12px;padding:10px;background:#f8fafc}.query-result-list b,.flow-list time{color:#2563eb;font-weight:800}.query-result-list span,.flow-list span{display:block;color:#475569}.query-result-list em{display:block;color:#64748b;font-style:normal;margin-top:4px}.flow-list div{display:grid;grid-template-columns:170px 190px 1fr 48px;gap:10px;align-items:center}.flow-list a{color:#2563eb;font-weight:800;text-decoration:none}
@media(max-width:1200px){.metric-grid{grid-template-columns:repeat(3,1fr)}.analysis-layout{grid-template-columns:1fr}.filter-row{grid-template-columns:1fr 1fr}.detail-grid{grid-template-columns:repeat(2,1fr)}}
</style>





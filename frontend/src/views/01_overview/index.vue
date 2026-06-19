<template>
  <section class="overview-console">
    <section class="overview-kpis">
      <article v-for="kpi in kpiCards" :key="kpi.key" class="overview-kpi-card">
        <div :class="['kpi-icon', kpi.tone]">{{ kpi.icon }}</div>
        <div class="kpi-copy">
          <small>{{ kpi.label }}</small>
          <strong>{{ kpi.value }}</strong>
          <span :class="kpi.trendTone">{{ kpi.hint }}</span>
        </div>
      </article>
    </section>

    <section class="overview-main-grid">
      <article class="date-board console-card">
        <header class="console-card-head">
          <div>
            <h2>日期 / 时间段总览</h2>
            <p>首页只呈现日期分布；点击日期再展开时间段，避免黑箱也避免堆满屏。</p>
          </div>
          <div class="head-actions">
            <label class="ticket-query-select">票池结果
              <select v-model="selectedTicketKey">
                <option value="latest">最新查询</option>
                <option v-for="item in ticketOptions" :key="item.key" :value="item.key">{{ item.label }}</option>
              </select>
            </label>
            <button class="mini-btn" @click="store.refresh()">⟳ 刷新</button>
            <button class="mini-btn primary" @click="openModal('dates')">查看全部日期</button>
          </div>
        </header>
        <div class="date-summary-strip">
          <span>当前显示 <b>{{ selectedTicketLabel }}</b></span>
          <span>查到日期 <b>{{ dateStats.days }}</b> 个</span>
          <span>查到时间段 <b>{{ dateStats.times }}</b> 个</span>
          <span>命中目标 <b>{{ dateStats.hits }}</b> 个</span>
          <span>查询时间 <b>{{ selectedTicketTime }}</b></span>
          <span>目标区间 <b>{{ targetRange }}</b></span>
        </div>
        <div class="date-card-grid">
          <button v-for="date in visibleDateRows" :key="date.date" :class="['date-chip-card', date.tone]" @click="openModal('date', date)">
            <span class="date-dot"></span>
            <b>{{ date.short }}</b>
            <small>{{ date.weekday }}</small>
            <em>{{ date.label }}</em>
            <strong>{{ date.timeCount }} 个时段</strong>
          </button>
          <button v-if="dateRows.length > visibleDateRows.length" class="date-chip-card more" @click="openModal('dates')">
            <b>+{{ dateRows.length - visibleDateRows.length }}</b><small>更多日期</small><em>点击查看</em><strong>弹窗展示</strong>
          </button>
        </div>
        <div class="date-legend">
          <span><i class="hit"></i>命中目标</span><span><i class="ok"></i>可预约日期</span>
          <span><i class="warn"></i>仅发现日期</span><span><i class="muted"></i>等待查询</span>
        </div>
      </article>

      <aside class="focus-diagnosis console-card">
        <header class="console-card-head compact">
          <div>
            <h2>当前关注问题 / 诊断摘要</h2>
            <p>点击槽位后，这里只显示该槽位的最新官方反馈和失败原因。</p>
          </div>
          <label class="only-abnormal"><span>只看异常</span><input type="checkbox" disabled /></label>
        </header>
        <div :class="['focus-alert', diagnoseTone]">
          <div class="alert-badge">{{ diagnoseIcon }}</div>
          <div>
            <div class="focus-title-row">
              <b>{{ displaySlot(focusedSlot?.slot || 'slot') }}</b>
              <span :class="['badge', badgeTone(focusedSlot)]">{{ slotBadge(focusedSlot) }}</span>
            </div>
            <p>{{ diagnoseConclusion }}</p>
          </div>
        </div>
        <div class="focus-flow">
          <div v-for="step in flowSteps" :key="step.key" :class="['focus-flow-step', step.state]">
            <i>{{ step.icon }}</i><span>{{ step.label }}</span>
          </div>
        </div>
        <div class="focus-detail-grid">
          <span>阶段<b>{{ slotPhaseText(focusedSlot) }}</b></span>
          <span>实时页面<b>{{ slotLivePageText(focusedSlot) }}</b></span>
          <span>查询状态<b>{{ slotQueryText(focusedSlot) }}</b></span>
          <span>快照对应<b>{{ slotSnapshotPageText(focusedSlot) }}</b></span>
          <span>轮次<b>{{ focusedSlot?.round ?? focusedSlot?.live_round ?? '-' }}</b></span>
          <span>心跳<b>{{ heartbeat(focusedSlot?.updated_at) }}</b></span>
          <span>恢复组件<b>{{ focusedSlot?.recovery_component || '-' }}</b></span>
          <span>调度池<b>{{ slotPoolText(focusedSlot) }}</b></span>
          <span>健康分<b>{{ slotReuseScore(focusedSlot) }}</b></span>
          <span>下次可查<b>{{ slotNextEta(focusedSlot) }}</b></span>
        </div>
        <div v-if="officialError" class="official-error-box">
          <b>{{ officialError.name || `Cloudflare Error ${officialError.code}` }}</b>
          <p>{{ officialError.headline || officialError.message || '官方返回错误已捕获。' }}</p>
          <small v-if="officialError.code === '1015'">已按 1015 策略：保留证据、换代理/新画像，不在原页无限刷新。</small>
        </div>
        <div v-else class="focus-reason-box">
          <b>最新原因</b><p>{{ focusedReason }}</p>
        </div>
        <div class="focus-actions">
          <button @click="openModal('slot', focusedSlot)">打开详情</button>
          <button @click="openModal('events')">查看关联日志</button>
          <button class="danger-lite" :disabled="!focusedSlot" @click="focusedSlot && store.slotCommand(focusedSlot.slot, 'reload')">重启槽位</button>
        </div>
      </aside>
    </section>

    <section class="compact-slots">
      <article v-for="slot in slots" :key="slot.slot" :class="['compact-slot-card', slotTone(slot), { selected: selectedSlotId === slot.slot }]" @click="selectSlot(slot.slot)">
        <header>
          <div><h3>{{ displaySlot(slot.slot) }}</h3><p><i></i>{{ slotPhaseText(slot) }}</p></div>
          <span :class="['badge', badgeTone(slot)]">{{ slotBadge(slot) }}</span>
        </header>
        <div class="scheduler-strip">
          <span :class="['scheduler-chip', slot.query_eligible ? 'ok' : 'muted']">健康分 <b>{{ slotReuseScore(slot) }}</b></span>
          <span class="scheduler-chip">复用 <b>{{ slot.session_success_count ?? 0 }}</b></span>
          <span class="scheduler-chip">下次 <b>{{ slotNextEta(slot) }}</b></span>
          <span :class="['scheduler-chip', poolTone(slot)]">{{ slotPoolText(slot) }}</span>
        </div>
        <div class="slot-card-body">
          <div class="slot-mini-meta">
            <span>当前阶段<b>{{ slotPhaseText(slot) }}</b></span>
            <span>实时页面<b>{{ slotLivePageText(slot) }}</b></span>
            <span>查询状态<b>{{ slotQueryText(slot) }}</b></span>
            <span>快照对应<b>{{ slotSnapshotPageText(slot) }}</b></span>
            <span>运行时长<b>{{ slotElapsed(slot) }}s</b></span>
            <span>最后心跳<b>{{ heartbeat(slot.updated_at) }}</b></span>
            <span>代理<b>{{ slot.proxy_display || '-' }}</b></span>
            <span class="wide">查询结果<b>{{ slotResultText(slot) }}</b></span>
          </div>
          <button class="snapshot-thumb" @click.stop="openModal('slot', slot)">
            <img v-if="slotImage(slot)" :src="slotImage(slot)" :alt="slot.slot" />
            <span v-else>{{ slotPlaceholder(slot) }}</span><em>实时快照</em>
          </button>
        </div>
        <footer>
          <button @click.stop="openModal('slot', slot)">打开详情</button>
          <button @click.stop="store.slotCommand(slot.slot, 'snapshot')">截图</button>
          <button @click.stop="store.slotCommand(slot.slot, 'reload')">重试</button>
        </footer>
      </article>
    </section>

    <section class="realtime-event-strip console-card">
      <header class="console-card-head compact">
        <div>
          <h2>关键事件流 · {{ displaySlot(selectedSlotId || focusedSlot?.slot || 'slot') }}</h2>
          <p>实时跟随当前槽位；可切换全部轮次或第 N 轮查看完整链路。</p>
        </div>
        <div class="event-controls">
          <select v-model="selectedRoundKey">
            <option value="latest">最新轮次</option>
            <option value="all">全部轮次</option>
            <option v-for="round in roundOptions" :key="round.key" :value="round.key">{{ round.label }}</option>
          </select>
          <button class="mini-btn primary" @click="openModal('events')">查看全部事件</button>
        </div>
      </header>
      <div class="event-timeline-line">
        <button v-for="event in timelineEvents" :key="event.id" :class="['timeline-pill', event.tone]" @click="openModal('event', event.raw)">
          <i>{{ event.screenshot ? '📷' : event.icon }}</i><time>{{ event.time }}</time><b>{{ event.title }}</b><span>{{ event.desc }}</span>
        </button>
        <div v-if="!timelineEvents.length" class="empty-events">该槽位暂无事件；启动后会按轮次实时记录。</div>
      </div>
    </section>

    <div v-if="modal" class="os-modal-backdrop" @click.self="closeModal">
      <section :class="['os-modal', modal.kind === 'event' ? 'narrow' : '']">
        <header class="os-modal-head">
          <div><h2>{{ modalTitle }}</h2><p>{{ modalSubtitle }}</p></div>
          <button @click="closeModal">×</button>
        </header>

        <div v-if="modal.kind === 'slot'" class="slot-modal-grid">
          <div class="slot-modal-main">
            <div class="focus-detail-grid three">
              <span>槽位<b>{{ displaySlot(modal.payload?.slot || '-') }}</b></span>
              <span>阶段<b>{{ slotPhaseText(modal.payload) }}</b></span>
              <span>实时页面<b>{{ slotLivePageText(modal.payload) }}</b></span>
              <span>查询状态<b>{{ slotQueryText(modal.payload) }}</b></span>
              <span>快照对应<b>{{ slotSnapshotPageText(modal.payload) }}</b></span>
              <span>轮次<b>{{ modal.payload?.round ?? modal.payload?.live_round ?? '-' }}</b></span>
              <span>心跳<b>{{ heartbeat(modal.payload?.updated_at) }}</b></span>
              <span>代理<b>{{ modal.payload?.proxy_display || '-' }}</b></span>
              <span>等待池<b>{{ modal.payload?.waiting_acquired ? '占用中' : '未占用' }}</b></span>
              <span>调度池<b>{{ slotPoolText(modal.payload) }}</b></span>
              <span>健康分<b>{{ slotReuseScore(modal.payload) }}</b></span>
              <span>下次可查<b>{{ slotNextEta(modal.payload) }}</b></span>
              <span>复用次数<b>{{ modal.payload?.session_success_count ?? 0 }}</b></span>
              <span>失败次数<b>{{ modal.payload?.session_failure_count ?? 0 }}</b></span>
              <span>最近失败<b>{{ failureKindText(modal.payload?.recent_failure_kind) }}</b></span>
            </div>
            <div class="modal-text-block scheduler-detail"><b>调度决策 / 不黑箱说明</b><p>{{ modal.payload?.scheduler_reason || modal.payload?.scheduler_blocked_reason || '暂无调度说明' }}</p></div>
            <div class="modal-text-block"><b>最新原因 / 结果</b><p>{{ modal.payload?.last_reason_zh || modal.payload?.last_reason || slotResultText(modal.payload) }}</p></div>
            <div class="modal-action-row">
              <button @click="modal.payload && store.slotCommand(modal.payload.slot, 'snapshot')">截图</button>
              <button @click="modal.payload && store.slotCommand(modal.payload.slot, 'reload')">重试</button>
              <button class="danger-lite" @click="modal.payload && store.slotCommand(modal.payload.slot, 'kill')">重开槽位</button>
            </div>
          </div>
          <a v-if="slotImage(modal.payload)" class="modal-snapshot" :href="slotImage(modal.payload)" target="_blank">
            <img :src="slotImage(modal.payload)" /><span>打开实时快照 / 失败页截图</span>
          </a>
          <div v-else class="modal-snapshot empty">暂无快照</div>
        </div>

        <div v-else-if="modal.kind === 'date'" class="date-modal-list">
          <div class="date-modal-summary"><b>{{ modal.payload?.date }}</b><span>{{ modal.payload?.label }} · {{ modal.payload?.weekday }}</span></div>
          <div class="time-slot-grid">
            <span v-for="time in modal.payload?.times || []" :key="time" :class="modal.payload?.tone">{{ time }}</span>
            <p v-if="!(modal.payload?.times || []).length" class="muted">该日期暂未展开到具体时间段。</p>
          </div>
        </div>

        <div v-else-if="modal.kind === 'dates'" class="date-modal-list">
          <button v-for="date in dateRows" :key="date.date" :class="['date-row-button', date.tone]" @click="openModal('date', date)">
            <b>{{ date.date }}</b><span>{{ date.weekday }}</span><em>{{ date.label }}</em><strong>{{ date.timeCount }} 个时段</strong>
          </button>
        </div>

        <div v-else-if="modal.kind === 'events'" class="events-modal-body">
          <div class="event-modal-toolbar">
            <span>槽位：<b>{{ displaySlot(selectedSlotId || '-') }}</b></span>
            <select v-model="selectedRoundKey">
              <option value="latest">最新轮次</option><option value="all">全部轮次</option>
              <option v-for="round in roundOptions" :key="round.key" :value="round.key">{{ round.label }}</option>
            </select>
            <a :href="`${API_BASE}/storage/logs/events.jsonl`" target="_blank">打开 events.jsonl</a>
          </div>
          <div class="event-list-full">
            <button v-for="event in fullEventItems" :key="event.id" :class="['event-row-full', event.tone]" @click="openModal('event', event.raw)">
              <time>{{ event.dateTime }}</time><b>{{ event.title }}</b><span>{{ event.desc }}</span><em>{{ event.roundLabel }}</em>
            </button>
            <p v-if="!fullEventItems.length" class="muted">当前筛选条件下没有事件。</p>
          </div>
        </div>

        <div v-else-if="modal.kind === 'event'" class="event-detail-body">
          <div :class="['event-detail-summary', toEventItem(modal.payload).tone]">
            <b>{{ toEventItem(modal.payload).title }}</b><span>{{ toEventItem(modal.payload).dateTime }}</span><p>{{ toEventItem(modal.payload).desc }}</p>
          </div>
          <a v-if="toEventItem(modal.payload).screenshot" class="event-image-preview" :href="toEventItem(modal.payload).screenshot" target="_blank">
            <img :src="toEventItem(modal.payload).screenshot" />
            <span>点击打开阶段快照</span>
          </a>
          <div class="quick-links" v-if="eventArtifacts(modal.payload).length">
            <a v-for="artifact in eventArtifacts(modal.payload)" :key="artifact.href" :href="artifact.href" target="_blank">{{ artifact.label }}</a>
          </div>
          <pre>{{ prettyJson(modal.payload) }}</pre>
        </div>
      </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { API_BASE } from '../../api/request'
import { useSystemStore } from '../../store/system'
import type { SlotStatus } from '../../types/system'

type ModalKind = 'slot' | 'date' | 'dates' | 'events' | 'event'
interface ModalState { kind: ModalKind; payload?: any }
interface DateRow { date: string; short: string; weekday: string; label: string; tone: string; timeCount: number; times: string[]; hit: boolean }
interface EventItem { id: string; raw: any; time: string; dateTime: string; title: string; desc: string; icon: string; tone: string; roundKey: string; roundLabel: string; screenshot?: string }

const store = useSystemStore()
const selectedRoundKey = ref('latest')
const selectedTicketKey = ref('latest')
const modal = ref<ModalState | null>(null)
const nowMs = ref(Date.now())
let ticker: number | undefined
const status = computed(() => store.status)
const slots = computed<SlotStatus[]>(() => status.value?.slots || [])
const focusedSlot = computed<SlotStatus | undefined>(() => slots.value.find((s) => s.slot === store.selectedSlot) || slots.value.find((s) => s.last_reason || s.recovery_component || /cf|waiting|login|business|query/.test(String(s.stage || ''))) || slots.value[0])
const selectedSlotId = computed(() => store.selectedSlot || focusedSlot.value?.slot || slots.value[0]?.slot || '')
const latestTime = computed(() => status.value?.ts ? new Date(status.value.ts).toLocaleTimeString('zh-CN', { hour12: false }) : '-')

onMounted(() => { ticker = window.setInterval(() => { nowMs.value = Date.now() }, 500) })
onUnmounted(() => { if (ticker) window.clearInterval(ticker) })
const targetRange = computed(() => `${status.value?.target?.start_date || 'now'} ~ ${status.value?.target?.cutoff_date || status.value?.target?.end_date || '-'}`)
const eventsChrono = computed<any[]>(() => [...(status.value?.events || [])].sort((a, b) => eventMs(a) - eventMs(b)))
const slotEventsChrono = computed(() => eventsChrono.value.filter((ev) => !selectedSlotId.value || eventSlot(ev) === selectedSlotId.value))
const ticketHistory = computed<any[]>(() => {
  const rows = [...(status.value?.ticket_history || [])]
    .filter((x: any) => Array.isArray(x?.days) || Array.isArray(x?.matched_slots) || Array.isArray(x?.slots))
    .sort((a: any, b: any) => new Date(b?.queried_at || b?.ts || 0).getTime() - new Date(a?.queried_at || a?.ts || 0).getTime())
  const latest = status.value?.latest_ticket || {}
  if (!rows.length && (Array.isArray(latest.days) || Array.isArray(latest.matched_slots) || Array.isArray(latest.slots))) return [latest]
  return rows
})
const ticketOptions = computed(() => ticketHistory.value.map((ticket, idx) => ({ key: ticketKey(ticket, idx), label: ticketLabel(ticket, idx) })))
const selectedTicket = computed(() => {
  if (selectedTicketKey.value === 'latest') return ticketHistory.value[0] || status.value?.latest_ticket || {}
  return ticketHistory.value.find((ticket, idx) => ticketKey(ticket, idx) === selectedTicketKey.value) || ticketHistory.value[0] || status.value?.latest_ticket || {}
})
const selectedTicketLabel = computed(() => selectedTicket.value ? ticketLabel(selectedTicket.value, Math.max(0, ticketHistory.value.findIndex((x, i) => ticketKey(x, i) === ticketKey(selectedTicket.value, i)))) : '等待查询')
const selectedTicketTime = computed(() => formatDateTime(selectedTicket.value?.queried_at || selectedTicket.value?.ts || ''))

const dateRows = computed<DateRow[]>(() => buildDateRows())
const visibleDateRows = computed(() => dateRows.value.slice(0, 6))
const dateStats = computed(() => ({ days: dateRows.value.filter((d) => d.tone !== 'placeholder').length, times: dateRows.value.reduce((n, d) => n + d.timeCount, 0), hits: dateRows.value.filter((d) => d.hit).length }))
const kpiCards = computed(() => {
  const total = status.value?.slot_policy?.total_slots || slots.value.length || 0
  const active = slots.value.filter((s) => !s.stale && !/stopped|idle|停止/.test(`${s.state || ''}${s.stage || ''}`.toLowerCase())).length
  const ticket = selectedTicket.value || {}
  const hit = Number(ticket.target_hit ? 1 : 0) + (Array.isArray(ticket.matched_slots) ? ticket.matched_slots.length : 0)
  const recoveries = countEvents(/recover|recovery|恢复|1015|429/i)
  const manual = slots.value.filter((s) => /manual|人工|failed|失败|卡住|1020/.test(`${s.last_reason || ''}${s.last_reason_zh || ''}`)).length
  const errors = countEvents(/error|failed|1015|1020|429|denied|timeout|失败|错误/i)
  const queryCount = status.value?.ticket_query_count_today ?? ticketHistory.value.filter((x) => String(x?.queried_at || x?.ts || '').slice(0, 10) === new Date().toISOString().slice(0, 10)).length
  return [
    { key: 'active', label: '当前有效会话数', value: `${active} / ${total}`, hint: active ? '实时连接' : '等待启动', icon: '♙', tone: 'blue', trendTone: active ? 'up' : 'flat' },
    { key: 'query', label: '今日查询次数', value: `${queryCount} 次`, hint: '票池成功写入', icon: '⌕', tone: 'blue', trendTone: 'flat' },
    { key: 'hit', label: '命中目标次数', value: `${hit} 次`, hint: hit ? '可提交' : '未命中', icon: '◎', tone: 'red', trendTone: hit ? 'up' : 'flat' },
    { key: 'days', label: '当前可用日期数', value: `${dateStats.value.days} 个`, hint: '日期优先', icon: '▣', tone: 'blue', trendTone: dateStats.value.days ? 'up' : 'flat' },
    { key: 'times', label: '当前可用时间段数', value: `${dateStats.value.times} 个`, hint: '点击展开', icon: '◷', tone: 'blue', trendTone: dateStats.value.times ? 'up' : 'flat' },
    { key: 'recovery', label: '自动恢复次数', value: `${recoveries} 次`, hint: recoveries ? '组件接管' : '无恢复', icon: '⟳', tone: 'blue', trendTone: recoveries ? 'up' : 'flat' },
    { key: 'manual', label: '待人工处理数', value: `${manual} 个`, hint: manual ? '需关注' : '正常', icon: '♟', tone: 'orange', trendTone: manual ? 'warn' : 'flat' },
    { key: 'error', label: '最近错误数', value: `${errors} 个`, hint: errors ? '可查详情' : '无错误', icon: '△', tone: 'red', trendTone: errors ? 'warn' : 'flat' }
  ]
})

const focusedReason = computed(() => focusedSlot.value?.last_reason_zh || focusedSlot.value?.last_reason || '暂无失败原因；继续观察当前阶段和实时快照。')
const diagnoseTone = computed(() => slotTone(focusedSlot.value))
const diagnoseIcon = computed(() => diagnoseTone.value === 'bad' ? '!' : diagnoseTone.value === 'warn' ? '⟳' : diagnoseTone.value === 'good' ? '✓' : 'i')
const officialError = computed(() => { for (const ev of [...slotEventsChrono.value].reverse()) { const found = findOfficialError(ev); if (found) return found } return null as any })
const diagnoseConclusion = computed(() => {
  const slot = focusedSlot.value
  if (!slot) return '等待槽位启动。'
  const text = `${slot.stage || ''} ${slot.live_page_stage || ''} ${slot.live_page_stage_zh || ''} ${slot.live_page_reason || ''} ${slot.live_page_title || ''} ${slot.last_reason_zh || ''} ${slot.last_reason || ''} ${officialError.value?.code || ''}`.toLowerCase()
  if (officialError.value?.code === '1015') return '官方返回 Cloudflare 1015 速率限制：已调用恢复组件换代理/新画像，不在原页面硬刷新。'
  if (/1020|denied/.test(text)) return '官方拒绝访问或校验失败：请查看失败快照、代理画像和恢复动作。'
  if (/cf|人机|challenge/.test(text)) return 'CF / 人机校验处理中；出现可交互挑战时由 CDP 组件点击接管。'
  if (/waiting|等待室|等候/.test(text)) return slot.waiting_acquired ? '该槽位正在按策略占用等待室，超过阈值会自动退出。' : '该槽位经过等待室或未占用等待池。'
  if (/login|登录|idp|b2c/.test(text)) return '正在登录或处理随机密保；失败会保留页面证据。'
  if (/business|query|查|schedule/.test(text)) return '正在查询日期与时间段，命中截止日前目标会触发抢票。'
  return /失败|failed|error|timeout/.test(text) ? '该槽位存在失败，已记录关键事件、官方反馈和截图。' : '槽位运行中，暂无需要人工处理的异常。'
})
const flowSteps = computed(() => {
  const stage = focusedSlot.value?.stage || focusedSlot.value?.last_live_stage || ''
  const order = [['proxy_acquire', '初始化', '✓'], ['cf_gate', '打开页面', '✓'], ['waiting_room', '等待室', '⌛'], ['login', '登录', '♙'], ['business_query', '进入查询', '⌕'], ['booking_submit', '提交', '⚡'], ['recovery', '恢复', '⟳']] as const
  let active = order.findIndex(([k]) => stage.includes(k)); if (active < 0 && officialError.value) active = 6; if (active < 0) active = 0
  return order.map(([key, label, icon], i) => ({ key, label, icon, state: i < active ? 'done' : i === active ? slotTone(focusedSlot.value) : 'todo' }))
})
const roundOptions = computed(() => {
  const map = new Map<string, { key: string; label: string; lastMs: number }>()
  for (const ev of slotEventsChrono.value) { const key = eventRoundKey(ev); if (key === 'unknown') continue; map.set(key, { key, label: roundLabel(key), lastMs: Math.max(map.get(key)?.lastMs || 0, eventMs(ev)) }) }
  return [...map.values()].sort((a, b) => b.lastMs - a.lastMs)
})
const effectiveRoundKey = computed(() => selectedRoundKey.value === 'latest' ? (roundOptions.value[0]?.key || 'all') : selectedRoundKey.value)
const selectedRoundEvents = computed(() => effectiveRoundKey.value === 'all' ? slotEventsChrono.value : slotEventsChrono.value.filter((ev) => eventRoundKey(ev) === effectiveRoundKey.value))
const semanticRoundEvents = computed(() => compactSemanticEvents(selectedRoundEvents.value))
const timelineEvents = computed(() => semanticRoundEvents.value.map(toEventItem).slice(-9))
const fullEventItems = computed(() => semanticRoundEvents.value.map(toEventItem))
const modalTitle = computed(() => modal.value?.kind === 'slot' ? `${displaySlot(modal.value.payload?.slot || selectedSlotId.value)} 槽位详情` : modal.value?.kind === 'date' ? `${modal.value.payload?.date || ''} 时间段详情` : modal.value?.kind === 'dates' ? '全部日期' : modal.value?.kind === 'events' ? `${displaySlot(selectedSlotId.value)} 全部关键事件` : '事件详情')
const modalSubtitle = computed(() => modal.value?.kind === 'events' ? '可切换最新轮次 / 全部轮次 / 指定第 N 轮，点击事件查看原始 payload。' : modal.value?.kind === 'date' ? '默认首页只显示日期，具体时间段在此展开。' : modal.value?.kind === 'slot' ? '包含当前阶段、代理、快照、失败原因和可执行动作。' : '完整证据用于排错，不在首页默认展示。')

watch(selectedSlotId, () => { selectedRoundKey.value = 'latest' })
watch(roundOptions, () => { if (!['latest', 'all'].includes(selectedRoundKey.value) && !roundOptions.value.some((r) => r.key === selectedRoundKey.value)) selectedRoundKey.value = 'latest' })
watch(ticketOptions, () => { if (selectedTicketKey.value !== 'latest' && !ticketOptions.value.some((x) => x.key === selectedTicketKey.value)) selectedTicketKey.value = 'latest' })
function selectSlot(slot: string) { store.selectedSlot = slot }
function openModal(kind: ModalKind, payload?: any) { modal.value = { kind, payload } }
function closeModal() { modal.value = null }
function countEvents(pattern: RegExp) { return eventsChrono.value.filter((ev) => pattern.test(`${ev.event_type || ''} ${JSON.stringify(ev.payload || {})}`)).length }
function buildDateRows(): DateRow[] {
  const ticket = selectedTicket.value || {}, rows = new Map<string, DateRow>(), matched = Array.isArray(ticket.matched_slots) ? ticket.matched_slots : [], slotsRaw = Array.isArray(ticket.slots) ? ticket.slots : []
  const hitDates = new Set<string>(matched.map((x: any) => normalizeDate(String(x?.date || x?.date_raw || ''))).filter(Boolean))
  for (const raw of Array.isArray(ticket.days) ? ticket.days : []) addDate(rows, normalizeDate(String(raw)), [], hitDates.has(normalizeDate(String(raw))))
  for (const item of [...matched, ...slotsRaw]) { const date = normalizeDate(String(item?.date || item?.date_raw || item?.day || '')); const time = String(item?.time || item?.time_raw || item?.slot || item?.start_time || '').trim(); addDate(rows, date, time ? [time] : [], hitDates.has(date) || Boolean(item?.target_hit || item?.matched)) }
  const out = [...rows.values()].sort((a, b) => a.date.localeCompare(b.date))
  return out.length ? out : [{ date: status.value?.target?.cutoff_date || '2026-06-22', short: shortDate(status.value?.target?.cutoff_date || '2026-06-22'), weekday: weekday(status.value?.target?.cutoff_date || '2026-06-22'), label: '等待查询', tone: 'placeholder', timeCount: 0, times: [], hit: false }]
}
function addDate(rows: Map<string, DateRow>, date: string, times: string[], hit: boolean) {
  if (!date) return; const old = rows.get(date), nextTimes = Array.from(new Set([...(old?.times || []), ...times])).filter(Boolean), nextHit = Boolean(old?.hit || hit)
  rows.set(date, { date, short: shortDate(date), weekday: weekday(date), label: nextHit ? '命中目标' : nextTimes.length ? '可预约' : '仅发现日期', tone: nextHit ? 'hit' : nextTimes.length ? 'ok' : 'warn', timeCount: nextTimes.length, times: nextTimes, hit: nextHit })
}
function normalizeDate(v: string) { return v.match(/\d{4}-\d{2}-\d{2}/)?.[0] || '' }
function shortDate(v: string) { return normalizeDate(v).slice(5) || v || '-' }
function weekday(v: string) { const d = new Date(`${normalizeDate(v)}T00:00:00`); return Number.isNaN(d.getTime()) ? '-' : d.toLocaleDateString('zh-CN', { weekday: 'short' }) }
function heartbeat(ts?: string) { if (!ts) return '-'; const d = new Date(ts); return Number.isNaN(d.getTime()) ? '-' : d.toLocaleTimeString('zh-CN', { hour12: false }) }
function formatDateTime(ts?: string) { if (!ts) return '-'; const d = new Date(ts); return Number.isNaN(d.getTime()) ? '-' : d.toLocaleString('zh-CN', { hour12: false }) }
function ticketKey(ticket: any, idx: number) { return String(ticket?.ticket_query_id || `${ticket?.slot_id || 'ticket'}-${ticket?.round_id || ticket?.round || 'round'}-${ticket?.live_round || idx}-${ticket?.queried_at || ticket?.ts || idx}`) }
function ticketLabel(ticket: any, idx: number) {
  const n = ticketHistory.value.length - idx
  const slot = ticket?.slot_id ? displaySlot(String(ticket.slot_id)) : '票池'
  const round = ticket?.round_id ? roundLabel(String(ticket.round_id)) : ticket?.live_round ? `业务第 ${ticket.live_round} 轮` : `第 ${n} 次`
  const count = Array.isArray(ticket?.days) ? ticket.days.length : 0
  return `${slot} ${round} · ${count} 日期 · ${formatDateTime(ticket?.queried_at || ticket?.ts)}`
}
function displaySlot(slot: string) { return slot ? slot.replace('slot_', 'Slot ') : '-' }
function eventMs(ev: any) { return new Date(ev?.created_at || ev?.ts || 0).getTime() || 0 }
function eventSlot(ev: any) { return String(ev?.slot_id || ev?.slot || ev?.payload?.slot || '') }
function eventRoundKey(ev: any) { const v = ev?.round_id ?? ev?.round ?? ev?.payload?.round_id ?? ev?.payload?.round ?? ev?.payload?.round_no ?? ev?.payload?.payload?.round; return v === undefined || v === null || v === '' ? 'unknown' : String(v).startsWith('round_') ? String(v) : `round_${String(v).padStart(4, '0')}` }
function roundLabel(key: string) { return key === 'unknown' ? '未知轮次' : `第 ${Number(key.replace(/\D/g, '')) || key} 轮` }
function compactSemanticEvents(events: any[]) {
  const out: any[] = [], seen = new Set<string>()
  const shots: { stage: string; ts: number; href: string }[] = []
  for (const ev of events) {
    const t = String(ev?.event_type || ''), p = ev?.payload || {}, stage = String(p.stage || '')
    const shot = typeof p.screenshot === 'string' ? p.screenshot : ''
    // live_snapshot is intentionally not attached to historical events:
    // it is overwritten every 10s. Event-chain screenshots must be immutable
    // stage/failure evidence; the current live image stays in the slot panel.
    if (shot && t === 'stage_final_snapshot') shots.push({ stage, ts: eventMs(ev), href: shot })
    if (shot && t === 'round_close_snapshot') shots.push({ stage: 'round_finish', ts: eventMs(ev), href: shot })
  }
  for (const ev of events) {
    if (!isSemanticEvent(ev)) continue
    const p = ev?.payload || {}, key = `${ev.event_type}|${p.stage || ev.stage || ''}|${p.message || p.reason || p.stage_zh || ''}`
    if (seen.has(key) && ev.event_type === 'stage_enter') continue
    seen.add(key)
    const stage = String(p.stage || ev.stage || '')
    const wanted = ev.event_type === 'stage_exit' ? nearestShot(shots, stage, eventMs(ev)) : ev.event_type === 'round_finish' ? nearestShot(shots, 'round_finish', eventMs(ev)) : ''
    out.push(wanted && !firstImageHref(ev) ? { ...ev, payload: { ...p, screenshot: wanted } } : ev)
  }
  return out
}
function nearestShot(shots: { stage: string; ts: number; href: string }[], stage: string, ts: number) {
  if (!ts) return ''
  let best = { delta: Number.POSITIVE_INFINITY, href: '' }
  for (const shot of shots) {
    const delta = Math.abs(shot.ts - ts)
    if (shot.stage === stage && delta <= 5000 && delta < best.delta) best = { delta, href: shot.href }
  }
  return best.href
}
function isSemanticEvent(ev: any) {
  const t = String(ev?.event_type || ''), p = ev?.payload || {}, stage = String(p.stage || ev?.stage || '')
  if (['live_snapshot', 'stage_final_snapshot', 'round_close_snapshot'].includes(t)) return false
  if (t === 'stage_enter') return ['cf_gate', 'waiting_room', 'login', 'business_query', 'booking_submit'].includes(stage)
  if (t === 'stage_exit') return ['proxy_acquire', 'cf_gate', 'waiting_room', 'login', 'business_query', 'booking_submit'].includes(stage)
  if (['round_start', 'round_finish', 'browser_launched', 'recovery_attempt'].includes(t)) return true
  if (t.startsWith('business_')) {
    if (['business_navigation_start', 'business_post_selecting', 'business_dates_collecting', 'business_slot_collecting', 'business_retry_home'].includes(t)) return false
    if (t === 'business_manage_clicked' && p.clicked === false) return false
    return true
  }
  return false
}
function slotPhaseText(slot?: SlotStatus) {
  if (!slot) return '-'
  const live = slotLivePageText(slot)
  const logic = slot.stage_zh || slot.stage || '-'
  if (live && live !== '-' && live !== logic) return `${logic} / ${live}`
  return logic
}
function slotLivePageText(slot?: SlotStatus) {
  if (!slot) return '-'
  if (slot.page_status_zh) return slot.page_status_zh
  if (slot.live_page_stage_zh) return slot.live_page_stage_zh
  const page = String(slot.live_page_stage || '')
  return ({
    cf_challenge: '人机验证/CF',
    waiting_room: '等待室',
    login: '登录页',
    security_questions: '密保页',
    idp_loading: '登录回跳中',
    home: '首页',
    schedule: '预约查询页',
    rate_limit_1015: '1015 限流',
    rate_limit_429: '429 限流',
    access_denied: '拉黑/拒绝访问',
    network_error: '网络/代理错误',
    login_failed: '登录失败页',
    blank: '空白页',
    site: '官网页面'
  } as Record<string, string>)[page] || '-'
}
function slotQueryText(slot?: SlotStatus) {
  if (!slot) return '-'
  if (slot.dispatcher_candidate_role === 'primary') return '查询状态（主候选）'
  if (String(slot.dispatcher_candidate_role || '').startsWith('backup')) return '查询状态（候补中）'
  if (slot.scheduler_status_zh) return slot.scheduler_status_zh
  if (slot.query_status_zh) return slot.query_status_zh
  const queryState = String(slot.smart_query_state || '')
  const waitReason = String(slot.smart_query_wait_reason || '')
  const hay = `${slot.stage || ''} ${slot.stage_zh || ''} ${slot.last_reason || ''} ${slot.last_reason_zh || ''} ${slot.live_page_stage || ''} ${slot.live_page_reason || ''}`.toLowerCase()
  if (queryState === 'api_querying' || queryState === 'querying' || /smart_query_reserved/.test(hay)) return '查询状态（使用中）'
  if (queryState === 'planned') return '查询状态（拟使用中）'
  if (queryState === 'cooling' || waitReason === 'session_cooldown' || waitReason === 'failure_cooldown' || waitReason === 'rate_limit_cooldown') return '查询状态（冷却中）'
  if (queryState === 'recovering') return '查询状态（恢复中）'
  if (queryState === 'waiting' || waitReason === 'global_success_gap' || waitReason === 'business_api_gate_busy' || /smart_query_wait/.test(hay)) return '查询状态（等候中）'
  const unhealthyPage = /cf_challenge|login|security_questions|idp_loading|waiting_room|rate_limit_1015|rate_limit_429|access_denied|network_error|blank/.test(String(slot.live_page_stage || ''))
  if (unhealthyPage) return '查询状态（未就绪）'
  if (queryState === 'preflight_blocked' || waitReason === 'page_unhealthy') return '查询状态（等候中）'
  if (/business|query|查/.test(hay)) return '查询状态（准备中）'
  return slot.state === 'pending' ? '查询状态（未启动）' : '查询状态（等候中）'
}
function slotPoolText(slot?: SlotStatus) {
  const role = String(slot?.pool_role || '')
  return ({
    hot_query: '热查询池',
    login_standby: '登录待命',
    candidate: '候选生产',
    recovering: '恢复池',
    terminal: '需重置',
    query_cooling: '冷却中'
  } as Record<string, string>)[role] || (slot?.query_eligible ? '可查询' : '未就绪')
}
function poolTone(slot?: SlotStatus) {
  const role = String(slot?.pool_role || '')
  if (slot?.query_eligible || role === 'hot_query') return 'ok'
  if (role === 'terminal') return 'bad'
  if (role === 'recovering') return 'warn'
  return 'muted'
}
function slotReuseScore(slot?: SlotStatus) {
  const n = Number(slot?.reuse_score ?? slot?.session_health_score)
  return Number.isFinite(n) ? String(Math.round(n)) : '-'
}
function slotNextEta(slot?: SlotStatus) {
  const n = Number(slot?.next_query_eta_seconds)
  if (!Number.isFinite(n) || n <= 0) return slot?.query_eligible ? '现在' : '-'
  if (n < 60) return `${Math.round(n)}s`
  return `${Math.floor(n / 60)}m${Math.round(n % 60)}s`
}
function failureKindText(kind?: string) {
  const k = String(kind || '')
  return ({
    none: '无',
    rate_limited: '429/限流',
    failed_to_fetch: 'fetch失败',
    auth_or_cf: '登录/CF拦截',
    page_view_blocked: '页面访问被拦截',
    terminal: '硬错误'
  } as Record<string, string>)[k] || k || '-'
}
function slotSnapshotPageText(slot?: SlotStatus) {
  if (!slot) return '-'
  if (slot.live_snapshot_page_stage_zh) return slot.live_snapshot_page_stage_zh
  const snap = String(slot.live_snapshot_page_stage || '')
  if (snap) return ({
    cf_challenge: '人机验证/CF',
    waiting_room: '等待室',
    login: '登录页',
    security_questions: '密保页',
    idp_loading: '登录回跳中',
    home: '首页',
    schedule: '预约查询页',
    rate_limit_1015: '1015 限流',
    rate_limit_429: '429 限流',
    access_denied: '拉黑/拒绝访问',
    network_error: '网络/代理错误',
    login_failed: '登录失败页',
    blank: '空白页',
    site: '官网页面'
  } as Record<string, string>)[snap] || snap
  return slotImage(slot) ? slotLivePageText(slot) : '-'
}
function slotRealtimeText(slot?: SlotStatus) {
  if (!slot) return '等待'
  if (slot.dispatcher_candidate_role === 'primary') return '查询主候选'
  if (String(slot.dispatcher_candidate_role || '').startsWith('backup')) return `查询候补${String(slot.dispatcher_candidate_role).replace('backup_', '')}`
  if (slot.realtime_status_zh) return slot.realtime_status_zh
  const page = String(slot.live_page_stage || '')
  const queryState = String(slot.smart_query_state || '')
  const waitReason = String(slot.smart_query_wait_reason || '')
  const hay = `${slot.stage || ''} ${slot.stage_zh || ''} ${slot.last_reason || ''} ${slot.last_reason_zh || ''} ${slot.recovery_error_type || ''} ${slot.recovery_action || ''} ${slot.live_page_reason || ''} ${slot.live_page_title || ''}`.toLowerCase()
  const pageText = slotLivePageText(slot)
  const queryText = slotQueryText(slot)
  if (queryText && !['-', '查询状态（未启动）'].includes(queryText)) return `${pageText && pageText !== '-' ? pageText : '页面状态'} / ${queryText}`
  if (page === 'rate_limit_1015' || /1015/.test(hay)) return '1015 状态'
  if (page === 'access_denied' || /1020|access_denied|access denied|blocked|拉黑/.test(hay)) return '拉黑/拒绝'
  if (/429|rate_limit_429|too many requests/.test(hay)) return '429 状态'
  if (queryState === 'api_querying' || queryState === 'querying') return '查询使用中'
  if (queryState === 'cooling') return '查询冷却中'
  if (queryState === 'recovering') return '恢复中'
  if (page === 'cf_challenge' || /cf|人机|challenge|turnstile/.test(hay)) return '人机验证'
  if (page === 'waiting_room' || /waiting room|等待室|等候/.test(hay)) return slot.waiting_acquired ? '等待室中' : '等待室'
  if (page === 'login' || page === 'security_questions' || page === 'idp_loading' || /login|登录|密保|b2c|idp/.test(hay)) return page === 'security_questions' ? '密保中' : '登录状态'
  if (page === 'network_error' || /network|proxy|err_/.test(hay)) return '页面错误'
  if (waitReason === 'session_cooldown') return '拟使用中'
  if (waitReason === 'global_success_gap' || waitReason === 'business_api_gate_busy') return '查询等待中'
  if (/smart_query_wait/.test(hay)) return '查询冷却中'
  if (page === 'home') return '首页状态'
  if (page === 'schedule') return '预约页状态'
  if (/business|query|查/.test(hay)) return '查询使用中'
  if (/booking|提交/.test(hay)) return '提交中'
  if (/失败|failed|timeout|error|denied/.test(hay)) return '页面错误'
  return slot.state === 'pending' ? '等待启动' : '运行中'
}
function slotTone(slot?: SlotStatus) {
  if (!slot) return 'idle'
  const text = `${slotRealtimeText(slot)} ${slotQueryText(slot)} ${slot.stage || ''} ${slot.state || ''} ${slot.last_reason || ''} ${slot.last_reason_zh || ''} ${slot.recovery_error_type || ''} ${slot.live_page_stage || ''}`.toLowerCase()
  if (/失败|failed|error|denied|timeout|卡住|1015|1020|429|拉黑|页面错误/.test(text)) return 'bad'
  if (/查询使用中|查询状态（使用中）|api_querying/.test(text)) return 'good'
  if (/主候选|候补|拟使用/.test(text)) return 'info'
  if (/waiting|等待|cf|人机|recover|恢复|login|challenge|密保|冷却|等候/.test(text) || slot.waiting_acquired) return 'warn'
  if (/business|query|查|booking|home|schedule|done|ok|首页|预约/.test(text)) return 'info'
  return 'info'
}
function badgeTone(slot?: SlotStatus) { const t = slotTone(slot); return t === 'good' ? 'success' : t === 'bad' ? 'danger' : t === 'warn' ? 'warning' : 'soft' }
function slotBadge(slot?: SlotStatus) { return slotRealtimeText(slot) }
function slotElapsed(slot?: SlotStatus) {
  if (!slot) return '0.0'
  const startMs = slot.round_started_at ? new Date(slot.round_started_at).getTime() : 0
  if (slot.state === 'running' && startMs && !Number.isNaN(startMs)) return (Math.max(0, nowMs.value - startMs) / 1000).toFixed(1)
  const n = Number(slot.elapsed_s ?? 0)
  return Number.isFinite(n) ? n.toFixed(1) : '0.0'
}
function slotResultText(slot?: SlotStatus) { if (!slot) return '-'; return slot.target_hit ? `命中目标；日期 ${slot.availability_days ?? '-'} / 时间段 ${slot.availability_slots ?? '-'}` : (slot.availability_days || slot.availability_slots) ? `发现 ${slot.availability_days || 0} 个日期 / ${slot.availability_slots || 0} 个时段` : slot.last_reason_zh || slot.last_reason || '等待查询结果' }
function slotPlaceholder(slot?: SlotStatus) { const page = slotLivePageText(slot); return page && page !== '-' ? page : slot?.stage === 'cf_gate' ? 'CF 页面' : slot?.stage === 'login' ? '登录页面' : slot?.stage === 'business_query' ? '查票页面' : '暂无快照' }
function slotImage(slot?: SlotStatus) { if (!slot || (!slot.live_snapshot && !slot.live_snapshot_stage)) return ''; const stamp = encodeURIComponent(`${slot.updated_at || ''}_${slot.live_snapshot_observed_at || ''}_${slot.live_snapshot_stage || ''}_${slot.live_snapshot_reason || ''}_${slot.live_snapshot_page_stage || ''}_${slot.live_page_stage || ''}_${slot.live_page_reason || ''}`); const path = slot.live_snapshot || `storage/live_snapshots/${slot.slot}.png`; return `${path.startsWith('http') ? path : `${API_BASE}/${path.replace(/^\/+/, '')}`}?v=${stamp}` }
function toEventItem(ev: any): EventItem { const eventType = String(ev?.event_type || 'event'), payload = ev?.payload || {}, stage = String(payload.stage || ev?.stage || payload?.payload?.state?.stage || ''), msg = payload.stage_zh || payload.message || payload.reason || stage || payload.mode || payload.selected_date || payload.post_name || payload?.payload?.state?.reason || payload?.error || '', hay = `${eventType} ${msg} ${JSON.stringify(payload)}`.toLowerCase(), tone = /fail|error|timeout|denied|1015|1020|429|失败|错误/.test(hay) ? 'bad' : /exit|finish|passed|success|命中|ok/.test(hay) ? 'good' : /recover|waiting|cf|challenge|login|恢复|等待/.test(hay) ? 'warn' : 'info', rk = eventRoundKey(ev), ts = ev?.created_at || ev?.ts || ''; return { id: String(ev?.event_id || ev?.id || `${ts}-${eventType}`), raw: ev, time: ts ? new Date(ts).toLocaleTimeString('zh-CN', { hour12: false }) : '-', dateTime: ts ? new Date(ts).toLocaleString('zh-CN', { hour12: false }) : '-', title: `${displaySlot(eventSlot(ev) || '系统')} · ${eventTitle(eventType, stage, payload)}`, desc: String(msg || summarizePayload(payload)), icon: tone === 'bad' ? '!' : tone === 'good' ? '✓' : tone === 'warn' ? '⟳' : 'i', tone, roundKey: rk, roundLabel: roundLabel(rk), screenshot: firstImageHref(ev) } }
function eventTitle(eventType: string, stage: string, payload: any) { const msg = `${payload?.message || ''} ${payload?.payload?.message || ''}`.toLowerCase(); if (eventType === 'stage_exit' && stage === 'login' && /interrupted by cf_challenge|blocked by cf_challenge/.test(msg)) return /after security/.test(msg) ? '登录后 CF 回插' : '登录阶段被 CF 打断'; if (eventType === 'stage_exit' && stage === 'login' && /security questions not matched/.test(msg)) return '密保问题未匹配'; return stage ? stageLabel(stage) : humanEvent(eventType) }
function humanEvent(t: string) { return ({ pipeline_started: '系统启动', pipeline_stopped: '系统停止', stage_enter: '进入阶段', stage_exit: '阶段完成', round_start: '本轮开始', round_finish: '本轮结束', browser_launched: '浏览器启动', recovery_attempt: '恢复尝试', live_snapshot: '阶段快照', stage_final_snapshot: '最终快照', browser_relaunched_direct: '直连重启', proxy_bypass_after_network_error: '代理失败转直连', business_navigation_start: '进入预约入口', business_manage_clicked: '点击预约入口', business_schedule_page_ready: '预约页就绪', business_context_resolved: '申请上下文', business_post_selecting: '选择北京', business_post_selected: '北京已选择', business_dates_collecting: '收集日期', business_dates_collected: '日期已收集', business_date_rejected: '日期不合适', business_date_accepted: '日期命中', business_slot_collecting: '查时间段', business_entries_collected: '时间段已收集', business_booking_signal_ready: '抢票信号', business_retry_home: '回首页重进', business_blocked_cf: 'CF 阻断', business_blocked_login: '登录回跳', business_blocked_waiting_room: '等待室阻断', business_rate_limit_cooldown: '限流冷却' } as Record<string, string>)[t] || t }
function stageLabel(stage: string) { return ({ proxy_acquire: '获取代理', cf_gate: 'CF 校验', waiting_room: '等待室', login: '登录/密保', business_query: '查日期/时间段', booking_submit: '提交抢票', recovery: '自动恢复', rate_limit_1015: '1015 限流', site: '官网首页' } as Record<string, string>)[stage] || stage }
function summarizePayload(p: Record<string, unknown>) { const s = JSON.stringify(p); return s.length > 120 ? `${s.slice(0, 120)}...` : s }
function findOfficialError(obj: any): any { const found = [obj?.official_error, obj?.payload?.official_error, obj?.payload?.payload?.official_error, obj?.payload?.recovery?.evidence?.official_error, obj?.payload?.evidence?.official_error].find(Boolean); if (found) return found; const p = obj?.payload || {}, inner = p?.payload || {}, state = inner?.state || p?.state || {}, t = `${p?.message || ''} ${p?.reason || ''} ${inner?.reason || ''} ${state?.stage || ''} ${state?.reason || ''} ${state?.title || ''}`.toLowerCase(); return /error\s*1015|you are being rate limited|rate_limit_1015|ban_1015/.test(t) ? { code: '1015', name: 'Cloudflare Error 1015', headline: 'You are being rate limited' } : /http\s*429|too many requests|rate_limit_429/.test(t) ? { code: '429', name: 'HTTP 429', headline: 'Too Many Requests' } : /error\s*1020|access_denied|access denied|you have been blocked/.test(t) ? { code: '1020', name: 'Cloudflare Access Denied', headline: 'Access denied' } : null }
function eventArtifacts(ev: any) { const links: { label: string; href: string }[] = []; const direct = ev?.payload?.screenshot; if (typeof direct === 'string') links.push({ label: 'screenshot', href: storageHref(direct) }); for (const pool of [ev?.payload?.artifacts, ev?.payload?.payload?.artifacts, ev?.payload?.evidence, ev?.payload?.recovery?.evidence]) { if (!pool || typeof pool !== 'object') continue; for (const [label, value] of Object.entries(pool)) if (typeof value === 'string' && (value.startsWith('storage/') || /\.(png|jpg|jpeg|webp|html|json|txt)$/i.test(value))) links.push({ label, href: storageHref(value) }) } return links }
function storageHref(value: string) { return value.startsWith('http') ? value : `${API_BASE}/${value.replace(/^\/+/, '')}` }
function firstImageHref(ev: any): string { return eventArtifacts(ev).find((x) => /\.(png|jpg|jpeg|webp)(\?|$)/i.test(x.href))?.href || '' }
function prettyJson(value: unknown) { return JSON.stringify(value, null, 2) }
</script>

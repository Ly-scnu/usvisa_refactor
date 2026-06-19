<template>
  <section class="diagnose-console-v2">
    <aside class="diagnose-queue-card">
      <h2>诊断对象队列</h2>
      <div class="diag-filter-tabs"><button class="active">全部</button><button>异常中</button><button>恢复中</button><button>待人工</button></div>
      <button v-for="slot in slots" :key="slot.slot" :class="['diag-queue-item', slotTone(slot), { active: activeSlot?.slot === slot.slot }]" @click="selectSlot(slot.slot)">
        <div><b>{{ displaySlot(slot.slot) }}</b><span>{{ slotBadge(slot) }}</span><i v-if="slot.target_hit">✓</i></div>
        <p><small>最近心跳</small><strong>{{ heartbeat(slot.updated_at) }}</strong></p>
        <p><small>最新阶段</small><strong>{{ slot.stage_zh || stageLabel(slot.stage || '') }}</strong></p>
        <em>{{ slot.last_reason_zh || slot.last_reason || slotResult(slot) }}</em>
      </button>
      <div class="diag-perspective-v2">
        <h3>诊断视角</h3>
        <span>当前聚焦 <b>{{ displaySlot(activeSlot?.slot || '-') }}</b></span>
        <span>更新事件 <b>{{ latestEventTime }}</b></span>
        <span>诊断模式 <b>自动 + 人工协同</b></span>
      </div>
    </aside>

    <main class="diagnose-main-v2">
      <article :class="['diagnose-hero-v2', conclusionTone]">
        <div class="hero-mark">{{ conclusionIcon }}</div>
        <div>
          <h2>状态结论：{{ conclusionTitle }}</h2>
          <p>{{ conclusionDesc }}</p>
        </div>
        <span v-if="isRecovering" class="badge warning">自动恢复中</span>
        <span v-if="needsManual" class="badge danger">优先关注</span>
      </article>

      <section class="diagnose-stage-strip">
        <div v-for="step in stageSteps" :key="step.key" :class="step.state">
          <i>{{ step.icon }}</i><span>{{ step.label }}</span>
        </div>
      </section>

      <section class="diagnose-two-col-v2">
        <article class="diag-panel-v2">
          <h3>A. 失败原因分析</h3>
          <button v-for="reason in reasonRows" :key="reason.name" class="reason-pill-v2" @click="openModal('reason', reason)">
            <i>{{ reason.icon }}</i><b>{{ reason.name }}</b><span :class="reason.level">可信度：{{ reason.confidence }}</span>
          </button>
          <div class="diagnose-advice"><b>关键建议</b><p>{{ recommendedAction }}</p></div>
        </article>
        <article class="diag-panel-v2">
          <h3>B. 运行上下文</h3>
          <div class="diag-context-grid-v2">
            <span>槽位<b>{{ displaySlot(activeSlot?.slot || '-') }}</b></span>
            <span>最近心跳<b>{{ heartbeat(activeSlot?.updated_at) }}</b></span>
            <span>当前阶段<b>{{ activeSlot?.stage_zh || stageLabel(activeSlot?.stage || '') }}</b></span>
            <span>运行时长<b>{{ activeSlot?.elapsed_s ?? 0 }}s</b></span>
            <span>代理<b>{{ activeSlot?.proxy_display || '-' }}</b></span>
            <span>恢复组件<b>{{ activeSlot?.recovery_component || latestRecovery?.component || '-' }}</b></span>
            <span>配置版本<b>cfg-v2026.06.12-r3</b></span>
            <span>本轮尝试<b>{{ activeRoundLabel }}</b></span>
          </div>
        </article>
      </section>

      <section class="diag-panel-v2 post-success-panel">
        <header>
          <div>
            <h3>C. 成功后失败单独统计</h3>
            <p>只统计同一槽位/轮次中“已出现查询成功或命中信号”之后发生的失败，避免和登录前、等待室、CF 早期失败混在一起。</p>
          </div>
          <span>{{ postSuccessFailures.length }} 条</span>
        </header>
        <div v-if="postSuccessFailureRows.length" class="post-success-grid">
          <button v-for="row in postSuccessFailureRows" :key="row.reason" class="post-success-row" @click="openModal('raw', row.samples)">
            <b>{{ row.reason }}</b>
            <span>全局 {{ row.total }} 次 · 当前槽位 {{ row.activeSlotTotal }} 次</span>
            <em>{{ row.latestTime }}</em>
          </button>
        </div>
        <div v-else class="diag-empty">暂无成功后失败样本；当前失败更可能发生在成功前流程。</div>
      </section>

      <section class="diag-panel-v2 diagnose-chain-panel">
        <header><div><h3>关键事件链</h3><p>按所选槽位/轮次从开始到结束展示；支持横向滚动，点击节点看快照或详情。</p></div><button @click="openModal('events')">查看完整事件流 ›</button></header>
        <div class="diagnose-chain-scroll">
          <button v-for="item in chainItems" :key="item.id" :class="['diagnose-chain-node', item.tone]" @click="item.screenshot ? openModal('image', { src: item.screenshot, title: item.title, raw: item.raw }) : openModal('event', item)">
            <time>{{ item.time }}</time>
            <i>{{ item.screenshot ? '📷' : item.icon }}</i>
            <b>{{ item.title }}</b>
            <span>{{ item.desc }}</span>
            <em>{{ item.tag }}</em>
          </button>
          <div v-if="!chainItems.length" class="diag-empty">暂无事件链；启动槽位后会实时记录。</div>
        </div>
      </section>
    </main>

    <aside class="diagnose-evidence-v2">
      <article class="diag-panel-v2">
        <h3>1. 失败快照 / 实时证据</h3>
        <div class="evidence-tabs"><button class="active">失败前快照</button><button>最近实时快照</button></div>
        <button v-if="evidenceImage" class="diagnose-snapshot" @click="openModal('image', { src: evidenceImage, title: '当前证据快照' })">
          <img :src="evidenceImage" /><span>{{ evidenceLabel }}</span>
        </button>
        <div v-else class="empty-shot">暂无快照；阶段失败和恢复组件会自动保留证据。</div>
      </article>

      <article class="diag-panel-v2 compact">
        <h3>2. 恢复记录</h3>
        <p>已自动恢复 {{ recoveryEvents.length }} 次，本轮 {{ activeRoundLabel }}</p>
        <button v-for="ev in recoveryEvents.slice(0, 4)" :key="ev.event_id" class="recover-row" @click="openModal('raw', ev)">
          <span>{{ formatTime(ev.created_at) }}</span><b>{{ recoveryAction(ev) }}</b>
        </button>
      </article>

      <article class="diag-panel-v2 compact">
        <h3>3. 建议动作</h3>
        <div class="diagnose-actions-v2">
          <button class="primary" @click="activeSlot && store.slotCommand(activeSlot.slot, 'headed')">切有头诊断</button>
          <button @click="activeSlot && store.slotCommand(activeSlot.slot, 'snapshot')">重新截图</button>
          <button @click="activeSlot && store.slotCommand(activeSlot.slot, 'reload')">重启槽位</button>
          <a :href="`${API_BASE}/storage/logs/events.jsonl`" target="_blank">查看关联日志</a>
          <button @click="openModal('events')">查看历史恢复记录</button>
          <a :href="`${API_BASE}/storage/logs/events.jsonl`" target="_blank">导出诊断包</a>
        </div>
      </article>

      <article class="diag-panel-v2 compact">
        <h3>4. 风险提示</h3>
        <ul class="risk-list-v2">
          <li v-if="cfFailures">最近记录 {{ cfFailures }} 次 CF / 人机 / 1015 相关事件</li>
          <li v-if="needsManual">该槽位需要人工确认页面或代理质量</li>
          <li>建议检查代理质量与浏览器指纹一致性</li>
        </ul>
      </article>
    </aside>

    <div v-if="modal" class="os-modal-backdrop" @click.self="closeModal">
      <section :class="['os-modal', modal.kind === 'image' ? 'image-modal' : '']">
        <header class="os-modal-head"><div><h2>{{ modalTitle }}</h2><p>{{ modalSubtitle }}</p></div><button @click="closeModal">×</button></header>
        <div v-if="modal.kind === 'image'" class="diagnose-image-view"><img :src="modal.payload.src" /></div>
        <div v-else-if="modal.kind === 'event'" class="event-detail-body">
          <div :class="['event-detail-summary', modal.payload.tone]"><b>{{ modal.payload.title }}</b><span>{{ modal.payload.dateTime }}</span><p>{{ modal.payload.desc }}</p></div>
          <button v-if="modal.payload.screenshot" class="event-image-preview" @click="openModal('image', { src: modal.payload.screenshot, title: modal.payload.title })"><img :src="modal.payload.screenshot" /><span>打开节点快照</span></button>
          <pre>{{ prettyJson(modal.payload.raw) }}</pre>
        </div>
        <div v-else-if="modal.kind === 'events'" class="events-modal-body">
          <div class="event-modal-toolbar"><span>槽位：<b>{{ displaySlot(activeSlot?.slot || '-') }}</b></span><select v-model="roundFilter"><option value="latest">最新轮次</option><option value="all">全部轮次</option><option v-for="r in roundOptions" :key="r.key" :value="r.key">{{ r.label }}</option></select><a :href="`${API_BASE}/storage/logs/events.jsonl`" target="_blank">打开 events.jsonl</a></div>
          <div class="event-list-full"><button v-for="item in chainItems" :key="item.id" :class="['event-row-full', item.tone]" @click="openModal('event', item)"><time>{{ item.dateTime }}</time><b>{{ item.title }}</b><span>{{ item.desc }}</span><em>{{ item.roundLabel }}</em></button></div>
        </div>
        <pre v-else>{{ prettyJson(modal.payload) }}</pre>
      </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { API_BASE } from '../../api/request'
import { useSystemStore } from '../../store/system'
import type { SlotStatus } from '../../types/system'

type ModalKind = 'image' | 'event' | 'events' | 'raw' | 'reason'
interface ChainItem { id: string; raw: any; time: string; dateTime: string; title: string; desc: string; tag: string; icon: string; tone: string; screenshot: string; roundKey: string; roundLabel: string }

const store = useSystemStore()
const roundFilter = ref('latest')
const modal = ref<{ kind: ModalKind; payload?: any } | null>(null)
const status = computed(() => store.status)
const slots = computed<SlotStatus[]>(() => status.value?.slots || [])
const allEvents = computed<any[]>(() => [...(status.value?.events || [])].sort((a, b) => eventMs(a) - eventMs(b)))
const activeSlot = computed(() => slots.value.find((s) => s.slot === store.selectedSlot) || slots.value.find((s) => /1015|429|denied|failed|失败|cf|recover|卡/.test(`${s.last_reason || ''}${s.last_reason_zh || ''}${s.stage || ''}`.toLowerCase())) || slots.value[0])
const slotEvents = computed(() => allEvents.value.filter((e) => eventSlot(e) === activeSlot.value?.slot))
const roundOptions = computed(() => {
  const map = new Map<string, { key: string; label: string; ms: number }>()
  for (const ev of slotEvents.value) { const k = roundKey(ev); if (k === 'unknown') continue; map.set(k, { key: k, label: roundLabel(k), ms: Math.max(map.get(k)?.ms || 0, eventMs(ev)) }) }
  return [...map.values()].sort((a, b) => b.ms - a.ms)
})
const effectiveRound = computed(() => roundFilter.value === 'latest' ? (roundOptions.value[0]?.key || 'all') : roundFilter.value)
const filteredEvents = computed(() => effectiveRound.value === 'all' ? slotEvents.value : slotEvents.value.filter((e) => roundKey(e) === effectiveRound.value))
const chainItems = computed<ChainItem[]>(() => filteredEvents.value.filter(importantEvent).map(toChainItem))
const activeRoundLabel = computed(() => effectiveRound.value === 'all' ? '全部轮次' : roundLabel(effectiveRound.value))
const latestEventTime = computed(() => formatTime((slotEvents.value.length ? slotEvents.value[slotEvents.value.length - 1]?.created_at : '') || status.value?.ts))
const latestRecovery = computed(() => recoveryEvents.value.length ? recoveryEvents.value[recoveryEvents.value.length - 1]?.payload || null : null)

const officialError = computed(() => {
  for (const ev of [...slotEvents.value].reverse()) {
    const found = findOfficialError(ev)
    if (found) return found
  }
  return null
})
const conclusionTone = computed(() => officialError.value ? (/1015|429|cf|challenge/i.test(JSON.stringify(officialError.value)) ? 'warn' : 'bad') : slotTone(activeSlot.value))
const conclusionIcon = computed(() => conclusionTone.value === 'bad' ? '×' : conclusionTone.value === 'warn' ? '!' : '✓')
const conclusionTitle = computed(() => officialError.value?.name || officialError.value?.headline || (activeSlot.value?.last_reason_zh || activeSlot.value?.last_reason || '暂无明确异常'))
const conclusionDesc = computed(() => officialError.value?.headline || officialError.value?.meaning || '没有检测到新的官方错误页；请结合事件链与快照判断。')
const recommendedAction = computed(() => officialError.value?.code === '1015' ? '不要原页反复刷新；保存证据后换代理/新画像，下一轮重新进入。' : officialError.value?.code === '429' ? '降低轮询频率，短暂退避后继续。' : '优先查看最新快照和失败节点 payload，必要时切有头诊断。')
const isRecovering = computed(() => recoveryEvents.value.length > 0 || /recover|恢复/.test(`${activeSlot.value?.last_reason || ''}${activeSlot.value?.last_reason_zh || ''}`.toLowerCase()))
const needsManual = computed(() => /manual|人工|failed|access_denied|卡/.test(`${activeSlot.value?.last_reason || ''}${activeSlot.value?.last_reason_zh || ''}`.toLowerCase()))
const stageSteps = computed(() => {
  const order = [['proxy_acquire', '初始化'], ['cf_gate', '打开页面'], ['waiting_room', '等待室'], ['login', '登录'], ['business_query', '进入查询'], ['booking_submit', '提交'], ['recovery', '恢复']] as const
  const stages = new Set(filteredEvents.value.map((e) => String(e.payload?.stage || e.stage || '')))
  let active = order.findIndex(([key]) => key === activeSlot.value?.stage)
  if (active < 0) active = Math.max(0, order.findIndex(([key]) => stages.has(key)))
  return order.map(([key, label], i) => ({ key, label, icon: i < active ? '✓' : i === active ? conclusionIcon.value : '○', state: i < active ? 'done' : i === active ? conclusionTone.value : 'todo' }))
})
const reasonRows = computed(() => {
  if (officialError.value?.code === '1015') return [{ icon: '▣', name: '官方 1015 限流页', confidence: '高', level: 'high' }, { icon: '◉', name: '代理/浏览器画像触发频控', confidence: '中', level: 'mid' }, { icon: '◷', name: '同会话请求过密', confidence: '中', level: 'mid' }]
  if (officialError.value?.code === '429') return [{ icon: '◷', name: '请求频率过高', confidence: '高', level: 'high' }, { icon: '▣', name: '轮询间隔偏短', confidence: '中', level: 'mid' }]
  if (/cf/.test(String(activeSlot.value?.stage || ''))) return [{ icon: '◇', name: 'Cloudflare 人机/托管挑战', confidence: '中', level: 'mid' }]
  return [{ icon: 'i', name: '暂无明确官方错误页', confidence: '低', level: 'low' }]
})
const recoveryEvents = computed(() => slotEvents.value.filter((e) => e.event_type === 'recovery_attempt' || /recover|1015|429/.test(JSON.stringify(e.payload || {}).toLowerCase())))
const cfFailures = computed(() => slotEvents.value.filter((e) => /cf|challenge|1015|429/.test(JSON.stringify(e.payload || {}).toLowerCase())).length)
const postSuccessFailures = computed(() => collectPostSuccessFailures(allEvents.value))
const postSuccessFailureRows = computed(() => {
  const map = new Map<string, { reason: string; total: number; activeSlotTotal: number; latestMs: number; samples: any[] }>()
  const active = activeSlot.value?.slot || ''
  for (const item of postSuccessFailures.value) {
    const row = map.get(item.reason) || { reason: item.reason, total: 0, activeSlotTotal: 0, latestMs: 0, samples: [] }
    row.total += 1
    if (item.slot === active) row.activeSlotTotal += 1
    row.latestMs = Math.max(row.latestMs, item.ms)
    if (row.samples.length < 12) row.samples.push(item.event)
    map.set(item.reason, row)
  }
  return [...map.values()]
    .sort((a, b) => b.activeSlotTotal - a.activeSlotTotal || b.total - a.total || b.latestMs - a.latestMs)
    .slice(0, 6)
    .map((row) => ({ ...row, latestTime: row.latestMs ? formatTime(new Date(row.latestMs).toISOString()) : '-' }))
})
const evidenceEvent = computed(() => [...chainItems.value].reverse().find((x) => x.screenshot) || null)
const evidenceImage = computed(() => evidenceEvent.value?.screenshot || (activeSlot.value ? `${API_BASE}/storage/live_snapshots/${activeSlot.value.slot}.png?v=${encodeURIComponent(activeSlot.value.updated_at || '')}` : ''))
const evidenceLabel = computed(() => evidenceEvent.value ? `${evidenceEvent.value.title} · ${evidenceEvent.value.time}` : '实时快照')
const modalTitle = computed(() => modal.value?.kind === 'image' ? (modal.value.payload?.title || '快照') : modal.value?.kind === 'event' ? '事件详情' : modal.value?.kind === 'events' ? '完整事件链' : modal.value?.kind === 'reason' ? '原因详情' : '原始数据')
const modalSubtitle = computed(() => modal.value?.kind === 'events' ? '可切换轮次；节点点击可查看快照或 payload。' : '完整证据用于排错，不在主界面默认展开。')

watch(() => activeSlot.value?.slot, () => { roundFilter.value = 'latest' })
function selectSlot(slot: string) { store.selectedSlot = slot }
function openModal(kind: ModalKind, payload?: any) { modal.value = { kind, payload } }
function closeModal() { modal.value = null }
function eventMs(ev: any): number { return new Date(ev?.created_at || 0).getTime() || 0 }
function eventSlot(ev: any): string { return String(ev?.slot_id || ev?.slot || '') }
function roundKey(ev: any): string { const v = ev?.round_id ?? ev?.round ?? ev?.payload?.round_id ?? ev?.payload?.round; return v ? String(v).startsWith('round_') ? String(v) : `round_${String(v).padStart(4, '0')}` : 'unknown' }
function roundLabel(key: string): string { return key === 'unknown' ? '未知轮次' : `第 ${Number(key.replace(/\D/g, '')) || key} 轮` }
function importantEvent(ev: any): boolean { return /round_|stage_|snapshot|recovery|browser|proxy|pipeline|business_/.test(String(ev.event_type)) || Boolean(ev.payload?.screenshot) || /1015|429|failed|error|denied|cf|login|waiting|query/.test(JSON.stringify(ev.payload || {}).toLowerCase()) }
function toChainItem(ev: any): ChainItem {
  const payload = ev.payload || {}, stage = String(payload.stage || ev.stage || payload?.payload?.state?.stage || ''), text = `${ev.event_type} ${JSON.stringify(payload)}`.toLowerCase()
  const tone = /1015|429|failed|error|denied|timeout|失败/.test(text) ? 'bad' : /recover|cf|waiting|login|challenge|恢复|等待/.test(text) ? 'warn' : /exit|success|ok|passed|acquired/.test(text) ? 'good' : 'info'
  const rk = roundKey(ev), title = stage ? stageLabel(stage) : humanEvent(ev.event_type)
  return { id: String(ev.event_id || `${ev.created_at}-${ev.event_type}`), raw: ev, time: formatTime(ev.created_at), dateTime: ev.created_at ? new Date(ev.created_at).toLocaleString('zh-CN', { hour12: false }) : '-', title, desc: eventDesc(ev), tag: humanEvent(ev.event_type), icon: tone === 'bad' ? '×' : tone === 'warn' ? '↻' : tone === 'good' ? '✓' : 'i', tone, screenshot: firstImageHref(ev), roundKey: rk, roundLabel: roundLabel(rk) }
}
function eventDesc(ev: any): string { const p = ev.payload || {}; return String(p.stage_zh || p.message || p.reason || p.action || p.mode || p.error_type || p.selected_date || p.post_name || p?.payload?.state?.reason || p?.payload?.state?.stage || '').slice(0, 80) }
function humanEvent(t: string): string { return ({ round_start: '本轮开始', round_finish: '本轮结束', stage_enter: '进入阶段', stage_exit: '阶段完成', live_snapshot: '阶段快照', stage_final_snapshot: '最终快照', browser_launched: '浏览器启动', recovery_attempt: '恢复尝试', pipeline_stopped: '系统停止', pipeline_started: '系统启动', business_navigation_start: '进入预约入口', business_manage_clicked: '点击预约入口', business_schedule_page_ready: '预约页就绪', business_context_resolved: '申请上下文', business_post_selecting: '选择北京', business_post_selected: '北京已选择', business_dates_collecting: '收集日期', business_dates_collected: '日期已收集', business_date_rejected: '日期不合适', business_date_accepted: '日期命中', business_slot_collecting: '查时间段', business_entries_collected: '时间段已收集', business_booking_signal_ready: '抢票信号', business_retry_home: '回首页重进', business_blocked_cf: 'CF 阻断', business_blocked_login: '登录回跳', business_blocked_waiting_room: '等待室阻断', business_rate_limit_cooldown: '限流冷却' } as Record<string, string>)[t] || t }
function stageLabel(stage: string): string { return ({ proxy_acquire: '获取代理', cf_gate: 'CF 校验', waiting_room: '等待室', login: '登录/密保', business_query: '查日期/时间段', booking_submit: '提交抢票', recovery: '自动恢复', rate_limit_1015: '1015 限流', site: '官网首页' } as Record<string, string>)[stage] || stage || '-' }
function displaySlot(slot: string): string { return slot.replace('slot_', 'Slot ') }
function heartbeat(ts?: string): string { return ts ? new Date(ts).toLocaleTimeString('zh-CN', { hour12: false }) : '-' }
function formatTime(ts?: string): string { return ts ? new Date(ts).toLocaleTimeString('zh-CN', { hour12: false }) : '-' }
function slotTone(slot?: SlotStatus): string { const text = `${slot?.stage || ''} ${slot?.last_reason || ''} ${slot?.last_reason_zh || ''}`.toLowerCase(); return /1015|429|failed|denied|error|失败|卡/.test(text) ? 'bad' : /recover|cf|waiting|login|恢复/.test(text) ? 'warn' : 'good' }
function slotBadge(slot?: SlotStatus): string { const tone = slotTone(slot); return tone === 'bad' ? '异常中' : tone === 'warn' ? '恢复/观察' : '正常运行' }
function slotResult(slot?: SlotStatus): string { return slot?.target_hit ? '已命中目标' : slot?.availability_days ? `发现 ${slot.availability_days} 个日期` : '等待诊断数据' }
function recoveryAction(ev: any): string { return ev.payload?.action || ev.payload?.message || ev.payload?.error_type || ev.event_type }
function findOfficialError(ev: any): any { const p = ev?.payload || {}; const found = [p.official_error, p.payload?.official_error, p.recovery?.evidence?.official_error, p.evidence?.official_error].find(Boolean); if (found) return found; const t = JSON.stringify(ev || ''); return /1015|rate limited/i.test(t) ? { code: '1015', name: 'Cloudflare Error 1015', headline: 'You are being rate limited', meaning: '官方反馈访问频率/代理画像触发限流。' } : /429/.test(t) ? { code: '429', name: 'HTTP 429', headline: 'Too Many Requests', meaning: '官方反馈请求过于频繁。' } : /1020/.test(t) ? { code: '1020', name: 'Cloudflare Error 1020', headline: 'Access denied' } : null }
function collectPostSuccessFailures(events: any[]) {
  const grouped = new Map<string, any[]>()
  for (const ev of events) {
    const key = `${eventSlot(ev) || 'unknown'}:${roundKey(ev)}`
    const list = grouped.get(key) || []
    list.push(ev)
    grouped.set(key, list)
  }
  const rows: { slot: string; round: string; reason: string; ms: number; event: any }[] = []
  for (const [key, list] of grouped) {
    let seenSuccess = false
    const [slot, round] = key.split(':')
    for (const ev of list.sort((a, b) => eventMs(a) - eventMs(b))) {
      if (isBusinessSuccessEvent(ev)) {
        seenSuccess = true
        continue
      }
      if (!seenSuccess || !isFailureEvent(ev)) continue
      rows.push({ slot, round, reason: classifyPostSuccessFailure(ev), ms: eventMs(ev), event: ev })
    }
  }
  return rows
}
function isBusinessSuccessEvent(ev: any): boolean {
  const text = `${ev?.event_type || ''} ${JSON.stringify(ev?.payload || {})}`.toLowerCase()
  return /business_dates_collected|business_entries_collected|business_date_accepted|business_booking_signal_ready|target_hit|query_success|availability|matched_slots|success_count/.test(text)
}
function isFailureEvent(ev: any): boolean {
  const text = `${ev?.event_type || ''} ${JSON.stringify(ev?.payload || {})}`.toLowerCase()
  return /failed|failure|error|denied|timeout|exception|blocked|retry_home|session_reuse|incomplete|1015|429|1020|业务页状态不完整|失败|异常|回跳|阻断/.test(text)
}
function classifyPostSuccessFailure(ev: any): string {
  const text = `${ev?.event_type || ''} ${JSON.stringify(ev?.payload || {})}`.toLowerCase()
  if (/业务页状态不完整|business_retry_home|incomplete|home/.test(text)) return '业务页状态不完整 / 回首页'
  if (/session_reuse|会话复用|reuse/.test(text)) return '会话复用判定失败'
  if (/business_blocked_login|login|auth|密保|回跳/.test(text)) return '登录态失效或登录回跳'
  if (/1015|429|rate_limit|too many|限流/.test(text)) return '官方限流 / 请求过密'
  if (/cf|challenge|1020|denied|access/.test(text)) return 'CF / 访问阻断'
  if (/timeout|超时/.test(text)) return '请求或页面等待超时'
  return eventDesc(ev) || humanEvent(String(ev?.event_type || '未知失败'))
}
function storageHref(value: string): string { return value.startsWith('http') ? value : `${API_BASE}/${value.replace(/^\/+/, '')}` }
function firstImageHref(ev: any): string { const p = ev?.payload || {}; const candidates: string[] = []; if (typeof p.screenshot === 'string') candidates.push(p.screenshot); for (const pool of [p.artifacts, p.payload?.artifacts, p.evidence, p.recovery?.evidence]) { if (!pool || typeof pool !== 'object') continue; for (const v of Object.values(pool)) if (typeof v === 'string') candidates.push(v) } const hit = candidates.find((v) => /\.(png|jpg|jpeg|webp)$/i.test(v)); return hit ? storageHref(hit) : '' }
function prettyJson(value: unknown): string { return JSON.stringify(value, null, 2) }
</script>

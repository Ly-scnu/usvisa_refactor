<template>
  <section class="deep-analysis">
    <div class="analysis-toolbar">
      <div>
        <b>策略分析大盘</b>
        <span>先看宏观结论，再展开四宫格，最后下钻到代理/阶段/时间/会话证据。</span>
      </div>
      <div class="toolbar-actions">
        <button :class="{ active: layoutMode === 'global' }" @click="layoutMode = 'global'">总盘</button>
        <button :class="{ active: layoutMode === 'grid' }" @click="layoutMode = 'grid'">四宫格</button>
        <button @click="resetAll">全部返回一级</button>
      </div>
    </div>

    <AnalysisPanelCard
      v-if="layoutMode === 'global'"
      class="global-board"
      title="全局总盘"
      eyebrow="L1 · MACRO"
      :subtitle="globalConclusion"
      :tone="globalTone"
      show-zoom
      @zoom="layoutMode = 'grid'"
    >
      <div class="global-hero">
        <div class="health-ring" :style="{ '--pct': `${successRate}%` }"><b>{{ successRate }}%</b><span>查询成功率</span></div>
        <div class="global-findings">
          <h4>{{ mainFinding.title }}</h4>
          <p>{{ mainFinding.desc }}</p>
          <div class="finding-tags">
            <button @click="focusPanel('funnel', 'risk_auth')">风控/登录</button>
            <button @click="focusPanel('proxy', 'proxy_country')">代理路线</button>
            <button @click="focusPanel('trend', 'risk_time')">风控时间</button>
            <button @click="focusPanel('session', 'failure_causes')">失败归因</button>
          </div>
        </div>
      </div>
      <div class="global-metrics">
        <span v-for="m in globalMetrics" :key="m.label"><small>{{ m.label }}</small><b>{{ m.value }}</b><em>{{ m.hint }}</em></span>
      </div>
      <div class="global-lanes">
        <div><h4>最佳路线</h4><p>{{ bestProxyLine }}</p></div>
        <div><h4>最大瓶颈</h4><p>{{ bottleneckLine }}</p></div>
        <div><h4>放票信号</h4><p>{{ releaseLine }}</p></div>
      </div>
    </AnalysisPanelCard>

    <div v-else class="board-grid">
      <AnalysisPanelCard
        v-for="slot in panelSlots"
        :key="slot.group"
        :title="boardTitle(slot.kind)"
        :eyebrow="boardEyebrow(slot.group, slot.kind)"
        :subtitle="boardSubtitle(slot.kind)"
        :model-value="slot.kind"
        :options="boardOptions(slot.group)"
        :show-reset="slot.kind !== defaultBoard(slot.group)"
        @update:model-value="setPanelKind(slot.group, $event)"
        @reset="setPanelKind(slot.group, defaultBoard(slot.group))"
      >
        <component :is="boardComponent(slot.kind)" :kind="slot.kind" />
      </AnalysisPanelCard>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, reactive, ref } from 'vue'
import AnalysisPanelCard from './AnalysisPanelCard.vue'

const props = defineProps<{ analytics: Record<string, any> }>()
const emit = defineEmits<{ 'open-session': [row: any] }>()

type GroupKey = 'proxy' | 'funnel' | 'trend' | 'session'
type BoardKind = 'proxy' | 'proxy_country' | 'proxy_session' | 'funnel' | 'risk_auth' | 'business_fail' | 'trend' | 'release_trend' | 'risk_time' | 'session' | 'top_sessions' | 'failure_causes'

const layoutMode = ref<'global' | 'grid'>('global')
const panelSlots = reactive<{ group: GroupKey; kind: BoardKind }[]>([
  { group: 'proxy', kind: 'proxy' },
  { group: 'funnel', kind: 'funnel' },
  { group: 'trend', kind: 'trend' },
  { group: 'session', kind: 'session' }
])
const options: Record<GroupKey, { key: BoardKind; label: string }[]> = {
  proxy: [{ key: 'proxy', label: '代理路线盘' }, { key: 'proxy_country', label: '国家/ASN 对比' }, { key: 'proxy_session', label: '代理 Session 质量' }],
  funnel: [{ key: 'funnel', label: '流程漏斗盘' }, { key: 'risk_auth', label: 'CF/等待室/登录' }, { key: 'business_fail', label: '查询业务失败' }],
  trend: [{ key: 'trend', label: '时间趋势盘' }, { key: 'release_trend', label: '放票趋势' }, { key: 'risk_time', label: '风控时间' }],
  session: [{ key: 'session', label: '会话质量盘' }, { key: 'top_sessions', label: '高价值会话' }, { key: 'failure_causes', label: '失败归因' }]
}

const sessions = computed<any[]>(() => props.analytics?.sessions || [])
const summary = computed(() => props.analytics?.summary || {})
const hourly = computed<any[]>(() => props.analytics?.hourly || [])
const releaseEvents = computed<any[]>(() => props.analytics?.release_events || [])
const byProxy = computed<any[]>(() => props.analytics?.by_proxy || [])
const successRate = computed(() => Math.round(Number(summary.value.success_rate || 0)))
const globalTone = computed(() => successRate.value >= 20 ? 'good' : successRate.value >= 5 ? 'warn' : 'bad')
const globalConclusion = computed(() => `样本 ${summary.value.total_sessions || 0} 个，会话成功 ${summary.value.used || 0} 个，恢复事件 ${summary.value.recovery_events || 0} 次。`)
const globalMetrics = computed(() => [
  { label: '会话产出', value: summary.value.total_sessions || 0, hint: '完整生产流程' },
  { label: '查询成功', value: summary.value.query_success || 0, hint: '写入票池次数' },
  { label: '失败会话', value: summary.value.failed || 0, hint: '未成功查 days' },
  { label: '平均存活', value: duration(summary.value.avg_alive_seconds || 0), hint: '产出到关闭' },
  { label: '命中目标', value: summary.value.hit || 0, hint: '截止日前' },
  { label: '恢复事件', value: summary.value.recovery_events || 0, hint: '1015/429/CF 等' }
])
const bestProxyLine = computed(() => {
  const p = byProxy.value[0]
  return p ? `${p.proxy_display}：成功率 ${p.success_rate}% · 成功 ${p.query_success}/${p.total} · 平均存活 ${duration(p.avg_alive_seconds)}` : '暂无足够代理样本。'
})
const bottleneckLine = computed(() => {
  const top = failureCauses.value[0]
  return top ? `${top.name} 最多，共 ${top.count} 次；建议下钻流程漏斗或失败归因。` : '暂无明显瓶颈。'
})
const releaseLine = computed(() => {
  const last = [...releaseEvents.value].reverse()[0]
  return last ? `${fmt(last.queried_at)} 出现 ${last.new_dates?.length || 0} 个新日期：${(last.new_dates || []).slice(0, 3).join(', ')}` : '暂无多轮日期变化样本。'
})
const mainFinding = computed(() => {
  const top = failureCauses.value[0]
  if (top) return { title: `当前最大问题：${top.name}`, desc: `在语义事件链和失败原因中累计 ${top.count} 次，建议先下钻查看关联代理、时间和会话。` }
  return { title: '暂无明显异常集中点', desc: '继续积累样本后，总盘会自动给出最大瓶颈和下钻方向。' }
})

const proxyRows = computed(() => byProxy.value.slice(0, 10).map((p) => ({
  name: p.proxy_display || '-',
  sub: `总 ${p.total || 0} · 成功 ${p.query_success || 0} · 失败 ${p.failed || 0}`,
  pct: Number(p.success_rate || 0),
  value: `${p.success_rate || 0}%`,
  extra: `平均存活 ${duration(p.avg_alive_seconds || 0)}`
})))
const proxyCountryRows = computed(() => groupSessions((s) => String(s.route || s.proxy_display || '-').split(':')[0] || '-'))
const proxySessionRows = computed(() => groupSessions((s) => String(s.proxy_session || s.proxy_display || '-')))

const funnelRows = computed(() => {
  const stages = [
    ['proxy_acquire', '获取代理'], ['browser_launched', '拉起浏览器'], ['cf_gate', 'CF 校验'], ['waiting_room', '等待室'], ['login', '登录/密保'], ['business_query', '进入查询'], ['business_post_selected', '选择北京'], ['business_dates_collected', '查询日期'], ['business_date_accepted', '日期命中'], ['business_entries_collected', '查询时间段'], ['booking_submit', '提交抢票']
  ]
  return stages.map(([key, label], idx) => {
    const entered = sessions.value.filter((s) => hasStage(s, key)).length
    const ok = key === 'business_dates_collected' ? sessions.value.filter((s) => s.query_success_count > 0).length : sessions.value.filter((s) => stageOk(s, key)).length
    const pct = idx === 0 ? 100 : Math.round(ok / Math.max(1, sessions.value.length) * 100)
    return { key, label, entered, ok, fail: Math.max(0, entered - ok), pct }
  })
})
const riskAuthRows = computed(() => countPatterns([
  ['CF / Challenge', /cf|challenge|人机/i], ['Waiting Room', /waiting|等待室/i], ['登录/密保', /login|b2c|密保|登录/i], ['1015 限流', /1015|ban_1015/i], ['429 限流', /429|too many/i]
]))
const businessFailRows = computed(() => countPatterns([
  ['未进入查询页', /navigation_not_found|进入查询失败|未进入查询页/i], ['预约入口未点击', /manage_clicked|not_found|预约|schedule|appointment/i], ['北京选择失败', /post_selected|post_select|北京/i], ['日期查询失败', /dates|schedule_days|日期/i], ['协议/上下文异常', /context|application|protocol|fetch/i]
]))

const trendRows = computed(() => hourly.value.map((h) => ({ label: h.hour, a: Number(h.queries || 0), b: Number(h.new_dates || 0), c: Number(h.hits || 0) })))
const releaseRows = computed(() => [...releaseEvents.value].reverse().slice(0, 10).map((x) => ({
  name: fmt(x.queried_at), sub: `${x.slot_id || '-'} ${x.round_id || ''}`, value: `新增 ${x.new_dates?.length || 0}`, extra: (x.new_dates || []).slice(0, 5).join(', ')
})))
const riskTimeRows = computed(() => {
  const rows = Array.from({ length: 24 }, (_, i) => ({ hour: `${String(i).padStart(2, '0')}:00`, risk: 0, success: 0 }))
  for (const s of sessions.value) {
    const h = new Date(s.updated_at || s.created_at || 0).getHours()
    if (Number.isFinite(h)) {
      if (s.query_success_count) rows[h].success += 1
      rows[h].risk += countSessionText(s, /1015|429|cf|challenge|waiting|denied/i)
    }
  }
  return rows
})

const qualityRows = computed(() => [...sessions.value].sort((a, b) => sessionScore(b) - sessionScore(a)).slice(0, 10))
const topSessionRows = computed(() => qualityRows.value.filter((s) => sessionScore(s) > 0).slice(0, 10))
const failureCauses = computed(() => {
  const rows = countPatterns([
    ['CF 校验失败', /cf|challenge|人机/i], ['进入等待室', /waiting|等待室/i], ['登录/密保失败', /login|b2c|密保|登录/i], ['未进入查询页', /navigation_not_found|未进入查询页|进入查询失败/i], ['1015 限流', /1015|ban_1015/i], ['429 限流', /429|too many/i], ['Access Denied', /1020|access_denied|denied/i], ['网络/超时', /network|timeout|超时/i]
  ])
  return rows.filter((x) => x.count > 0)
})

function resetAll() { panelSlots[0].kind = 'proxy'; panelSlots[1].kind = 'funnel'; panelSlots[2].kind = 'trend'; panelSlots[3].kind = 'session' }
function focusPanel(group: GroupKey, kind: BoardKind) { layoutMode.value = 'grid'; setPanelKind(group, kind) }
function defaultBoard(group: GroupKey): BoardKind { return options[group][0].key }
function boardOptions(group: GroupKey) { return options[group] }
function setPanelKind(group: GroupKey, kind: string) { const slot = panelSlots.find((x) => x.group === group); if (slot) slot.kind = kind as BoardKind }
function boardTitle(kind: BoardKind) { return ({ proxy: '代理路线盘', proxy_country: '国家/ASN 对比盘', proxy_session: '代理 Session 质量盘', funnel: '流程漏斗盘', risk_auth: 'CF/等待室/登录风控盘', business_fail: '查询业务失败盘', trend: '时间趋势盘', release_trend: '放票趋势盘', risk_time: '风控时间盘', session: '会话质量盘', top_sessions: '高价值会话排行盘', failure_causes: '失败会话归因盘' } as Record<BoardKind, string>)[kind] }
function boardSubtitle(kind: BoardKind) { return ({ proxy: '先判断哪条代理路线整体更稳。', proxy_country: '按国家/ASN/路线聚合，适合比较大策略。', proxy_session: '按 proxy session 看复用与存活。', funnel: '看完整流程死在哪一步。', risk_auth: '把 CF、等待室、登录密保单独拎出来看。', business_fail: '聚焦首页到查询页、北京、days 协议。', trend: '按小时看查询、命中、新日期。', release_trend: '定位新日期出现的时间点。', risk_time: '找 1015/429/CF 高发时段。', session: '找最值得复用的会话票。', top_sessions: '按质量分排序，高成功、长存活、少错误。', failure_causes: '失败票按原因聚合，便于先修最大头。' } as Record<BoardKind, string>)[kind] }
function boardEyebrow(group: GroupKey, kind: BoardKind) { return kind === defaultBoard(group) ? `L2 · ${group.toUpperCase()}` : `L3 · ${group.toUpperCase()} DRILLDOWN` }

function groupSessions(keyFn: (s: any) => string) {
  const map = new Map<string, any>()
  for (const s of sessions.value) {
    const key = keyFn(s) || '-'
    const row = map.get(key) || { name: key, total: 0, success: 0, failed: 0, alive: 0, risk: 0 }
    row.total += 1; row.success += s.query_success_count ? 1 : 0; row.failed += s.status === 'failed' ? 1 : 0; row.alive += Number(s.alive_seconds || 0); row.risk += countSessionText(s, /1015|429|cf|challenge|denied/i)
    map.set(key, row)
  }
  return [...map.values()].map((r) => ({ ...r, pct: Math.round(r.success / Math.max(1, r.total) * 100), sub: `成功 ${r.success}/${r.total} · 风险 ${r.risk}`, value: `${Math.round(r.success / Math.max(1, r.total) * 100)}%`, extra: `平均存活 ${duration(r.alive / Math.max(1, r.total))}` })).sort((a, b) => b.pct - a.pct || b.total - a.total).slice(0, 10)
}
function countPatterns(patterns: [string, RegExp][]) { return patterns.map(([name, re]) => ({ name, count: sessions.value.reduce((n, s) => n + (countSessionText(s, re) ? 1 : 0), 0), pct: Math.round(sessions.value.reduce((n, s) => n + (countSessionText(s, re) ? 1 : 0), 0) / Math.max(1, sessions.value.length) * 100) })).sort((a, b) => b.count - a.count) }
function countSessionText(s: any, re: RegExp) { return re.test(`${s.last_result || ''} ${s.last_message || ''} ${s.last_reason || ''} ${JSON.stringify(s.flow || [])} ${JSON.stringify(s.raw_flow || [])}`) ? 1 : 0 }
function hasStage(s: any, key: string) { return key === 'browser_launched' ? (s.flow || []).some((e: any) => e.event_type === 'browser_launched') : (s.flow || []).some((e: any) => `${e.stage || ''} ${e.event_type || ''}`.includes(key)) }
function stageOk(s: any, key: string) { return (s.flow || []).some((e: any) => `${e.stage || ''} ${e.event_type || ''}`.includes(key) && e.tone === 'good') || (key === 'proxy_acquire' && !!s.proxy_display) }
function sessionScore(s: any) { return Number(s.query_success_count || 0) * 100 + Number(s.uses_count || 0) * 30 + Math.min(120, Number(s.alive_seconds || 0) / 60) - Number(s.recovery_count || 0) * 12 - (s.status === 'failed' ? 50 : 0) }
function duration(sec?: number) { const s = Math.round(Number(sec || 0)); if (s < 60) return `${s}s`; const m = Math.floor(s / 60); if (m < 60) return `${m}m${s % 60}s`; return `${Math.floor(m / 60)}h${m % 60}m` }
function fmt(ts?: string) { if (!ts) return '—'; const d = new Date(ts); return Number.isNaN(d.getTime()) ? String(ts) : d.toLocaleString('zh-CN', { hour12: false }) }
function displaySlot(slot: string) { return slot ? slot.replace('slot_', 'Slot ') : '-' }
function sessionLabel(row: any) { return row?.session_label || `${displaySlot(row?.slot_id || '')} / ${row?.round_id || row?.session_id || '-'}` }
function maxVal(rows: any[], keys: string[]) { return Math.max(1, ...rows.flatMap((r) => keys.map((k) => Number(r[k] || 0)))) }

const ListBoard = defineComponent({
  props: { rows: { type: Array, default: () => [] } },
  setup(p) {
    return () => h('div', { class: 'rank-list' }, (p.rows as any[]).length
      ? (p.rows as any[]).map((r) => h('div', { class: 'rank-row' }, [
        h('b', r.name),
        h('span', r.sub || ''),
        h('strong', r.value || `${r.count ?? ''}`),
        h('em', r.extra || (r.pct !== undefined ? `${r.pct}%` : ''))
      ]))
      : h('p', { class: 'empty-hint' }, '暂无足够样本'))
  }
})

const BarBoard = defineComponent({
  props: { rows: { type: Array, default: () => [] }, keys: { type: Array, default: () => ['a'] } },
  setup(p) {
    return () => {
      const rows = p.rows as any[]
      const keys = p.keys as string[]
      const max = maxVal(rows, keys)
      return h('div', { class: 'bar-board' }, rows.map((r) => h('div', { class: 'bar-row' }, [
        h('span', r.label || r.hour || r.name),
        h('div', { class: 'bar-track' }, keys.map((k, i) => h('i', {
          class: `bar-${i}`,
          style: { width: `${Math.max(2, Math.round(Number(r[k] || 0) / max * 100))}%` },
          title: `${k}: ${r[k] || 0}`
        }))),
        h('em', keys.map((k) => `${k}:${r[k] || 0}`).join(' '))
      ])))
    }
  }
})

const FunnelBoard = defineComponent({
  setup() {
    return () => h('div', { class: 'funnel-board' }, funnelRows.value.map((r) => h('div', { class: 'funnel-row' }, [
      h('b', r.label),
      h('span', [h('i', { style: { width: `${Math.max(3, r.pct)}%` } })]),
      h('em', `进 ${r.entered} / 成 ${r.ok} / 失 ${r.fail}`)
    ])))
  }
})

const SessionBoard = defineComponent({
  props: { rows: { type: Array, default: () => [] } },
  setup(p) {
    return () => h('div', { class: 'session-board' }, (p.rows as any[]).length
      ? (p.rows as any[]).map((s) => h('button', { onClick: () => emit('open-session', s) }, [
        h('b', sessionLabel(s)),
        h('span', `${s.proxy_display || '-'} · ${s.status_zh || s.status} · 查 ${s.query_success_count || 0} · 存活 ${duration(s.alive_seconds)}`),
        h('em', s.last_result || '—')
      ]))
      : h('p', { class: 'empty-hint' }, '暂无可排序会话'))
  }
})

function boardComponent(kind: BoardKind) {
  const map: Record<BoardKind, any> = {
    proxy: defineComponent(() => () => h(ListBoard, { rows: proxyRows.value })),
    proxy_country: defineComponent(() => () => h(ListBoard, { rows: proxyCountryRows.value })),
    proxy_session: defineComponent(() => () => h(ListBoard, { rows: proxySessionRows.value })),
    funnel: FunnelBoard,
    risk_auth: defineComponent(() => () => h(ListBoard, { rows: riskAuthRows.value.map((x) => ({ name: x.name, value: x.count, sub: `影响 ${x.pct}% 会话`, extra: `${x.count} 次` })) })),
    business_fail: defineComponent(() => () => h(ListBoard, { rows: businessFailRows.value.map((x) => ({ name: x.name, value: x.count, sub: `影响 ${x.pct}% 会话`, extra: `${x.count} 次` })) })),
    trend: defineComponent(() => () => h(BarBoard, { rows: trendRows.value, keys: ['a', 'b', 'c'] })),
    release_trend: defineComponent(() => () => h(ListBoard, { rows: releaseRows.value })),
    risk_time: defineComponent(() => () => h(BarBoard, { rows: riskTimeRows.value, keys: ['risk', 'success'] })),
    session: defineComponent(() => () => h(SessionBoard, { rows: qualityRows.value })),
    top_sessions: defineComponent(() => () => h(SessionBoard, { rows: topSessionRows.value })),
    failure_causes: defineComponent(() => () => h(ListBoard, { rows: failureCauses.value.map((x) => ({ name: x.name, value: x.count, sub: `占比 ${x.pct}%`, extra: `${x.count} 会话` })) }))
  }
  return map[kind]
}
</script>

<style scoped>
.deep-analysis { display:flex; flex-direction:column; gap:16px; }
.analysis-toolbar { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:14px 16px; background:linear-gradient(135deg,#eff6ff,#fff); border:1px solid #dbeafe; border-radius:16px; }
.analysis-toolbar b { display:block; color:#0f172a; font-size:17px; }.analysis-toolbar span { display:block; color:#64748b; margin-top:3px; }.toolbar-actions { display:flex; gap:8px; flex-wrap:wrap; }.toolbar-actions button { border:1px solid #dbeafe; background:#fff; color:#2563eb; border-radius:10px; padding:8px 12px; font-weight:900; cursor:pointer; }.toolbar-actions button.active { background:#2563eb; color:#fff; border-color:#2563eb; }
.global-board { min-height: 560px; }.global-hero { display:grid; grid-template-columns:210px 1fr; gap:20px; align-items:center; padding:18px; border-radius:18px; background:#f8fafc; border:1px solid #e7edf6; }.health-ring{--pct:0%;width:180px;height:180px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(#22c55e var(--pct),#dbeafe var(--pct) 70%,#fee2e2 0);position:relative}.health-ring:after{content:"";position:absolute;width:120px;height:120px;background:#fff;border-radius:50%;box-shadow:inset 0 0 0 1px #e7edf6}.health-ring b,.health-ring span{position:relative;z-index:1}.health-ring b{font-size:36px;color:#0f172a}.health-ring span{margin-top:48px;margin-left:-70px;color:#64748b;font-size:12px;font-weight:900}.global-findings h4{font-size:24px;margin:0 0 8px;color:#0f172a}.global-findings p{color:#475569;line-height:1.7}.finding-tags{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.finding-tags button{border:0;background:#eaf2ff;color:#2563eb;border-radius:999px;padding:8px 12px;font-weight:900;cursor:pointer}.global-metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:16px 0}.global-metrics span{background:#fff;border:1px solid #e7edf6;border-radius:14px;padding:13px}.global-metrics small{display:block;color:#64748b}.global-metrics b{display:block;color:#0f172a;font-size:22px;margin:4px 0}.global-metrics em{font-style:normal;color:#94a3b8;font-size:12px}.global-lanes{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.global-lanes div{border:1px solid #e7edf6;border-radius:14px;padding:14px;background:#fff}.global-lanes h4{margin:0 0 8px;color:#0f172a}.global-lanes p{margin:0;color:#475569;line-height:1.6}.board-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.rank-list{display:grid;gap:9px}.rank-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:3px 10px;align-items:center;border-bottom:1px solid #edf2f7;padding-bottom:9px}.rank-row b{color:#0f172a;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.rank-row span{grid-column:1;color:#64748b;font-size:12px}.rank-row strong{grid-column:2;grid-row:1;color:#2563eb}.rank-row em{grid-column:2;grid-row:2;color:#94a3b8;font-size:12px;font-style:normal}.bar-board,.funnel-board,.session-board{display:grid;gap:9px}.bar-row,.funnel-row{display:grid;grid-template-columns:72px 1fr 115px;gap:9px;align-items:center}.bar-row span,.funnel-row b{color:#475569;font-weight:800}.bar-track,.funnel-row span{height:14px;background:#edf2f7;border-radius:999px;overflow:hidden;display:flex;align-items:center}.bar-track i,.funnel-row i{height:100%;display:block;border-radius:999px}.bar-0{background:#3b82f6}.bar-1{background:#22c55e}.bar-2{background:#f97316}.funnel-row i{background:linear-gradient(90deg,#60a5fa,#2563eb)}.bar-row em,.funnel-row em{font-style:normal;color:#64748b;font-size:12px}.session-board button{text-align:left;border:1px solid #e7edf6;background:#f8fafc;border-radius:12px;padding:10px;cursor:pointer}.session-board b{display:block;color:#2563eb}.session-board span{display:block;color:#475569;font-size:12px;margin-top:3px}.session-board em{display:block;color:#94a3b8;font-size:12px;font-style:normal;margin-top:2px}.empty-hint{color:#94a3b8;margin:12px 0}
@media(max-width:1100px){.board-grid,.global-metrics,.global-lanes{grid-template-columns:1fr}.global-hero{grid-template-columns:1fr}.analysis-toolbar{align-items:flex-start;flex-direction:column}.global-board{min-height:auto}}
</style>

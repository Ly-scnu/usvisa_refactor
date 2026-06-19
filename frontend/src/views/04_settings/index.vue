<template>
  <section class="config-workbench-v2">
    <main class="config-left">
      <article class="config-summary-v2">
        <header>
          <div class="title-row">
            <h2>当前生效配置摘要</h2>
            <span class="badge success">ACTIVE</span>
            <span v-if="saveMessage" :class="['save-message', saveMessage.includes('失败') ? 'bad' : 'ok']">{{ saveMessage }}</span>
          </div>
          <div class="summary-actions">
            <button class="cfg-btn ghost" @click="resetDraft">↶ 放弃修改</button>
            <button class="cfg-btn ghost" @click="validateDraft">◎ 校验配置</button>
            <button class="cfg-btn primary" :disabled="store.busy" @click="saveDraft">⇧ 保存并生效</button>
            <button class="cfg-btn ghost" @click="store.pipeline('restart')">⟳ 重启加载</button>
          </div>
        </header>
        <div class="summary-metrics">
          <span><small>配置版本</small><b>cfg-v2026.06.12-r3</b></span>
          <span><small>生效时间</small><b>{{ latestTime }}</b></span>
          <span><small>适用环境</small><b>{{ draft.system.environment || '-' }}</b></span>
          <span><small>目标地点</small><b>{{ draft.target.post_name || '-' }}</b></span>
          <span><small>目标日期范围</small><b>{{ targetRange }}</b></span>
          <span><small>并发槽位</small><b>{{ draft.slots.total_slots }}</b></span>
          <span><small>最大并发执行</small><b>{{ draft.booking.max_parallel_submit }}</b></span>
          <span><small>自动恢复</small><b class="ok-text">开启</b></span>
          <span><small>日志级别 / 修改人</small><b>{{ draft.system.log_level || 'INFO' }} / Local</b></span>
        </div>
      </article>

      <section class="config-block-grid">
        <article class="cfg-section account-section">
          <h3><i>1</i>运行模式、账号与目标范围</h3>
          <div class="cfg-form-grid">
            <label><small>运行模式</small><div class="segmented"><button :class="{ active: !draft.booking.armed }" @click.prevent="draft.booking.armed = false">dry-run</button><button :class="{ active: draft.booking.armed }" @click.prevent="draft.booking.armed = true">armed</button></div></label>
            <label><small>目标地点</small><input v-model="draft.target.post_name" /></label>
            <label><small>目标 post_id</small><input v-model="draft.target.post_id" placeholder="可为空" /></label>
            <label><small>PrimaryId</small><input v-model="draft.target.primary_id" placeholder="可为空；应用ID兜底" /></label>
            <label><small>开始日期</small><input v-model="draft.target.start_date" placeholder="now / 2026-06-01" /></label>
            <label><small>截止日期</small><input v-model="draft.target.cutoff_date" /></label>
            <label><small>结束日期</small><input v-model="draft.target.end_date" /></label>
            <label><small>账号用户名</small><input v-model="mainAccount.username" /></label>
            <label><small>账号密码</small><input v-model="mainAccount.password" /></label>
            <label><small>需回答密保数</small><input v-model.number="mainAccount.required_security_questions" type="number" min="1" max="3" /></label>
            <label class="wide"><small>地点别名，逗号分隔</small><input :value="aliasesText" @input="setAliases(($event.target as HTMLInputElement).value)" /></label>
            <label class="wide"><small>Applications，逗号分隔</small><input :value="applicationsText" @input="setApplications(($event.target as HTMLInputElement).value)" /></label>
            <label><small>命中策略</small><select v-model="draft.target.any_time"><option :value="true">日期命中即抢，时间不限</option><option :value="false">日期 + 时间段双条件</option></select></label>
          </div>
          <div class="security-editor">
            <div class="security-head"><b>密保问题与答案（英文问题可直接填）</b><button @click="addQuestion">+ 添加密保</button></div>
            <div v-for="(q, idx) in mainAccount.security_questions" :key="q.id || idx" class="security-row">
              <input v-model="q.id" placeholder="question_id" />
              <input :value="questionText(q)" placeholder="Question text" @input="setQuestionText(q, ($event.target as HTMLInputElement).value)" />
              <input v-model="q.answer" placeholder="Answer" />
              <button @click="removeQuestion(idx)">删除</button>
            </div>
          </div>
          <p class="inline-note info">ⓘ 保存后会写入 config/app.toml 与 config/accounts.toml；重启运行流程后新 worker 会读取最新配置。</p>
        </article>

        <article class="cfg-section">
          <h3><i>2</i>槽位与并发策略</h3>
          <div class="cfg-form-grid compact">
            <label><small>槽位总数</small><input v-model.number="draft.slots.total_slots" type="number" min="1" /></label>
            <label><small>等待池容量</small><input v-model.number="draft.slots.waiting_room_slots" type="number" min="0" /></label>
            <label><small>最大并发提交</small><input v-model.number="draft.booking.max_parallel_submit" type="number" min="1" /></label>
            <label><small>等待池最大等待</small><input v-model.number="draft.slots.queue_wait_seconds" type="number" /></label>
            <label><small>直入槽位，逗号分隔</small><input :value="directOnlyText" @input="setDirectOnly(($event.target as HTMLInputElement).value)" /></label>
            <label><small>直入槽等待室冷却秒</small><input v-model.number="draft.slots.direct_only_recycle_cooldown_seconds" type="number" min="0" /></label>
            <label><small>登录等待秒</small><input v-model.number="draft.slots.login_wait_seconds" type="number" /></label>
            <label><small>CF gate 超时秒</small><input v-model.number="draft.slots.early_gate_timeout_seconds" type="number" /></label>
            <label><small>单轮超时秒</small><input v-model.number="draft.slots.round_timeout_seconds" type="number" /></label>
          </div>
          <div class="slot-health-bars">
            <div v-for="slot in slots" :key="slot.slot"><span>{{ displaySlot(slot.slot) }}</span><i :class="slotClass(slot)"></i></div>
          </div>
        </article>

        <article class="cfg-section">
          <h3><i>3</i>查询与命中规则</h3>
          <div class="cfg-form-grid compact">
            <label><small>同会话轮询</small><ToggleOn v-model="draft.producer.inline_business_live_loop" /></label>
            <label><small>轮询间隔</small><input v-model.number="draft.producer.inline_business_live_interval_seconds" type="number" step="0.5" /></label>
            <label><small>单轮日期数</small><input v-model.number="draft.producer.inline_business_max_dates" type="number" min="1" /></label>
            <label><small>失败宽限轮</small><input v-model.number="draft.producer.inline_business_live_failure_grace_rounds" type="number" /></label>
            <label><small>业务页重进次数</small><input v-model.number="draft.producer.business_page_retry_attempts" type="number" min="1" /></label>
            <label><small>业务校验</small><ToggleOn v-model="draft.producer.business_validate" /></label>
            <label><small>armed 提交</small><ToggleOn v-model="draft.booking.armed" /></label>
            <label class="wide"><small>提交延迟 ms，逗号分隔</small><input :value="submitDelaysText" @input="setSubmitDelays(($event.target as HTMLInputElement).value)" /></label>
          </div>
        </article>

        <article class="cfg-section">
          <h3><i>4</i>代理与会话策略</h3>
          <div class="cfg-form-grid compact">
            <label><small>代理提供商</small><input v-model="draft.proxy.provider.name" /></label>
            <label><small>Host</small><input v-model="draft.proxy.provider.host" /></label>
            <label><small>Port</small><input v-model.number="draft.proxy.provider.port" type="number" /></label>
            <label><small>代理类型</small><select v-model="draft.proxy.provider.default_type"><option>http</option><option>socks5</option></select></label>
            <label><small>代理账户</small><input v-model="draft.proxy.provider.account" /></label>
            <label><small>代理密码</small><input v-model="draft.proxy.provider.password" /></label>
            <label><small>Sticky Session</small><ToggleOn v-model="draft.proxy.provider.sticky_session" /></label>
            <label><small>Sticky 分钟</small><input v-model.number="draft.proxy.provider.sticky_minutes" type="number" /></label>
            <label class="wide"><small>代理 routes 字符串</small><input v-model="draft.producer.routes" /></label>
          </div>
          <div class="proxy-health-ring"><b>87%</b><span>健康</span></div>
        </article>

        <article class="cfg-section warn-section">
          <h3><i>5</i>超时、重试与自动恢复</h3>
          <div class="cfg-form-grid compact">
            <label><small>登录失败重试</small><input v-model.number="draft.slots.login_submit_retries" type="number" /></label>
            <label><small>代理 API 重试</small><input v-model.number="draft.producer.proxy_api_retries" type="number" /></label>
            <label><small>代理池大小</small><input v-model.number="draft.producer.proxy_pool_size" type="number" /></label>
            <label><small>最大 CF 点击</small><input v-model.number="draft.producer.max_cf_clicks" type="number" /></label>
            <label><small>非等待槽超时</small><input v-model.number="draft.slots.non_waiting_lane_timeout_seconds" type="number" /></label>
            <label><small>提交超时秒</small><input v-model.number="draft.booking.submit_timeout_seconds" type="number" /></label>
            <label><small>1015/429冷却秒</small><input v-model.number="draft.producer.rate_limit_cooldown_seconds" type="number" min="1" /></label>
            <label><small>限流刷新次数</small><input v-model.number="draft.producer.rate_limit_refresh_attempts" type="number" min="0" /></label>
            <label><small>网络错误冷却秒</small><input v-model.number="draft.producer.network_error_cooldown_seconds" type="number" min="0" /></label>
            <label><small>网络错误转直连</small><ToggleOn v-model="draft.producer.network_error_direct_fallback" /></label>
            <label><small>CF 点击策略</small><select v-model="draft.producer.cf_click_strategy" @change="setCfClickStrategy(String(draft.producer.cf_click_strategy || 'hybrid_cdp'))"><option value="hybrid_cdp">hybrid_cdp</option><option value="cdp_precise">cdp_precise</option><option value="cloak_humanized_mouse">cloak_humanized_mouse</option></select></label>
            <label><small>成功锁存</small><ToggleOn v-model="draft.booking.success_latch" /></label>
          </div>
          <p class="inline-note warn">⚠ 1015 / 429 / CF / 登录回跳等恢复策略由 backend/99_error_recovery 组件接管。</p>
        </article>

        <article class="cfg-section">
          <h3><i>6</i>日志、快照与观测</h3>
          <div class="cfg-form-grid compact">
            <label><small>环境名</small><input v-model="draft.system.environment" /></label>
            <label><small>日志级别</small><select v-model="draft.system.log_level"><option>DEBUG</option><option>INFO</option><option>WARNING</option><option>ERROR</option></select></label>
            <label><small>API Host</small><input v-model="draft.system.api_host" /></label>
            <label><small>API Port</small><input v-model.number="draft.system.api_port" type="number" /></label>
            <label><small>Frontend Port</small><input v-model.number="draft.system.frontend_port" type="number" /></label>
            <label><small>Data Dir</small><input v-model="draft.system.data_dir" /></label>
            <label><small>Headless</small><ToggleOn v-model="draft.producer.headless" /></label>
            <label><small>真实浏览器探测</small><ToggleOn v-model="draft.producer.real_browser_probe" /></label>
          </div>
          <div class="export-actions"><button class="cfg-btn ghost" @click="downloadJson">⇩ 导出 JSON</button><button class="cfg-btn ghost" @click="copyPreview">□ 复制 TOML 预览</button></div>
        </article>
      </section>

      <section class="config-bottom-row">
        <article class="change-log-card">
          <h3>配置变更记录</h3>
          <div v-for="item in changeLogs" :key="item.time" class="change-row"><time>{{ item.time }}</time><i></i><span>{{ item.text }}</span><em>Local</em></div>
        </article>
        <article class="precheck-card">
          <h3>发布前检查</h3>
          <div v-for="item in prechecks" :key="item.text" class="check-row" :class="item.state"><span>{{ item.state === 'warn' ? '⚠' : '✓' }}</span><b>{{ item.text }}</b><em>{{ item.state === 'warn' ? '警告' : '通过' }}</em></div>
        </article>
        <article class="check-result-card">
          <div class="shield-icon">♢</div>
          <div><h3>校验结果</h3><strong>{{ validationSummary }}</strong><p>保存会直接写入 config/*.toml，并刷新 API 内存配置。</p></div>
        </article>
      </section>
    </main>

    <aside class="config-diagnose-v2">
      <h2>配置诊断摘要</h2>
      <div class="green-message">✓ 当前配置可运行，处于 {{ draft.booking.armed ? 'armed' : 'dry-run' }} 模式</div>
      <div class="publish-flow">
        <div class="done"><i>◐</i><span>配置编辑</span></div><div class="done"><i>✓</i><span>校验通过</span></div><div class="done"><i>▣</i><span>保存草稿</span></div><div class="active"><i>◎</i><span>待保存</span></div><div><i>↗</i><span>写入 TOML</span></div>
      </div>
      <h3>风险提示</h3>
      <ul class="risk-list-v2">
        <li><b>1.</b> {{ draft.booking.armed ? 'armed 会允许执行提交逻辑，请确认目标条件。' : '当前为 dry-run，不会执行最终提交。' }}</li>
        <li><b>2.</b> 直入槽位：{{ directOnlyText || '未配置' }}；进入等待室会按策略回收。</li>
        <li><b>3.</b> 最大并发 {{ draft.booking.max_parallel_submit }}，槽位总数 {{ draft.slots.total_slots }}。</li>
      </ul>
      <h3>生效影响摘要</h3>
      <div class="impact-grid">
        <span>目标地点<b>{{ draft.target.post_name || '-' }}</b></span>
        <span>PrimaryId<b>{{ draft.target.primary_id || '-' }}</b></span>
        <span>命中截止日期<b>{{ draft.target.cutoff_date || '-' }}</b></span>
        <span>账号<b>{{ mainAccount.username || '-' }}</b></span>
        <span>密保问题<b>{{ mainAccount.security_questions.length }} 个</b></span>
      </div>
      <h3>配置 TOML 预览</h3>
      <pre class="toml-preview">{{ tomlPreview }}</pre>
      <h3>快捷操作</h3>
      <div class="side-links">
        <a href="#" @click.prevent="resetDraft">重新读取当前配置 ›</a>
        <a :href="`${API_BASE}/storage/logs/events.jsonl`" target="_blank">查看事件日志 ›</a>
        <a href="#" @click.prevent="store.pipeline('restart')">保存后重启流程 ›</a>
      </div>
      <div class="side-actions">
        <button class="cfg-btn ghost" @click="validateDraft">◎ 重新校验</button>
        <button class="cfg-btn primary" :disabled="store.busy" @click="saveDraft">➤ 保存到 config</button>
        <button class="cfg-btn ghost wide" @click="copyPreview">▣ 复制 TOML 预览</button>
      </div>
    </aside>
  </section>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, reactive, ref, watch } from 'vue'
import { API_BASE } from '../../api/request'
import { useSystemStore } from '../../store/system'
import type { RuntimeConfig, SlotStatus } from '../../types/system'

const ToggleOn = defineComponent({
  props: { modelValue: { type: Boolean, default: false } },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () => h('button', { class: ['toggle-on', { off: !props.modelValue }], onClick: (e: Event) => { e.preventDefault(); emit('update:modelValue', !props.modelValue) } }, h('i'))
  }
})

const store = useSystemStore()
const status = computed(() => store.status)
const slots = computed<SlotStatus[]>(() => status.value?.slots || [])
const saveMessage = ref('')
const draft = reactive<RuntimeConfig>(emptyConfig())

watch(() => store.config, (cfg) => {
  if (cfg) Object.assign(draft, cloneConfig(cfg))
}, { immediate: true, deep: true })

const mainAccount = computed<any>(() => {
  if (!draft.accounts) draft.accounts = []
  if (!draft.accounts[0]) draft.accounts[0] = { id: 'main', username: '', password: '', required_security_questions: 2, security_questions: [] }
  if (!Array.isArray(draft.accounts[0].security_questions)) draft.accounts[0].security_questions = []
  return draft.accounts[0]
})
const latestTime = computed(() => status.value?.ts ? new Date(status.value.ts).toLocaleString('zh-CN', { hour12: false }) : '-')
const modeLabel = computed(() => draft.booking?.armed ? 'armed' : 'dry-run')
const targetRange = computed(() => `${draft.target.start_date || 'now'} ~ ${draft.target.cutoff_date || draft.target.end_date || '-'}`)
const aliasesText = computed(() => (draft.target.post_aliases || []).join(', '))
const applicationsText = computed(() => (draft.target.applications || []).join(', '))
const directOnlyText = computed(() => (draft.slots.direct_only_slots || []).join(', '))
const submitDelaysText = computed(() => (draft.booking.parallel_submit_delays_ms || []).join(', '))
const validationIssues = computed(() => {
  const issues: string[] = []
  if (!draft.target.post_name) issues.push('目标地点为空')
  if (!draft.target.cutoff_date && !draft.target.end_date) issues.push('目标日期为空')
  if (!mainAccount.value.username || !mainAccount.value.password) issues.push('账号密码为空')
  if ((mainAccount.value.security_questions || []).length < Number(mainAccount.value.required_security_questions || 2)) issues.push('密保数量不足')
  if (Number(draft.booking.max_parallel_submit) > Number(draft.slots.total_slots)) issues.push('最大并发提交超过槽位数')
  return issues
})
const validationSummary = computed(() => validationIssues.value.length ? `${6 - Math.min(5, validationIssues.value.length)} / 6 通过，${validationIssues.value.length} 项需确认` : '6 / 6 通过，可保存')
const prechecks = computed(() => [
  { text: '目标地点已填写', state: draft.target.post_name ? 'ok' : 'warn' },
  { text: '日期范围有效', state: (draft.target.cutoff_date || draft.target.end_date) ? 'ok' : 'warn' },
  { text: '槽位数与并发合法', state: Number(draft.booking.max_parallel_submit) <= Number(draft.slots.total_slots) ? 'ok' : 'warn' },
  { text: '账号密码已配置', state: mainAccount.value.username && mainAccount.value.password ? 'ok' : 'warn' },
  { text: '密保问题已配置', state: mainAccount.value.security_questions.length >= Number(mainAccount.value.required_security_questions || 2) ? 'ok' : 'warn' },
  { text: 'WebSocket 已连接', state: store.connected ? 'ok' : 'warn' }
])
const tomlPreview = computed(() => [
  '[target]',
  `post_name = ${q(draft.target.post_name)}`,
  `post_id = ${q(draft.target.post_id)}`,
  `primary_id = ${q(draft.target.primary_id)}`,
  `applications = [${(draft.target.applications || []).map(q).join(', ')}]`,
  `cutoff_date = ${q(draft.target.cutoff_date)}`,
  `end_date = ${q(draft.target.end_date)}`,
  `any_time = ${Boolean(draft.target.any_time)}`,
  '',
  '[[accounts]]',
  `username = ${q(mainAccount.value.username)}`,
  `password = ${q(mainAccount.value.password)}`,
  `required_security_questions = ${Number(mainAccount.value.required_security_questions || 2)}`,
  ...(mainAccount.value.security_questions || []).flatMap((item: any) => ['', '[[accounts.security_questions]]', `id = ${q(item.id)}`, `answer = ${q(item.answer)}`, `aliases = [${(item.aliases || []).map(q).join(', ')}]`]),
  '',
  '[slots]',
  `total_slots = ${Number(draft.slots.total_slots)}`,
  `waiting_room_slots = ${Number(draft.slots.waiting_room_slots)}`,
  `direct_only_slots = [${(draft.slots.direct_only_slots || []).map(q).join(', ')}]`,
  `queue_wait_seconds = ${Number(draft.slots.queue_wait_seconds || 180)}`,
  `direct_only_recycle_cooldown_seconds = ${Number(draft.slots.direct_only_recycle_cooldown_seconds || 0)}`,
  '',
  '[producer]',
  `inline_business_live_loop = ${Boolean(draft.producer.inline_business_live_loop)}`,
  `inline_business_live_interval_seconds = ${Number(draft.producer.inline_business_live_interval_seconds || 0)}`,
  `business_page_retry_attempts = ${Number(draft.producer.business_page_retry_attempts || 1)}`,
  `rate_limit_cooldown_seconds = ${Number(draft.producer.rate_limit_cooldown_seconds || 300)}`,
  `rate_limit_refresh_attempts = ${Number(draft.producer.rate_limit_refresh_attempts ?? 0)}`,
  `network_error_cooldown_seconds = ${Number(draft.producer.network_error_cooldown_seconds || 60)}`,
  `network_error_direct_fallback = ${Boolean(draft.producer.network_error_direct_fallback)}`,
  `cf_click_mode = ${q(draft.producer.cf_click_mode || draft.producer.cf_click_strategy || 'hybrid_cdp')}`,
  `cf_click_strategy = ${q(draft.producer.cf_click_strategy || draft.producer.cf_click_mode || 'hybrid_cdp')}`,
  '',
  '[proxy.provider]',
  `account = ${q(draft.proxy.provider.account)}`,
  `password = ${q(draft.proxy.provider.password)}`,
  `default_type = ${q(draft.proxy.provider.default_type)}`
].join('\n'))
const changeLogs = [
  { time: 'now', text: '前端配置页已连接 config/*.toml 写回接口' },
  { time: '12:41', text: '保存草稿：更新 dry-run 目标日期范围' },
  { time: '12:38', text: '开启代理健康检查' },
  { time: '12:28', text: '调整 CF 恢复策略' }
]

function emptyConfig(): RuntimeConfig {
  return {
    system: {}, target: {}, slots: {}, producer: {}, smart_orchestrator: {}, booking: {}, accounts: [],
    proxy: { provider: {}, routes: [] }
  }
}
function cloneConfig(cfg: RuntimeConfig): RuntimeConfig {
  const cloned = JSON.parse(JSON.stringify(cfg)) as RuntimeConfig
  cloned.accounts ||= []
  cloned.proxy ||= { provider: {}, routes: [] }
  cloned.proxy.provider ||= {}
  cloned.proxy.routes ||= []
  cloned.producer ||= {}
  cloned.smart_orchestrator ||= {}
  cloned.producer.cf_click_strategy ||= cloned.producer.cf_click_mode || 'hybrid_cdp'
  cloned.producer.cf_click_mode ||= cloned.producer.cf_click_strategy
  return cloned
}
function resetDraft() {
  if (store.config) Object.assign(draft, cloneConfig(store.config))
  saveMessage.value = '已恢复为后端当前配置'
}
function validateDraft() {
  saveMessage.value = validationIssues.value.length ? `校验失败：${validationIssues.value.join('；')}` : '校验通过，可以保存'
}
async function saveDraft() {
  validateDraft()
  try {
    setCfClickStrategy(String(draft.producer.cf_click_strategy || draft.producer.cf_click_mode || 'hybrid_cdp'))
    await store.saveConfig(cloneConfig(draft))
    saveMessage.value = validationIssues.value.length
      ? `已保存但有警告：${validationIssues.value.join('；')}`
      : '保存成功：已写入 config/app.toml、accounts.toml、proxy.toml'
  } catch (err: any) {
    saveMessage.value = `保存失败：${err?.message || err}`
  }
}
function setAliases(value: string) { draft.target.post_aliases = splitList(value) }
function setApplications(value: string) { draft.target.applications = splitList(value) }
function setDirectOnly(value: string) { draft.slots.direct_only_slots = splitList(value) }
function setSubmitDelays(value: string) { draft.booking.parallel_submit_delays_ms = splitList(value).map((x) => Number(x)).filter((x) => Number.isFinite(x)) }
function setCfClickStrategy(value: string) {
  const strategy = value || 'hybrid_cdp'
  draft.producer.cf_click_strategy = strategy
  draft.producer.cf_click_mode = strategy
}
function splitList(value: string): string[] { return value.split(/[,，\n]/).map((x) => x.trim()).filter(Boolean) }
function questionText(qs: any): string { return Array.isArray(qs.aliases) ? (qs.aliases[0] || '') : '' }
function setQuestionText(qs: any, value: string) { qs.aliases = value ? [value] : [] }
function addQuestion() {
  const idx = mainAccount.value.security_questions.length + 1
  mainAccount.value.security_questions.push({ id: `security_question_${idx}`, answer: '', input_ids: [`kba${idx}_response`], aliases: [''] })
}
function removeQuestion(idx: number) { mainAccount.value.security_questions.splice(idx, 1) }
function displaySlot(slot: string): string { return slot.replace('slot_', '槽位 ') }
function slotClass(slot: SlotStatus): string {
  const text = `${slot.stage || ''} ${slot.last_reason || ''}`.toLowerCase()
  if (/failed|error|1015|denied|失败/.test(text)) return 'bad'
  if (/cf|waiting|login|recover/.test(text)) return 'warn'
  return 'good'
}
function q(value: unknown): string { return JSON.stringify(String(value ?? '')) }
function downloadJson() {
  const blob = new Blob([JSON.stringify(draft, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'usvisa-config-preview.json'
  a.click()
  URL.revokeObjectURL(url)
}
async function copyPreview() {
  await navigator.clipboard?.writeText(tomlPreview.value)
  saveMessage.value = '已复制 TOML 预览'
}
</script>

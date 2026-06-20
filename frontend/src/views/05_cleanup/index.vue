<template>
  <section class="cleanup-page">
    <header class="cleanup-hero">
      <div>
        <h2>清理 / 存储治理中心</h2>
        <p>清理日志、截图、调试证据、临时文件和可选运行历史；所有删除都限制在当前项目目录内，并默认保护账号、配置、数据库和实时状态。</p>
      </div>
      <div class="cleanup-actions">
        <span :class="['sync-pill', error ? 'bad' : busy ? 'syncing' : 'ok']"><i></i>{{ statusText }}</span>
        <button :disabled="busy" @click="refreshAll">刷新</button>
        <button :disabled="busy" @click="preview">预演</button>
        <button class="danger" :disabled="busy || !previewData.count" @click="runCleanup">执行清理</button>
      </div>
    </header>

    <section class="cleanup-metrics">
      <article><small>storage 总占用</small><b>{{ summary.storage_size_text || '-' }}</b><span>{{ summary.project_root || '等待读取项目路径' }}</span></article>
      <article><small>默认可清理项</small><b>{{ enabledCount }} 类</b><span>危险项默认关闭</span></article>
      <article><small>本次预估释放</small><b>{{ previewData.total_size_text || '0 B' }}</b><span>{{ previewData.count || 0 }} 个对象</span></article>
      <article><small>保护规则</small><b>{{ protectedCount }}</b><span>配置、DB、实时状态不会删</span></article>
    </section>

    <section class="cleanup-layout">
      <main class="cleanup-main-card">
        <div class="cleanup-toolbar">
          <div><h3>清理规则</h3><p>先选择类别和保留天数，再预演；预演确认后才执行。</p></div>
          <label class="retention-box">保留最近 <input v-model.number="retentionDays" min="0" max="365" type="number" /> 天</label>
        </div>

        <div class="category-grid">
          <label v-for="cat in categories" :key="cat.key" :class="['category-card', { active: selectedCategories.includes(cat.key), danger: cat.dangerous }]">
            <input v-model="selectedCategories" :value="cat.key" type="checkbox" />
            <div><b>{{ cat.label }}</b><span>{{ cat.description }}</span><em>{{ cat.count }} 项 · {{ cat.size_text }} · 最旧 {{ cat.oldest_age_days }} 天</em></div>
            <strong v-if="cat.dangerous">高风险</strong>
          </label>
        </div>

        <section class="preview-panel">
          <div class="panel-head">
            <div><h3>预演结果</h3><p>{{ previewHint }}</p></div>
            <div class="panel-actions"><button :disabled="busy" @click="selectDefault">默认选择</button><button :disabled="busy" @click="selectedCategories = categories.map((x) => x.key)">全选</button><button :disabled="busy" @click="selectedCategories = []">清空</button></div>
          </div>
          <div class="cleanup-table-wrap">
            <table class="cleanup-table">
              <thead><tr><th>类别</th><th>路径</th><th>类型</th><th>大小</th><th>距今</th><th>修改时间</th><th>风险</th></tr></thead>
              <tbody>
                <tr v-for="item in previewItems" :key="item.category + '-' + item.path">
                  <td><span :class="['cat-pill', item.dangerous ? 'danger' : 'safe']">{{ item.category_label }}</span></td>
                  <td class="path-cell" :title="item.path">{{ item.path }}</td>
                  <td>{{ item.type === 'dir' ? '目录' : '文件' }}</td>
                  <td><b>{{ item.size_text }}</b></td>
                  <td>{{ item.age_days }} 天</td>
                  <td>{{ fmt(item.modified_at) }}</td>
                  <td>{{ item.dangerous ? '需确认' : '安全' }}</td>
                </tr>
                <tr v-if="!previewItems.length"><td colspan="7" class="empty-cell">暂无预演结果。点击“预演”后会列出将被删除的对象。</td></tr>
              </tbody>
            </table>
          </div>
        </section>
      </main>

      <aside class="cleanup-side">
        <article class="side-card"><h3>安全护栏</h3><ul><li>只允许删除项目目录内文件</li><li>真实配置 config/*.toml 不在规则内</li><li>数据库和实时状态 JSON 默认保护</li><li>浏览器画像/历史 JSONL 默认关闭</li></ul></article>
        <article class="side-card result-card"><h3>最近执行结果</h3><div v-if="lastRun.generated_at" class="run-result"><b>释放 {{ lastRun.freed_size_text }}</b><span>删除 {{ lastRun.deleted_count }} 项，失败 {{ lastRun.failed_count }} 项</span><em>{{ fmt(lastRun.generated_at) }}</em></div><p v-else class="muted">还没有执行过清理。本页默认先预演，不会直接删除。</p><button :disabled="!lastRun.generated_at" @click="exportReport">导出报告 JSON</button></article>
      </aside>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { apiGet, apiPostJson } from '../../api/request'

type Category = { key: string; label: string; description: string; default_enabled: boolean; dangerous: boolean; count: number; size: number; size_text: string; oldest_age_days: number }
type PreviewItem = { category: string; category_label: string; path: string; type: string; size: number; size_text: string; age_days: number; modified_at: string; dangerous: boolean }
const summary = ref<Record<string, any>>({ categories: [], storage_size_text: '等待统计', guardrails: { protected_names: [] } })
const previewData = ref<Record<string, any>>({ count: 0, total_size_text: '0 B', items: [] })
const lastRun = ref<Record<string, any>>({})
const selectedCategories = ref<string[]>([])
const retentionDays = ref(3)
const busy = ref(false)
const error = ref('')
const lastRefreshAt = ref('')
const categories = computed<Category[]>(() => summary.value.categories || [])
const enabledCount = computed(() => selectedCategories.value.length)
const protectedCount = computed(() => (summary.value.guardrails?.protected_names || []).length)
const previewItems = computed<PreviewItem[]>(() => previewData.value.items || [])
const statusText = computed(() => error.value ? `失败：${error.value}` : busy.value ? '处理中' : lastRefreshAt.value ? `已同步 · ${fmt(lastRefreshAt.value)}` : '等待刷新')
const previewHint = computed(() => previewData.value.generated_at ? `将清理 ${previewData.value.count || 0} 项，预计释放 ${previewData.value.total_size_text || '0 B'}；保留最近 ${retentionDays.value} 天。` : '当前还没有预演数据。')
onMounted(refreshAll)
async function refreshAll() { await withBusy(loadSummary) }
async function loadSummary() { summary.value = await apiGet('/api/cleanup/summary'); if (!selectedCategories.value.length) selectDefault(); lastRefreshAt.value = new Date().toISOString() }
function selectDefault() { selectedCategories.value = categories.value.filter((x) => x.default_enabled).map((x) => x.key) }
async function preview() { await withBusy(loadPreview) }
async function loadPreview() { previewData.value = await apiPostJson('/api/cleanup/preview', { categories: selectedCategories.value, retention_days: retentionDays.value }); lastRefreshAt.value = new Date().toISOString() }
async function runCleanup() { const danger = categories.value.some((x) => x.dangerous && selectedCategories.value.includes(x.key)); if (!window.confirm(danger ? '你选择了高风险清理项。确认执行？' : `确认删除预演中的 ${previewData.value.count || 0} 项？`)) return; await withBusy(async () => { lastRun.value = await apiPostJson('/api/cleanup/run', { confirm: true, categories: selectedCategories.value, retention_days: retentionDays.value }); await loadSummary(); await loadPreview() }) }
async function withBusy(fn: () => Promise<void>) { busy.value = true; error.value = ''; try { await fn() } catch (e: any) { error.value = e?.message || String(e) } finally { busy.value = false } }
function fmt(ts?: string) { if (!ts) return '-'; const d = new Date(ts); return Number.isNaN(d.getTime()) ? String(ts) : d.toLocaleString('zh-CN', { hour12: false }) }
function exportReport() { const blob = new Blob([JSON.stringify({ summary: summary.value, preview: previewData.value, last_run: lastRun.value }, null, 2)], { type: 'application/json' }); const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `cleanup_report_${Date.now()}.json`; a.click(); URL.revokeObjectURL(a.href) }
</script>

<style scoped>
.cleanup-page{display:flex;flex-direction:column;gap:16px}.cleanup-hero{display:flex;justify-content:space-between;gap:16px}.cleanup-hero h2{margin:0;font-size:22px;color:#0f172a}.cleanup-hero p{margin:6px 0 0;color:#64748b;max-width:760px}.cleanup-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.cleanup-actions button,.panel-actions button,.result-card button{border:1px solid #dbeafe;background:#eff6ff;color:#2563eb;border-radius:10px;padding:9px 13px;font-weight:800}.cleanup-actions .danger{border-color:#fecaca;background:#fff1f2;color:#dc2626}.sync-pill{display:inline-flex;align-items:center;gap:7px;border:1px solid #dbeafe;background:#f8fafc;color:#64748b;border-radius:999px;padding:8px 11px;font-size:12px;font-weight:800}.sync-pill i{width:8px;height:8px;border-radius:50%;background:#22c55e}.sync-pill.bad{border-color:#fecaca;background:#fff1f2;color:#dc2626}.cleanup-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.cleanup-metrics article,.cleanup-main-card,.side-card{background:#fff;border:1px solid #e3eaf5;border-radius:16px;box-shadow:0 10px 26px rgba(15,23,42,.05)}.cleanup-metrics article{padding:16px;display:grid;gap:5px}.cleanup-metrics small{color:#64748b}.cleanup-metrics b{color:#0f172a;font-size:24px}.cleanup-metrics span{color:#94a3b8;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.cleanup-layout{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:18px;align-items:start}.cleanup-main-card,.side-card{padding:16px}.cleanup-toolbar,.panel-head{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;margin-bottom:14px}.cleanup-toolbar h3,.panel-head h3,.side-card h3{margin:0;color:#0f172a}.cleanup-toolbar p,.panel-head p{margin:5px 0 0;color:#64748b}.retention-box{display:flex;align-items:center;gap:8px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:9px 12px;color:#475569;font-weight:800}.retention-box input{width:70px;border:1px solid #cbd5e1;border-radius:8px;padding:7px;font-weight:800}.category-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.category-card{position:relative;display:flex;gap:10px;padding:14px;border:1px solid #e4ebf5;border-radius:14px;background:#fff;cursor:pointer}.category-card.active{border-color:#93c5fd;background:#eff6ff}.category-card.danger.active{border-color:#fdba74;background:#fff7ed}.category-card b{display:block;color:#0f172a}.category-card span{display:block;color:#64748b;font-size:12px;line-height:1.45;margin:4px 0}.category-card em{color:#2563eb;font-size:12px;font-style:normal;font-weight:800}.category-card strong{position:absolute;right:12px;top:12px;color:#f97316;background:#ffedd5;border-radius:999px;padding:3px 7px;font-size:11px}.preview-panel{margin-top:16px;border-top:1px solid #e5ebf4;padding-top:16px}.panel-actions{display:flex;gap:8px}.cleanup-table-wrap{overflow:auto;border:1px solid #e5ebf4;border-radius:14px}.cleanup-table{width:100%;border-collapse:collapse;min-width:980px;background:#fff}.cleanup-table th,.cleanup-table td{padding:12px 14px;border-bottom:1px solid #edf2f7;text-align:left;color:#475569;font-size:13px}.cleanup-table th{background:#f8fafc;color:#64748b;font-weight:900}.path-cell{max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#2563eb!important;font-weight:700}.cat-pill{border-radius:999px;padding:4px 8px;font-weight:900;font-size:12px}.cat-pill.safe{background:#dcfce7;color:#16a34a}.cat-pill.danger{background:#ffedd5;color:#f97316}.empty-cell{text-align:center!important;color:#94a3b8!important;padding:30px!important}.cleanup-side{display:grid;gap:14px}.side-card ul{margin:12px 0 0;padding-left:18px;color:#475569;display:grid;gap:10px}.run-result{display:grid;gap:6px;margin:12px 0}.run-result b{font-size:24px;color:#16a34a}.muted,.run-result em{color:#94a3b8;font-style:normal}button:disabled{opacity:.55;cursor:not-allowed}@media(max-width:1180px){.cleanup-layout{grid-template-columns:1fr}.cleanup-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:760px){.cleanup-hero{flex-direction:column}.cleanup-metrics,.category-grid{grid-template-columns:1fr}}
</style>

<script setup>
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { knowledgeApi } from '@/api'
import { FormModal } from '@/components/common'
import { showToast } from '@/utils/toast'

const props = defineProps({ shopPk: [Number, String], jobs: Array, suggestedGoodsId: String, categoryFilter: String })
const emit = defineEmits(['refresh', 'published'])
const stored = ref([]), page = ref(1), hasMore = ref(false), loading = ref(false), listError = ref('')
const open = ref(false), busy = ref(false), error = ref(''), selected = ref(null), preview = ref(null)
const products = ref([]), productQuery = ref(''), productsMore = ref(false)
const form = reactive({ category: 'product', goods_id: '', title: '', content: '', mode: 'create' })
const jobs = computed(() => (props.jobs || stored.value).map(job => ({ ...job, candidates: props.categoryFilter
  ? job.candidates.filter(c => (c.publication?.category || c.category) === props.categoryFilter) : job.candidates }))
  .filter(job => !props.categoryFilter || job.candidates.length))
const count = computed(() => jobs.value.flatMap(j => j.candidates).filter(c => c.status === 'pending_review').length)
let generation = 0, previewSequence = 0, productSequence = 0, disposed = false
const labels = { pending: '等待提炼', running: '正在提炼', ready: '提炼完成', skipped: '没有可复用知识', cancelled: '已取消' }
const status = c => c.status === 'published' ? '已入库' : c.status === 'rejected' ? '已忽略' : '待确认'
const categoryName = c => (c.publication?.category || c.category) === 'product' ? '商品知识' : c.knowledge_type === 'policy' ? '客服知识 · 商户政策' : '客服知识'
const canConfirm = computed(() => Boolean(preview.value && (!preview.value.target || (preview.value.target.enabled && ['append','replace'].includes(form.mode)))))
function invalidatePreview() { preview.value = null; previewSequence++; error.value = '' }
watch(() => [form.category, form.goods_id, form.title, form.content], invalidatePreview)
watch(() => props.shopPk, () => {
  generation++; previewSequence++; productSequence++; open.value = false; selected.value = null
  stored.value = []; hasMore.value = false; loading.value = false; error.value = ''; page.value = 1
  products.value = []; productQuery.value = ''; busy.value = false
})
onUnmounted(() => { disposed = true; generation++; previewSequence++; productSequence++ })

async function refresh(more = false) {
  if (props.jobs) { emit('refresh'); return }
  if (!props.shopPk || loading.value) return
  const current = generation, pk = props.shopPk, next = more ? page.value + 1 : 1
  loading.value = true; listError.value = ''
  try {
    const result = await knowledgeApi.listCurationTasks({ shop_pk: pk, page: next })
    if (disposed || current !== generation) return
    stored.value = more ? [...stored.value, ...result.list] : result.list
    page.value = next; hasMore.value = result.has_more
  } catch (e) { if (!disposed && current === generation) listError.value = e.message || '候选读取失败，可重试' }
  finally { if (current === generation) loading.value = false }
}
async function loadProducts() {
  const current = generation, seq = ++productSequence
  try {
    const result = await knowledgeApi.listCurationProducts({ shop_pk: props.shopPk, q: productQuery.value.trim() })
    if (disposed || current !== generation || seq !== productSequence) return
    products.value = result.list; productsMore.value = result.has_more
  } catch (e) { if (current === generation && seq === productSequence) error.value = e.message || '商品暂不可读' }
}
async function edit(job, candidate) {
  selected.value = { task: job.id, index: candidate.candidate_index, correction: !!candidate.corrected_reference, token: candidate.candidate_token, original: { title: candidate.title, content: candidate.content, category: candidate.category } }
  Object.assign(form, { category: candidate.category, title: candidate.corrected_reference?.content?.title || candidate.corrected_reference?.content?.goods_name || candidate.title, content: candidate.content,
    goods_id: candidate.corrected_reference?.content?.goods_id || props.suggestedGoodsId || '', mode: 'create' })
  invalidatePreview(); open.value = true
  if (form.category === 'product') await loadProducts()
}
async function prepare() {
  if (!selected.value || !form.title.trim() || !form.content.trim()) { error.value = '请填写标题和正文'; return }
  const current = generation, seq = ++previewSequence
  busy.value = true; error.value = ''
  try {
    const data = await knowledgeApi.previewCuration(selected.value.task, selected.value.index,
      { category: form.category, title: form.title.trim(), goods_id: form.goods_id })
    if (disposed || current !== generation || seq !== previewSequence) return
    if (data.candidate.status !== 'pending_review') { error.value = '这条候选已处理，请关闭并刷新'; return }
    preview.value = data
    form.mode = data.target ? (data.candidate.corrected_reference ? '' : 'append') : 'create'
  } catch (e) { if (current === generation && seq === previewSequence) error.value = e.message || '预览失败，请重试' }
  finally { if (current === generation) busy.value = false }
}
async function confirm(decision = 'publish') {
  if (!preview.value && decision === 'publish') { await prepare(); return }
  if (decision === 'publish' && !canConfirm.value) return
  const current = generation, selection = { ...selected.value }
  const payload = { ...form, ...(decision === 'reject' ? selection.original : { title: form.title.trim(), content: form.content.trim() }), decision,
    candidate_token: selection.token, target_hash: preview.value?.target_hash || '0'.repeat(64), mode: decision === 'reject' ? 'create' : form.mode }
  busy.value = true; error.value = ''
  try {
    const result = await knowledgeApi.reviewCuration(selection.task, selection.index, payload)
    if (disposed || current !== generation) return
    open.value = false
    showToast(result.state === 'published' ? (result.current_matches ? '已入库，后续 AI 可检索使用' : '原入库记录已确认；当前知识有变化，请到知识库核对') : '已忽略，不会用于自动回答', 'success')
    emit('published'); await refresh()
  } catch (e) { if (current === generation) error.value = e.message || '未确认入库，可重试同一操作' }
  finally { if (current === generation) busy.value = false }
}
function close() { if (busy.value) return; open.value = false; previewSequence++ }
</script>

<template>
  <details class="kc-panel" @toggle="e => { if(e.target.open && !props.jobs && !stored.length) refresh() }">
    <summary>人工知识沉淀<span v-if="count"> · {{ count }} 条待确认</span></summary>
    <div class="kc-toolbar"><button type="button" @click="refresh()" :disabled="loading">{{ loading?'读取中…':'刷新候选' }}</button><span>确认入库后，才用于后续回答</span></div>
    <p v-if="listError" role="alert" class="kc-error">{{ listError }}</p>
    <p v-if="!jobs.length&&!loading">{{ hasMore?'当前页暂无此类候选，可以查看更多':'暂无已提炼的知识' }}</p>
    <section v-for="job in jobs" :key="job.id" class="kc-job">
      <p v-if="!job.candidates?.length">{{ labels[job.state] || job.state }}<span v-if="job.error&&job.state==='pending'">，将自动重试</span></p>
      <article v-for="candidate in job.candidates" :key="candidate.candidate_index" class="kc-candidate">
        <small>{{ categoryName(candidate) }} · {{ status(candidate) }}</small>
        <strong>{{ candidate.title }}</strong><p>{{ candidate.content }}</p><small v-if="candidate.scope">适用范围：{{ candidate.scope }}</small><small v-if="candidate.corrected_reference">纠正已引用知识 #{{ candidate.corrected_reference.id }} · 版本 {{ candidate.corrected_reference.version_no || '未留痕' }}；需人工核对后修订</small>
        <button v-if="candidate.status==='pending_review'" type="button" @click="edit(job,candidate)">查看并入库</button>
        <p v-else-if="candidate.publication">{{ ({created:'已新增',merged:'已合并',revised:'已修订',reused:'已复用已有知识'})[candidate.publication.action] }} · 知识 #{{ candidate.publication.target_id }}</p>
        <details v-if="candidate.publication"><summary>本次入库记录</summary><p>入库前：{{ candidate.publication.before?.content || '无已有正文' }}</p><p>入库后：{{ candidate.publication.after.content }}</p><small>后续编辑以正式知识库当前内容为准。</small></details>
      </article>
    </section>
    <button v-if="hasMore" type="button" @click="refresh(true)" :disabled="loading">查看更多候选</button>
  </details>
  <FormModal :model-value="open" title="确认知识入库" :show-footer="false" max-width="680px" @update:model-value="value => { if (!value) close() }">
    <form class="kc-form" @submit.prevent="confirm()">
      <label>存入知识库<select v-model="form.category" :disabled="busy" @change="form.category==='product'&&loadProducts()"><option value="product">商品知识库</option><option value="service">客服知识库（含商户政策）</option></select></label>
      <div v-if="form.category==='product'" class="kc-product-picker">
        <label>查找商品<div class="kc-inline"><input v-model="productQuery" placeholder="商品名称或 ID" :disabled="busy"><button type="button" @click="loadProducts()" :disabled="busy">查找</button></div></label>
        <label>对应商品<select v-model="form.goods_id" :disabled="busy"><option value="">请选择本店商品</option><option v-if="form.goods_id&&!products.some(p=>p.goods_id===form.goods_id)" :value="form.goods_id">{{ form.goods_id }}（请核对）</option><option v-for="p in products" :key="p.goods_id" :value="p.goods_id">{{ p.goods_name || p.goods_id }} · {{ p.goods_id }}</option></select></label><small v-if="productsMore">结果较多，请输入名称缩小范围。</small>
      </div>
      <label>标题<input v-model="form.title" maxlength="100" :disabled="busy"></label>
      <label>知识正文<textarea v-model="form.content" rows="5" maxlength="4000" :disabled="busy"></textarea></label>
      <section v-if="preview" class="kc-preview">
        <template v-if="preview.target"><strong>已有知识</strong><p>{{ preview.target.content || '尚无正文' }}</p>
          <p v-if="!preview.target.enabled" class="kc-error">此知识已停用，请先到知识库核对并启用。</p>
          <label v-else>处理方式<select v-model="form.mode" :disabled="busy"><option v-if="selected?.correction" value="" disabled>请选择如何处理这条纠正</option><option value="append">合并：保留已有内容，补充本条</option><option value="replace">修订：用上面的正文替换已有正文</option></select></label>
        </template><p v-else>将新增一条知识，确认后即可供后续 AI 检索。</p>
      </section>
      <p v-if="error" role="alert" class="kc-error">{{ error }}</p>
      <div class="kc-footer"><button type="button" @click="close()" :disabled="busy">取消</button><button type="button" @click="confirm('reject')" :disabled="busy">忽略此条</button><button v-if="preview" type="button" @click="prepare()" :disabled="busy">重新预览</button><button class="kc-primary" :disabled="busy || (preview&&!canConfirm)">{{ busy?'处理中…':preview?'确认入库':'预览入库' }}</button></div>
    </form>
  </FormModal>
</template>

<style scoped>
.kc-panel{margin:10px 0;padding:12px;border:1px solid var(--border-color,#dce1e8);border-radius:10px;font-size:13px;background:var(--bg-primary,#fff);color:var(--text-primary,#253047)}
.kc-panel summary{cursor:pointer;font-weight:600}.kc-toolbar,.kc-inline,.kc-footer{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.kc-toolbar{margin:12px 0;color:var(--text-secondary,#58657a)}
.kc-candidate{padding:12px 0;border-top:1px solid var(--border-color,#dce1e8)}.kc-candidate strong,.kc-candidate small{display:block}.kc-candidate p,.kc-preview p{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.65;margin:8px 0}.kc-candidate small{color:var(--text-secondary,#58657a);margin-bottom:5px}
button{font:inherit;border:1px solid var(--border-color,#cbd3df);border-radius:7px;background:var(--bg-primary,#fff);color:var(--text-primary,#253047);padding:7px 12px;cursor:pointer}button:disabled{opacity:.5;cursor:default}.kc-form{display:grid;gap:14px;font-size:14px}.kc-form label{display:grid;gap:6px}.kc-form input,.kc-form select,.kc-form textarea{width:100%;box-sizing:border-box;min-width:0;padding:9px 10px;border:1px solid var(--border-color,#cbd3df);border-radius:7px;background:var(--bg-primary,#fff);color:var(--text-primary,#253047);font:inherit}.kc-product-picker{display:grid;gap:10px}.kc-inline{flex-wrap:nowrap}.kc-preview{padding:12px;border-radius:8px;background:var(--bg-secondary,#f2f5fa);max-height:240px;overflow:auto}.kc-footer{justify-content:flex-end;padding-top:5px}.kc-primary{background:#285eea;color:#fff;border-color:#285eea}.kc-error{color:#b42318}.kc-panel p{margin-bottom:8px}
</style>

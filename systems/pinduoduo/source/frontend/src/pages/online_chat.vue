<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { chatApi } from '@/api'
import { formatDateTime } from '@/utils/format'
import { showToast } from '@/utils/toast'
import { createChatWsManager } from '@/utils/chat_ws'
import { draftInvalidatedBy, isPlatformNotice, mediaUrl, videoUrl, mergeConversations, mergeMessages, messageKey, nowBeijingIso, parseContext } from '@/utils/chat_history'
import './styles/chat_workspace.css'
import ChatText from '@/components/chat/ChatText.vue'
import KnowledgeCuration from '@/components/chat/KnowledgeCuration.vue'
import { shouldSendWithEnter } from '@/utils/chat_composer'
import { createWorkspaceCache, shouldAssessConversation } from '@/utils/chat_workspace_cache'
import { chatAttention, refreshChatAttention } from '@/store/chat_attention'

const router = useRouter()
const shops = ref([]), shopQuery = ref(''), shopTotal = ref(0), shopPage = ref(1), shopLoading = ref(false)
const activePk = ref(null), mobileTab = ref('shops'), imagePreview = ref(''), msgContainer = ref(null), aiContainer = ref(null)
const showShops = ref(false), showTools = ref(false), showHistory = ref(false), showDeliveryReview = ref(false)
const cache = reactive({})
const showTraces=ref(false), traceItems=ref([]),traceBefore=ref(null),traceLoading=ref(false),traceError=ref(''),traceScope=ref(null)
let shopSequence = 0, disposed = false, refreshTimer
const { shopCache, threadCache } = createWorkspaceCache(cache)
const activeShop = computed(() => shops.value.find((s) => s.id === activePk.value))
const current = computed(() => activePk.value ? shopCache(activePk.value) : null)
const selected = computed(() => current.value?.conversations.find((c) => c.customer_uid === current.value.selected))
const thread = computed(() => activePk.value && current.value?.selected ? threadCache(activePk.value, current.value.selected) : null)
const canSend = computed(() => Boolean(activeShop.value?.status === 1 && activeShop.value?.connected && thread.value && thread.value.draft.trim() && !thread.value.sending))
const composeText = computed({
  get: () => thread.value ? (thread.value.view === 'ai' ? thread.value.aiInput : thread.value.draft) : '',
  set: value => { if (thread.value) { if (thread.value.view === 'ai') thread.value.aiInput = value; else thread.value.draft = value } },
})
const canSubmit = computed(() => thread.value?.view === 'ai'
  ? Boolean(thread.value.control && thread.value.workspaceLoaded && thread.value.aiInput.trim() && !thread.value.aiBusy && !thread.value.supplementBusy && (thread.value.aiInputKind==='fact' || activeShop.value?.ai_ready))
  : canSend.value)
const kindLabel = kind => ({fact:'你 · 事实补充',instruction:'你',assistant:'AI',draft:'草稿',delivery:'发送结果'}[kind] || kind)
const visibleAiEntries = computed(()=> (thread.value?.entries||[]).filter((entry,index,all)=>
  !['draft','draft_note'].includes(entry.kind) && !(entry.kind==='assistant' && all[index-1]?.kind==='draft' && all[index-1]?.control_revision===entry.control_revision)))
const curationLabel = state => ({pending:'等待提炼',running:'正在提炼',ready:'已生成待审核候选',skipped:'没有可复用知识',cancelled:'范围已变化，已取消'}[state] || state)
let resizeStart = null
function startResize(event) {
  resizeStart = { y: event.clientY, height: thread.value.composeHeight, item: thread.value }
  event.currentTarget.setPointerCapture(event.pointerId)
}
function resizeCompose(event) {
  if (!resizeStart) return
  resizeStart.item.composeHeight = Math.max(52, Math.min(240, window.innerHeight * .35, resizeStart.height + resizeStart.y - event.clientY))
}
function resizeByKey(event) {
  if (!['ArrowUp','ArrowDown','Home','End'].includes(event.key)) return
  event.preventDefault()
  thread.value.composeHeight = event.key==='Home' ? 52 : event.key==='End' ? 240 : Math.max(52, Math.min(240, thread.value.composeHeight + (event.key==='ArrowUp'?16:-16)))
}
function submitComposer() { if (!canSubmit.value) return; return thread.value.view === 'ai' ? submitToAi() : sendMessage() }
function composeKey(event) {
  if (shouldSendWithEnter(event, thread.value?.view)) {
    event.preventDefault(); submitComposer()
  }
}
const phraseForm = reactive({ title: '', content: '', shared: false, phrase_id: null })
const phraseSaving = ref(false), phraseError = ref(''), showPhraseForm = ref(false), connectingPk = ref(null)
const aiModeBusy = ref(null)
const titleOf = (conv) => conv?.nickname || conv?.customer_uid || '客户'
const formatTime = (value) => value ? formatDateTime(value) : '尚未同步'
const conversationTime = (value) => value ? formatDateTime(value).slice(5, 16) : ''
function cleanFilters(filters) { return Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== '' && value != null)) }
function isCurrent(pk, uid) { return activePk.value === pk && current.value?.selected === uid }
async function scrollAiBottom() { await nextTick(); const el = aiContainer.value; if (el) el.scrollTop = el.scrollHeight }
async function scrollBottom() { await nextTick(); const el = msgContainer.value; if (el) el.scrollTop = el.scrollHeight }

async function loadShops(append = false, { silent = false } = {}) {
  if (silent && shopLoading.value) return
  const seq = ++shopSequence
  if (!silent) shopLoading.value = true
  try {
    const data = await chatApi.workspaceShops({ page: append ? shopPage.value + 1 : 1, page_size: 50, q: shopQuery.value })
    if (disposed || seq !== shopSequence) return
    shopTotal.value = data.total
    if (silent && shopPage.value > 1) {
      const byId = new Map(shops.value.map(shop => [shop.id, shop]))
      for (const shop of data.list) byId.set(shop.id, shop)
      shops.value = [...byId.values()]
    } else {
      shopPage.value = data.page
      shops.value = append ? [...shops.value, ...data.list] : data.list
    }
    for (const shop of shops.value) { const state = shopCache(shop.id); if (!state.convSync.checked) state.convSync = shop.sync || {}; wsManager.connect(shop.id) }
    if (activePk.value && !shops.value.some((s) => s.id === activePk.value)) activePk.value = null
    if (!activePk.value && shops.value.length) await selectShop(shops.value[0], false)
  } finally { if (seq === shopSequence) shopLoading.value = false }
}
async function selectShop(shop, goMobile = true) {
  if (activePk.value !== shop.id) { Object.assign(phraseForm, { title: '', content: '', shared: false, phrase_id: null }); showPhraseForm.value = false; phraseError.value = '' }
  showShops.value = false; showTools.value = false
  activePk.value = shop.id; const state = shopCache(shop.id)
  if (goMobile || shops.value.length === 1) mobileTab.value = 'conversations'
  if (!state.conversations.length) await loadConversations(shop.id)
  await loadPhrases(shop.id)
}
async function loadConversations(pk = activePk.value, { append = false, reset = false, silent = false } = {}) {
  if (!pk) return
  const state = shopCache(pk)
  if (silent && state.convLoading) return
  const seq = ++state.convSequence
  if (!silent) { state.convLoading = true; state.convError = '' }
  const page = append ? state.convPage + 1 : 1
  if (reset) state.conversations = []
  try {
    const data = await chatApi.archiveConversations({ shop_pk: pk, page, page_size: 30, ...cleanFilters(state.filters) })
    if (disposed || seq !== state.convSequence) return
    state.conversations = mergeConversations(state.conversations, data.list)
    state.convError = ''
    if (!silent || state.convPage === 1) { state.convPage = page; state.convMore = data.has_more }
    state.convSync = data.sync || {}
  } catch (error) { if (seq === state.convSequence) state.convError = error.message || '本地会话查询失败' }
  finally { if (seq === state.convSequence) state.convLoading = false }
}
async function searchConversations() {
  if (!activePk.value) return
  current.value.selected = ''
  await loadConversations(activePk.value, { reset: true })
}
async function selectConversation(conv) {
  const pk = activePk.value, state = shopCache(pk), uid = conv.customer_uid
  state.selected = uid; mobileTab.value = 'chat'; showHistory.value = false; showDeliveryReview.value = false
  const item = threadCache(pk, uid)
  conv.unread = 0
  chatApi.markConversationRead(pk, uid).catch(() => {})
  await loadControl(pk, uid)
  await loadAiWorkspace(pk, uid)
  if (item.control?.attention_count) { item.view = 'ai'; scrollAiBottom(); if (shouldAssessConversation(item,activeShop.value?.ai_ready)) askAi(pk, uid, item) }
  if (!item.loaded) await loadMessages(pk, uid)
  else { await recoverShop(pk, { silent: true }); await scrollBottom() }
}
async function loadMessages(pk = activePk.value, uid = current.value?.selected, { older = false, search = false } = {}) {
  if (!pk || !uid) return
  const item = threadCache(pk, uid), seq = ++item.sequence
  if (older && !item.more) return
  item.loading = true; item.error = ''
  if (search) { item.messages = []; item.before = null }
  const el = msgContainer.value, previousHeight = el?.scrollHeight || 0, previousTop = el?.scrollTop || 0
  try {
    const data = await chatApi.archiveMessages({ shop_pk: pk, customer_uid: uid, limit: 50,
      ...(older && item.before ? { before: item.before } : {}), ...cleanFilters({ q: item.query, start_time: item.start_time, end_time: item.end_time }) })
    if (disposed || seq !== item.sequence) return
    item.messages = mergeMessages(item.messages, data.messages)
    item.before = data.before_cursor; item.more = data.has_more; item.sync = data.sync || {}; item.loaded = true
    item.latestId = Math.max(item.latestId, data.next_after_id || 0); item.conversationId = data.conversation_id
    applyControl(item, data.ai_control)
    if (isCurrent(pk, uid)) {
      await nextTick()
      if (older && msgContainer.value) msgContainer.value.scrollTop = previousTop + msgContainer.value.scrollHeight - previousHeight
      else if (!item.query) await scrollBottom()
    }
    if (data.conversation_id) {
      const context = await chatApi.getConversationContext(data.conversation_id)
      if (seq === item.sequence) item.context = context
    }
  } catch (error) { if (seq === item.sequence) item.error = error.message || '本地聊天记录查询失败' }
  finally { if (seq === item.sequence) item.loading = false }
}
async function syncPlatform({ more = false, history = false } = {}) {
  const pk = activePk.value, uid = history ? current.value?.selected : ''
  if (!pk || (history && !uid)) return
  const state = shopCache(pk), item = uid ? threadCache(pk, uid) : null
  if (state.syncing) return
  state.syncing = true
  try {
    const previous = item ? item.sync : state.convSync
    const cursor = more && previous?.next_cursor != null ? String(previous.next_cursor) : null
    const data = await chatApi.syncPage({ shop_pk: pk, customer_uid: uid || '', cursor })
    if (disposed) return
    if (item) { item.sync = { ...data, checked: true }; if (!item.query && !item.start_time && !item.end_time) item.messages = mergeMessages(item.messages, data.messages || []); await loadMessages(pk, uid) }
    else { state.convSync = { ...data, checked: true }; await loadConversations(pk) }
    showToast(data.error ? `本页同步未完成：${data.error}` : data.complete ? '本次平台返回范围已同步完成' : '本页已保存，可继续同步更早记录', data.error ? 'warning' : 'success')
    await loadShops()
  } catch (error) {
    const checkpoint = { ...(item ? item.sync : state.convSync), checked: true, error: error.message || '平台同步失败，本地记录仍可查看' }
    if (item) { item.sync = checkpoint; item.error = checkpoint.error } else { state.convSync = checkpoint; state.convError = checkpoint.error }
  } finally { state.syncing = false }
}
async function connectShop(shop) {
  connectingPk.value = shop.id
  try { await chatApi.connectShop(shop.id); showToast('已提交连接请求，实际状态以刷新结果为准', 'success'); await loadShops() }
  finally { connectingPk.value = null }
}
function handleRealtime(pk, uid, raw) {
  const state = shopCache(pk), item = threadCache(pk, uid)
  const currentView = isCurrent(pk, uid) && !item.query && !item.start_time && !item.end_time
  const el = msgContainer.value, nearBottom = !el || el.scrollHeight - el.scrollTop - el.clientHeight < 100
  if (!item.query && !item.start_time && !item.end_time) item.messages = mergeMessages(item.messages, [raw])
  const duplicate = Boolean(raw.msg_id && item.seenMessageIds?.has(raw.msg_id))
  if (!item.seenMessageIds) item.seenMessageIds = new Set()
  if (raw.msg_id) { item.seenMessageIds.add(raw.msg_id); if (item.seenMessageIds.size > 2048) item.seenMessageIds.delete(item.seenMessageIds.values().next().value) }
  const previous = state.conversations.find((c) => c.customer_uid === uid)
  const isOutbound = raw.direction === 'out'
  if (!duplicate && !isPlatformNotice(raw) && item.aiDraftMeta) {
    if (draftInvalidatedBy(item.aiDraftMeta, item.messages)) item.aiDraftMeta = null
    // 分页/搜索可能没有边界消息；服务端核对，不能凭不完整本地历史推断有效。
    loadAiWorkspace(pk, uid)
  }
  if ((!state.filters.q && !state.filters.start_time && !state.filters.end_time || previous)
      && (!raw.archive_sync || !previous?.last_msg_at || String(raw.msg_at || '') >= String(previous.last_msg_at))) {
    state.conversations = mergeConversations(state.conversations, [{ ...previous, customer_uid: uid, nickname: raw.nickname || previous?.nickname || uid,
      last_content: raw.content || '', last_msg_at: raw.msg_at || raw.msg_time || nowBeijingIso(), unread: currentView || isOutbound ? 0 : (previous?.unread || 0) + (duplicate || raw.archive_sync ? 0 : 1) }])
  }
  if (currentView && nearBottom) scrollBottom()
  if (currentView && !isOutbound) chatApi.markConversationRead(pk, uid).catch(() => {})
}
async function recoverShop(pk, { silent = false } = {}) {
  const state = shopCache(pk)
  if (state.recovering) return
  state.recovering = true
  if (!silent) { state.recovery = '正在补齐断线期间的记录…'; state.recoveryError = '' }
  try {
    for (const [uid, item] of Object.entries(state.threads)) {
      if (!item.loaded || item.query || item.start_time || item.end_time) continue
      let cursor = item.latestId, pending = true
      for (let page = 0; page < 20 && pending; page++) {
        const data = await chatApi.archiveMessages({ shop_pk: pk, customer_uid: uid, after_id: cursor, limit: 100 })
        if (disposed) return
        item.messages = mergeMessages(item.messages, data.messages)
        applyControl(item, data.ai_control)
        if (data.has_more && data.next_after_id <= cursor) throw new Error('历史补偿位置没有前进，请刷新重试')
        cursor = data.next_after_id; pending = data.has_more; item.latestId = Math.max(item.latestId, cursor)
      }
      if (pending) throw new Error('仍有未补齐记录，请点击继续补齐')
    }
    await loadConversations(pk, { silent })
    state.recoveryError = ''
  } catch (error) { state.recoveryError = error.message || '历史补偿未完成，可重试' }
  finally { state.recovery = ''; state.recovering = false }
}
const wsManager = createChatWsManager({ onNewMessage: handleRealtime, onControlState: (pk, uid, state) => { applyControl(threadCache(pk, uid), state); if (isCurrent(pk, uid)) loadAiWorkspace(pk, uid) }, onReconnect: recoverShop, onState: (pk, value) => { shopCache(pk).wsState = value } })
async function sendMessage() {
  if (!canSend.value) return
  const pk = activePk.value, uid = current.value.selected, item = threadCache(pk, uid), content = item.draft.trim()
  const clientId = crypto.randomUUID(), message = { client_id: clientId, direction: 'out', msg_type: 'text', content, msg_at: nowBeijingIso(), status: 'sending' }
  item.messages = mergeMessages(item.messages, [message]); item.sending = true; item.draft = ''; await scrollBottom()
  try {
    const { sendOperation } = await import('../utils/send_operation')
    const operation = await sendOperation(pk, uid, content)
    const result = await chatApi.sendMessageByUid(pk, uid, content, operation.key)
    operation.complete()
    item.messages = item.messages.filter((m) => m.client_id !== clientId)
    item.messages = mergeMessages(item.messages, [{ ...message, ...result.message, status: result.delivery_state === 'confirmed' ? 'sent' : 'accepted' }])
    const state = shopCache(pk)
    state.conversations = mergeConversations(state.conversations, [{ ...state.conversations.find((c) => c.customer_uid === uid), customer_uid: uid, last_content: content, last_msg_at: nowBeijingIso(), unread: 0 }])
    if (isCurrent(pk, uid)) await scrollBottom()
  } catch (error) {
    item.messages = item.messages.map((m) => m.client_id === clientId ? { ...m, status: 'failed', error: error.message || '发送失败' } : m)
  } finally { item.sending = false; loadControl(pk, uid) }
}
function applyControl(item, state) {
  if (state && (!item.control || state.revision >= item.control.revision)) {
    if (item.control && state.revision !== item.control.revision) item.aiDraftMeta = null
    item.control = state
    const conversation = shopCache(item.shopPk).conversations.find(c => c.customer_uid === item.uid)
    if (conversation) conversation.attention_count = state.attention_count || 0
  }
}
async function loadControl(pk, uid) {
  const item = threadCache(pk, uid), sequence = ++item.controlSequence
  try {
    const state = await chatApi.getConversationControl(pk, uid)
    if (disposed || sequence !== item.controlSequence) return
    applyControl(item, state); item.controlError = ''
  } catch (error) {
    if (!disposed && sequence === item.controlSequence) item.controlError = '暂时无法读取接管状态，请重试'
  }
}
async function loadDeliveries(pk, uid) {
  const item = threadCache(pk, uid)
  item.deliveryBusy = true
  try { item.deliveries = await chatApi.getDeliveries(pk, uid); item.deliveryError = '' }
  catch (error) { item.deliveryError = error.message || '暂时无法读取发送记录' }
  finally { item.deliveryBusy = false }
}
async function resolveDelivery(row) {
  const pk = activePk.value, uid = current.value.selected, item = thread.value
  item.deliveryBusy = true
  try {
    await chatApi.resolveDelivery(pk, uid, row.id, item.resolutionOutcome, item.resolutionNote.trim())
    if (row.operation_key.startsWith('manual:') && row.kind === 'text') {
      const { sendOperation } = await import('../utils/send_operation')
      const operation = await sendOperation(pk, uid, deliveryBody(row))
      if (`manual:${operation.key}` === row.operation_key) operation.complete()
    }
    item.resolutionNote = ''
    await loadDeliveries(pk, uid)
    await loadControl(pk, uid)
  } catch (error) { item.deliveryError = error.message || '核对结论未保存' }
  finally { item.deliveryBusy = false }
}
function deliveryBody(row) { try { return JSON.parse(row.body) } catch { return row.body } }
function deliveryState(row) { return ({dispatching:'发送进行中', accepted:'发送成功', uncertain:'结果不明', review:'需要人工核对'})[row.state] || row.state }
async function toggleConversationControl() {
  const pk = activePk.value, uid = current.value?.selected, item = thread.value
  if (!item || item.controlBusy || !item.control) return
  item.controlBusy = true
  ++item.controlSequence
  try {
    const mode = item.control.mode === 'human' ? 'auto' : 'human'
    const state = await chatApi.setConversationControl(pk, uid, mode, item.control.revision)
    if (disposed) return
    applyControl(item, state); item.controlError = ''
    await loadAiWorkspace(pk, uid); refreshChatAttention()
    showToast(mode === 'human' ? '已接管，后续自动回复暂停' : '已恢复自动处理，仅处理新消息', 'success')
  } catch (error) {
    item.controlError = error.message || '接管状态未保存，请重试'
    await loadControl(pk, uid)
  } finally { item.controlBusy = false }
}
async function loadPhrases(pk) {
  const state = shopCache(pk); state.phraseLoading = true
  try { state.phrases = (await chatApi.listPhrases(pk)).list || [] }
  finally { state.phraseLoading = false }
}
async function toggleAiMode() {
  const shop = activeShop.value
  if (!shop || aiModeBusy.value !== null) return
  const pk = shop.id, enabled = !shop.ai_enabled
  aiModeBusy.value = pk
  try {
    await chatApi.setAiMode(pk, enabled)
    shop.ai_enabled = enabled
    showToast(enabled ? 'AI 托管已开启，仅处理新的客户消息' : 'AI 托管已关闭', 'success')
    await loadShops()
  } catch (error) { showToast(error.message || 'AI 托管设置失败', 'error') }
  finally { aiModeBusy.value = null }
}

async function olderAi() {
  const pk=activePk.value,uid=current.value?.selected,item=thread.value
  if (!item || item.historyLoading) return
  item.historyLoading=true
  try {
    const page=await chatApi.humanHistory({shop_pk:pk,customer_uid:uid,before:item.historyBefore||undefined})
    const known=new Map([...(page.entries||[]),...(item.coldEntries||[])].map(e=>[e.id,e]))
    item.coldEntries=[...known.values()].sort((a,b)=>(a.at||'').localeCompare(b.at||''))
    item.historyLoaded=true;item.historyBefore=page.before;item.historyAvailable=!!page.before
    item.entries=[...new Map([...item.coldEntries,...item.entries].map(e=>[e.id,e])).values()]
  } catch(e) { item.aiError=e.message }
  finally { item.historyLoading=false }
}
async function loadTraces(more=false) {
  const pk=activePk.value,uid=current.value?.selected
  if (!pk||!uid||traceLoading.value)return
  if(!more){traceItems.value=[];traceBefore.value=null;traceScope.value={pk,uid}}
  showTraces.value=true;traceLoading.value=true;traceError.value=''
  try {
    const result=await chatApi.answerHistory({shop_pk:pk,customer_uid:uid,before:more?traceBefore.value:undefined})
    if (!isCurrent(pk,uid)) return
    traceItems.value=more?[...traceItems.value,...result.items]:result.items;traceBefore.value=result.before
  } catch(e) {traceError.value=e.message}
  finally {traceLoading.value=false}
}
function feedbackAnswer(answer) {
  if(!traceScope.value||!isCurrent(traceScope.value.pk,traceScope.value.uid))return
  thread.value.answerId=answer.id;thread.value.answerLabel=answer.customer_reply||answer.operator_message||'所选回答'
  thread.value.view='ai';showTraces.value=false
}

async function loadAiWorkspace(pk, uid) {
  const item = threadCache(pk, uid), sequence = (item.workspaceSequence || 0) + 1
  item.workspaceSequence = sequence
  try {
    const data = await chatApi.getAiWorkspace(pk, uid)
    if (disposed || sequence !== item.workspaceSequence) return
    if (item.control && data.control.revision < item.control.revision) return
    applyControl(item, data.control)
    if (!item.supplementDirty) { item.supplement = data.supplement; item.supplementRevision = data.control.revision }
    item.entries = [...(item.coldEntries||[]), ...(data.entries || [])]; item.historyAvailable=data.history_available && (!item.historyLoaded || !!item.historyBefore); item.curation = data.curation || []; item.batchClosed = data.batch_closed
    item.requests = data.requests || []; item.assessed = data.assessed; item.business = data.business || {}
    if (!item.aiInput.trim()) item.aiInputKind = item.requests.some(r=>r.status==='open') ? 'fact' : 'instruction'
    item.aiDraftMeta = draftInvalidatedBy(data.draft, item.messages) ? null : data.draft || null
    item.workspaceLoaded = true
  } catch (error) { item.aiError = error.message || '人工补充暂不可读，请重新进入会话后重试' }
}
async function submitToAi() {
  const pk = activePk.value, uid = current.value?.selected, item = thread.value
  if (!item?.control || item.supplementBusy || !item.aiInput?.trim()) return
  const text = item.aiInput, kind = 'instruction', revision = item.control.revision
  item.supplementBusy = true; item.aiError = ''; item.lastAiAction = 'save'
  try {
    const data = await chatApi.saveAiSupplement(pk, uid, text, revision, kind, item.answerId || null)
    applyControl(item, data.control)
    item.entries = [...(item.coldEntries||[]), ...(data.entries || [])]; item.answerId=null; item.answerLabel=''; item.batchClosed = false; item.aiDraftMeta = null
    item.aiCommandId = kind==='instruction' ? item.entries.filter(e=>e.kind==='instruction').at(-1)?.id : null
    item.supplement = data.supplement; item.supplementRevision = data.control.revision
    if (item.aiInput === text) item.aiInput = ''
    item.supplementBusy = false
    if (activeShop.value?.id === pk && activeShop.value.ai_ready) await askAi(pk, uid, item)
  } catch (error) { item.aiError = error.message || '内容未保存，输入已保留' }
  finally { item.supplementBusy = false }
}
async function askAi(pk = activePk.value, uid = current.value?.selected, item = thread.value) {
  if (!item || item.aiBusy || item.supplementBusy) return
  item.aiBusy = true; item.aiError = ''; item.lastAiAction = 'advice'
  try {
    const result=await chatApi.generateAiAdvice(pk, uid, item.aiCommandId || null)
    item.aiCommandId=null
    if(result.execution){await loadMessages(pk,uid);showToast(result.execution.state==='confirmed'?'回复已确认送达':'发送成功，平台已接受','success')}
  }
  catch (error) { item.aiError = error.message || 'AI 暂未回应，已保存的内容可稍后继续' }
  finally { item.aiBusy = false; await loadAiWorkspace(pk, uid); if (isCurrent(pk,uid)&&item.view==='ai') await scrollAiBottom(); refreshChatAttention() }
}
async function generateAiDraft() {
  const pk = activePk.value, uid = current.value?.selected, item = thread.value
  if (!item || item.aiBusy || item.supplementBusy) return
  item.aiBusy = true; item.aiError = ''; item.lastAiAction = 'draft'
  try {
    await chatApi.generateAiDraft(pk, uid)
    if (disposed) return
    // 不以本地插入ID另做一套裁决，读回服务端保存及失效后的权威草稿。
    await loadAiWorkspace(pk, uid)
    if (isCurrent(pk,uid)&&item.view==='ai') await scrollAiBottom()
  } catch (error) { item.aiError = error.message || 'AI 草稿生成失败' }
  finally { item.aiBusy = false; await loadControl(pk, uid) }
}
function retryAiAction() { return thread.value.lastAiAction === 'draft' ? generateAiDraft() : askAi() }
function openAi() {
  const item = thread.value
  item.view = 'ai'; scrollAiBottom()
  if (shouldAssessConversation(item,activeShop.value?.ai_ready)) askAi()
}
function useAiDraft() {
  const item = thread.value, draft = item?.aiDraftMeta
  if (!draft || item.control?.revision !== draft.control_revision) return
  if (item.draft.trim() && !window.confirm('替换当前尚未发送的对客输入内容？')) return
  item.draft = draft.content; item.view = 'customer'; mobileTab.value = 'chat'
  nextTick(() => document.querySelector('[aria-label="回复客户"]')?.focus())
}
function usePhrase(phrase) { if (thread.value) { thread.value.draft = phrase.content; thread.value.view = 'customer'; showTools.value = false; mobileTab.value = 'chat' } }
async function saveQuickPhrase() {
  if (!activePk.value || !phraseForm.title.trim() || !phraseForm.content.trim()) return
  phraseSaving.value = true; phraseError.value = ''
  try { await chatApi.savePhrase({ shop_pk: activePk.value, ...phraseForm }); await loadPhrases(activePk.value); Object.assign(phraseForm, { title: '', content: '', shared: false, phrase_id: null }); showPhraseForm.value = false }
  catch (error) { phraseError.value = error.message || '短语保存失败' }
  finally { phraseSaving.value = false }
}
async function archivePhrase(phrase) {
  await chatApi.savePhrase({ shop_pk: activePk.value, phrase_id: phrase.id, title: phrase.title, content: phrase.content, shared: phrase.shared, enabled: false })
  await loadPhrases(activePk.value)
}
const orderContext = computed(() => thread.value?.context?.latest_order_context || parseContext(thread.value?.messages.findLast((m) => m.order_context)?.order_context))
const goodsContext = computed(() => thread.value?.context?.latest_goods_context || parseContext(thread.value?.messages.findLast((m) => m.goods_context)?.goods_context))
const contextEntries = (object) => Object.entries(object || {}).filter(([, value]) => value != null && typeof value !== 'object')
const contextLabel = (key) => ({ order_no: '订单号', order_id: '订单号', goods_id: '商品标识', goods_name: '商品名称', title: '名称', price: '价格', sku: '规格', spec: '规格', status: '状态', after_sale_status: '售后状态', after_sale_type: '售后类型', quantity: '数量' }[key] || key)
onMounted(async () => { await loadShops(); refreshTimer = setInterval(() => { if (!document.hidden) { loadShops(false, { silent: true }).catch(() => {}); if (activePk.value) recoverShop(activePk.value, { silent: true }) } }, 45000) })
onUnmounted(() => { disposed = true; clearInterval(refreshTimer); wsManager.dispose() })
</script>

<template>
  <div class="cw-page">
    <header class="cw-header"><h1>{{ activeShop?.shop_name || '在线聊天' }}</h1><div class="cw-header-controls"><button class="cw-button cw-desktop-control" :aria-expanded="showShops" aria-controls="cw-shops-panel" @click="showShops=!showShops;showTools=false">{{ showShops?'收起店铺':'切换店铺' }}</button><button class="cw-button cw-desktop-control" :aria-expanded="showTools" aria-controls="cw-tools-panel" @click="showTools=!showTools;showShops=false">{{ showTools?'收起工具':'商品与工具' }}</button></div></header>
    <nav class="cw-mobile-nav" aria-label="工作台导航"><button v-for="tab in [{id:'shops',name:'店铺'},{id:'conversations',name:'会话'},{id:'chat',name:'聊天'},{id:'tools',name:'商品与工具'}]" :key="tab.id" :class="{active:mobileTab===tab.id}" @click="mobileTab=tab.id">{{ tab.name }}<span v-if="tab.id==='conversations'&&chatAttention.shops[activePk]" class="cw-attention-count">{{ chatAttention.shops[activePk] }}</span></button></nav>
    <button v-if="showShops||showTools" class="cw-drawer-backdrop" aria-label="关闭工具面板" @click="showShops=false;showTools=false"></button>
    <div class="cw-layout" :class="{'cw-show-shops':showShops,'cw-show-tools':showTools}" :data-tab="mobileTab">
      <section id="cw-shops-panel" class="cw-panel cw-shops"><div class="cw-panel-head"><h2>店铺 <span>{{ shopTotal }}</span></h2><button class="cw-text-button" :disabled="shopLoading" @click="loadShops()">刷新</button></div>
        <form class="cw-search" @submit.prevent="loadShops()"><input v-model="shopQuery" aria-label="搜索店铺" placeholder="店铺名称、ID、所属成员"><button class="cw-button" type="submit">查找</button></form>
        <div class="cw-scroll"><button v-for="shop in shops" :key="shop.id" class="cw-shop" :class="{active:activePk===shop.id}" @click="selectShop(shop)"><strong>{{ shop.shop_name }}</strong><span>{{ shop.shop_id }} · {{ shop.owner }}</span><div class="cw-badges"><span :class="shop.connected?'cw-good':'cw-muted'">{{ shop.connected?'消息通道已连接':'消息通道未连接' }}</span><span :class="shop.ai_ready?'cw-blue':'cw-muted'">{{ shop.ai_ready?(shop.ai_enabled?'AI 托管中':'AI 辅助模式'):shop.ai_enabled?'AI 待配置':'AI 未配置' }}</span></div><small v-if="shop.status!==1">店铺已停用 · 可查本地历史</small><small v-if="shopCache(shop.id).convSync.error" class="cw-error-text">{{ shopCache(shop.id).convSync.error }}</small><span v-if="shopCache(shop.id).conversations.reduce((n,c)=>n+(c.unread||0),0)" class="cw-unread">{{ shopCache(shop.id).conversations.reduce((n,c)=>n+(c.unread||0),0) }} 未读</span></button>
          <p v-if="!shops.length&&!shopLoading" class="cw-empty">没有匹配的店铺</p><button v-if="shops.length<shopTotal" class="cw-button cw-wide" :disabled="shopLoading" @click="loadShops(true)">加载更多店铺</button>
        </div><footer class="cw-panel-foot">{{ shopLoading?'正在读取店铺…':'店铺掉线不影响已保存的历史查询' }}</footer>
      </section>
      <section class="cw-panel cw-conversations"><div class="cw-panel-head"><h2>会话</h2></div>
        <template v-if="current"><form class="cw-filters" @submit.prevent="searchConversations"><div class="cw-query-row"><input v-model="current.filters.q" aria-label="搜索客户或历史内容" placeholder="搜索客户、历史消息"><button class="cw-button" :disabled="current.convLoading">查询</button></div><details class="cw-advanced"><summary>日期筛选<span v-if="current.filters.start_time||current.filters.end_time"> · 已设置</span></summary><div class="cw-date-row"><input v-model="current.filters.start_time" type="date" aria-label="历史开始日期"><span>至</span><input v-model="current.filters.end_time" type="date" aria-label="历史结束日期"></div><div class="cw-actions"><button class="cw-text-button" :disabled="current.convLoading">应用筛选</button><button type="button" class="cw-text-button" @click="Object.assign(current.filters,{q:'',start_time:'',end_time:''});searchConversations()">重置</button></div></details></form>
          <div class="cw-sync"><div class="cw-actions"><button class="cw-text-button" :disabled="current.syncing||activeShop?.status!==1" @click="syncPlatform()">{{ current.syncing?'同步中…':'同步平台会话' }}</button><button v-if="current.convSync.next_cursor" class="cw-text-button" :disabled="current.syncing" @click="syncPlatform({more:true})">继续同步</button><details class="cw-sync-details"><summary>同步详情</summary><div>上次同步：{{ formatTime(current.convSync.last_success_at) }}<br>{{ current.convSync.complete?'已取完本次平台可返回范围':current.convSync.checked?'平台同步尚未完成':'当前展示本地已保存的记录' }}</div></details></div></div>
          <p v-if="current.convError" class="cw-alert" role="alert">{{ current.convError }}</p><p v-if="current.recovery||current.recoveryError" class="cw-alert">{{ current.recovery||current.recoveryError }}<button v-if="current.recoveryError" class="cw-text-button" @click="recoverShop(activePk)">继续补齐</button></p>
          <div class="cw-scroll"><button v-for="conv in current.conversations" :key="conv.customer_uid" class="cw-conversation" :class="{active:current.selected===conv.customer_uid}" @click="selectConversation(conv)"><span class="cw-conversation-avatar" aria-hidden="true">{{ Array.from(titleOf(conv))[0] }}</span><span class="cw-conversation-body"><span class="cw-conversation-title"><strong :title="titleOf(conv)">{{ titleOf(conv) }}</strong><time :title="formatTime(conv.last_msg_at)">{{ conversationTime(conv.last_msg_at) }}</time></span><span class="cw-conversation-preview"><span>{{ conv.last_content||'暂无消息摘要' }}</span><span v-if="conv.attention_count" class="cw-attention-count" :aria-label="`${conv.attention_count} 项待人工处理`">{{ conv.attention_count }}</span><span v-else-if="conv.unread" class="cw-count" :aria-label="`${conv.unread} 条未读消息`">{{ conv.unread>99?'99+':conv.unread }}</span></span></span></button><div v-if="!current.conversations.length&&!current.convLoading" class="cw-empty"><strong>没有匹配的本地会话</strong><p>本地存档为空不代表平台没有历史。可同步平台最近会话后再查询。</p></div><button v-if="current.convMore" class="cw-button cw-wide" :disabled="current.convLoading" @click="loadConversations(activePk,{append:true})">加载更多会话</button><p v-if="current.convLoading" class="cw-empty">正在查询本地历史…</p></div>
        </template><p v-else class="cw-empty">选择店铺后查询会话历史</p>
      </section>
      <section class="cw-panel cw-chat"><template v-if="thread"><div class="cw-panel-head"><div><h2>{{ titleOf(selected)||current.selected }}</h2><small>{{ activeShop?.shop_name }}</small></div><div class="cw-chat-actions"><span class="cw-connection" :class="{online:current.wsState==='connected'}">{{ current.wsState==='connected'?'在线':'重连中' }}</span><button class="cw-text-button" :aria-expanded="showHistory" @click="showHistory=!showHistory;showDeliveryReview=false">查找</button><button class="cw-text-button" :aria-expanded="showDeliveryReview" @click="showDeliveryReview=!showDeliveryReview;showHistory=false;showDeliveryReview&&loadDeliveries(activePk,current.selected)">发送核对</button></div></div>
        <div class="cw-conversation-control" role="status">
          <span>{{ thread.control?.mode === 'human' ? '人工接管中' : thread.control ? '自动处理' : '接管状态读取中…' }}</span>
          <button type="button" class="cw-button" :disabled="thread.controlBusy || !thread.control || !!thread.controlError" @click="toggleConversationControl">{{ thread.controlBusy ? '保存中…' : thread.control?.mode === 'human' ? '结束人工处理' : '人工接管' }}</button>

        </div>
        <div class="cw-conversation-tabs" role="tablist" aria-label="对话对象"><button id="cw-customer-tab" role="tab" :aria-selected="thread.view==='customer'" aria-controls="cw-customer-messages" @click="thread.view='customer'">顾客对话</button><button id="cw-ai-tab" role="tab" :aria-selected="thread.view==='ai'" aria-controls="cw-ai-messages" @click="openAi">AI 协作<span v-if="thread.control?.attention_count" class="cw-attention-count" :aria-label="`${thread.control.attention_count} 项待人工补充`">{{ thread.control.attention_count }}</span><span v-else-if="thread.aiDraftMeta"> · 有草稿</span></button></div>
        <section v-if="showDeliveryReview&&thread.view==='customer'" class="cw-deliveries">
          <p>先查看平台会话。未查到相同内容不代表没有发出，请核实后填写依据。</p>
          <p v-if="thread.deliveryError" role="alert">{{ thread.deliveryError }}</p>
          <p v-if="!thread.deliveries.length">{{ thread.deliveryBusy?'读取中…':'没有待核对发送' }}</p>
          <article v-for="row in thread.deliveries" :key="row.id"><strong>#{{ row.id }} · {{ deliveryState(row) }}</strong><p>{{ deliveryBody(row) }}</p><small>{{ row.reason }}</small>
            <form @submit.prevent="resolveDelivery(row)"><label>核对结论 <select v-model="thread.resolutionOutcome"><option value="confirmed">平台已发送</option><option value="not_sent">确认未发送</option></select></label><label>核对依据 <input v-model="thread.resolutionNote" minlength="4" maxlength="500" required placeholder="填写平台记录或人工核查依据"></label><button class="cw-button" :disabled="thread.deliveryBusy||thread.resolutionNote.trim().length<4">保存核对结论</button></form>
          </article>
        </section>
        <p v-if="thread.controlError" class="cw-alert" role="alert">{{ thread.controlError }} <button type="button" class="cw-text-button" @click="loadControl(activePk,current.selected)">重试</button></p>
        <form v-if="showHistory&&thread.view==='customer'" class="cw-message-search" @submit.prevent="loadMessages(activePk,current.selected,{search:true})"><input v-model="thread.query" aria-label="搜索本会话消息" placeholder="查找本会话消息"><button class="cw-button">查找</button><button v-if="thread.query" type="button" class="cw-text-button" @click="thread.query='';loadMessages(activePk,current.selected,{search:true})">查看全部</button></form>
        <div v-if="showHistory&&thread.view==='customer'" class="cw-history-toolbar"><span>平台历史</span><button class="cw-text-button" :disabled="current.syncing||activeShop?.status!==1" @click="syncPlatform({history:true,more:!!thread.sync.next_cursor})">{{ current.syncing?'同步中…':thread.sync.next_cursor?'继续同步平台历史':'同步平台历史' }}</button></div>
        <p v-if="thread.error||thread.sync.error" class="cw-alert" role="alert">{{ thread.error||thread.sync.error }} · 已保存的记录仍可查阅</p>
        <div v-show="thread.view==='customer'" id="cw-customer-messages" role="tabpanel" aria-labelledby="cw-customer-tab" ref="msgContainer" class="cw-message-list"><button v-if="thread.more" class="cw-load-earlier" :disabled="thread.loading" @click="loadMessages(activePk,current.selected,{older:true})">{{ thread.loading?'正在加载…':'加载更早的本地消息' }}</button><p v-if="thread.loading&&!thread.messages.length" class="cw-empty">正在读取聊天记录…</p><p v-if="!thread.loading&&!thread.messages.length" class="cw-empty">{{ thread.query?'没有匹配的消息':'暂无本地记录，可同步平台历史' }}</p>
          <article v-for="msg in thread.messages" :key="messageKey(msg)" class="cw-message" :class="{outgoing:msg.direction==='out',failed:msg.status==='failed',notice:msg.platform_notice}"><p v-if="msg.platform_notice" class="cw-platform-notice">{{ msg.content==='[平台扩展消息]'?'平台扩展消息（当前未提供可读内容）':msg.content }}</p><div v-else class="cw-message-meta">{{ msg.direction==='out'?'客服':'客户' }} · {{ formatTime(msg.msg_at) }}</div><div v-if="!msg.platform_notice" class="cw-bubble"><img v-if="mediaUrl(msg)" :src="mediaUrl(msg)" alt="聊天图片，点击放大" loading="lazy" @click="imagePreview=mediaUrl(msg)"><video v-else-if="videoUrl(msg)" :src="videoUrl(msg)" controls preload="none" style="max-width:100%;max-height:360px"></video><div v-else-if="parseContext(msg.goods_context)" class="cw-goods-card"><strong>{{ parseContext(msg.goods_context).goods_name||'商品信息' }}</strong><p>{{ msg.content }}</p></div><p v-else>{{ msg.content||'［非文本消息］' }}</p></div><small v-if="msg.status==='sending'">正在发送…</small><small v-if="msg.status==='accepted'">发送成功</small><div v-if="msg.status==='failed'" class="cw-error-text">{{ msg.error }}<button class="cw-text-button" @click="thread.draft=msg.content">恢复到输入框</button><small>结果不明时请先核对平台记录，避免重复发送。</small></div></article>
        </div>
        <section v-show="thread.view==='ai'" id="cw-ai-messages" ref="aiContainer" role="tabpanel" aria-labelledby="cw-ai-tab" class="cw-ai-history">
          <div class="cw-ai-toolbar"><button class="cw-text-button" @click="loadTraces()">回答依据与反馈</button><button class="cw-text-button" @click="router.push('/harness-operations')">运行状态与经验</button><span>与 AI 沟通 · 明确要求发送时才发给顾客</span><button class="cw-text-button" :disabled="thread.aiBusy||thread.supplementBusy||!thread.workspaceLoaded||!activeShop?.ai_ready||!!thread.aiInput.trim()" @click="generateAiDraft">{{ thread.aiBusy?'正在处理…':'生成草稿' }}</button></div>
          <button v-if="thread.historyAvailable" class="cw-load-earlier" :disabled="thread.historyLoading" @click="olderAi">{{ thread.historyLoading?'读取中…':'加载更早的协作记录' }}</button>
          <p v-if="!thread.workspaceLoaded" class="cw-empty">正在读取协作记录…<button class="cw-text-button" @click="loadAiWorkspace(activePk,current.selected)">重新读取</button></p>
          <p v-else-if="!thread.entries.length&&!thread.aiBusy" class="cw-ai-intro">补充事实、讨论回复，或明确告诉 AI“发给顾客”。</p>
          <details v-if="thread.business?.issues?.length" class="cw-business-progress"><summary>问题进度 · {{ thread.business.issues.filter(i=>!['resolved','answered'].includes(i.status)).length }} 项待跟进</summary><ul><li v-for="issue in thread.business.issues" :key="issue.id"><strong>{{ issue.topic }}</strong> · {{ ({pending:'待判断',ready:'回复就绪',waiting_customer:'等顾客补充',waiting_staff:'等工作人员',answered:'已回复',resolved:'已解决'})[issue.status] || issue.status }}<small v-if="issue.object_ref"> · {{ issue.object_ref }}</small></li></ul></details>
          <section v-if="thread.requests.some(r=>r.status==='open')" class="cw-information-requests" aria-label="需要你补充的信息"><h3>需要你补充</h3><ol><li v-for="request in thread.requests.filter(r=>r.status==='open')" :key="request.id">{{ request.question }}</li></ol><p>在下方补充后，AI 会继续核对是否足够。</p></section>
          <p v-else-if="thread.control?.attention_count&&!thread.aiBusy" class="cw-ai-intro">{{ thread.control.reason || '这条会话需要人工处理' }}。<button class="cw-text-button" :disabled="!activeShop?.ai_ready" @click="askAi()">请 AI 说明需要什么信息</button></p>
          <p v-if="thread.batchClosed" class="cw-ai-divider">上一轮人工处理已结束</p>
          <article v-for="entry in visibleAiEntries" :key="entry.id" class="cw-ai-entry" :class="[entry.kind,{'unconfirmed':entry.kind==='delivery'&&!['accepted','confirmed'].includes(entry.delivery_state)}]"><small>{{ kindLabel(entry.kind) }}{{ entry.archived ? ' · 历史协作' : '' }}</small><ChatText :text="entry.content" /></article>
          <p v-if="thread.aiBusy" class="cw-ai-intro" role="status">AI 正在思考…</p>
          <section v-if="thread.aiDraftMeta" class="cw-ai-preview"><h3>回复草稿 · 未发送</h3><ChatText :text="thread.aiDraftMeta.content" /><details v-if="thread.aiDraftMeta.operator_note"><summary>草稿说明</summary><ChatText :text="thread.aiDraftMeta.operator_note" /></details><button class="cw-button cw-primary" @click="useAiDraft">放入对客输入框</button></section>
          <KnowledgeCuration v-if="thread.curation.length" :key="`${activePk}:${current.selected}`" :shop-pk="activePk" :jobs="thread.curation" :suggested-goods-id="goodsContext?.goods_id" @refresh="loadAiWorkspace(activePk,current.selected)" />
        </section>
        <form class="cw-compose" @submit.prevent>
          <div class="cw-resize-handle" role="separator" aria-label="调整输入框高度" aria-orientation="horizontal" tabindex="0" aria-valuemin="52" aria-valuemax="240" :aria-valuenow="Math.round(thread.composeHeight)" @pointerdown="startResize" @pointermove="resizeCompose" @pointerup="resizeStart=null" @pointercancel="resizeStart=null" @keydown="resizeByKey"><span></span></div>
          <p v-if="thread.view==='ai'&&thread.answerId" class="cw-alert">正在反馈这条回答：{{ thread.answerLabel.slice(0,80) }}<button type="button" class="cw-text-button" @click="thread.answerId=null">取消指定</button></p>
          <div class="cw-compose-tools"><template v-if="thread.view==='ai'"><span>直接补充事实、纠正理解或说明处理要求</span></template><button v-else type="button" class="cw-text-button" @click="showTools=true;showShops=false;mobileTab='tools'">常用语与商品</button></div>
          <p v-if="thread.aiError&&thread.view==='ai'" class="cw-error-text" role="alert">{{ thread.aiError }}<button v-if="!thread.aiBusy&&thread.lastAiAction!=='save'" type="button" class="cw-text-button" @click="retryAiAction">{{ thread.lastAiAction==='draft'?'重试生成草稿':'重试 AI 回复' }}</button></p>
          <textarea v-model="composeText" :aria-label="thread.view==='ai'?'内部协作内容':'回复客户'" :maxlength="thread.view==='ai'?4000:10000" :style="{height:`${thread.composeHeight}px`}" :disabled="thread.view==='ai'?thread.supplementBusy:thread.sending" :placeholder="thread.view==='ai'?('补充事实、纠正理解，或明确告诉 AI 发给顾客…'):'回复顾客…'" @keydown="composeKey"></textarea>
          <div class="cw-send-row"><span class="cw-composer-target">{{ thread.view==='ai'?'明确要求后才发送给顾客':!activeShop?.connected?'通道未连接':'' }}</span><button type="button" class="cw-button cw-primary" :disabled="!canSubmit" @click="submitComposer">{{ thread.view==='ai'?(thread.supplementBusy?'保存中…':'发送给 AI'):(thread.sending?'发送中…':'发送给顾客') }}</button></div>
        </form>
      </template><div v-else class="cw-welcome"><span class="cw-welcome-icon">☏</span><h2>选择会话，开始接待</h2><p>历史记录、商品订单与常用回复<br>集中在同一个工作台</p></div></section>
      <aside id="cw-tools-panel" class="cw-panel cw-tools"><div class="cw-panel-head"><h2>商品与工具</h2><button class="cw-text-button" @click="showTools=false;mobileTab='chat'">关闭</button></div><template v-if="activeShop"><section class="cw-tool-section cw-ai-control"><div class="cw-ai-mode-title"><h3>AI 客服</h3><span :class="activeShop.ai_enabled&&activeShop.ai_ready?'cw-good':'cw-muted'">{{ activeShop.ai_enabled&&activeShop.ai_ready?'托管已开启':'辅助回复' }}</span></div><p class="cw-detail">{{ activeShop.ai_enabled?'新客户消息自动回复；历史记录不补发。':'AI 建议由你确认后发送。' }}</p><button type="button" class="cw-button" :class="{'cw-primary':!activeShop.ai_enabled}" :disabled="aiModeBusy!==null||(!activeShop.ai_enabled&&!activeShop.ai_ready)" @click="toggleAiMode">{{ aiModeBusy===activePk?'正在保存…':activeShop.ai_enabled?'暂停 AI 托管':'开启 AI 托管' }}</button><small>托管按店铺生效；关键词规则仍按原设置执行。生成失败或通道异常时请人工接待。</small></section><section class="cw-tool-section"><h3>店铺运行状态</h3><p>{{ activeShop.shop_name }}</p><div class="cw-badges"><span :class="activeShop.connected?'cw-good':'cw-muted'">{{ activeShop.connected?'IM 通道已连接':'IM 通道未连接' }}</span><span class="cw-chip">{{ activeShop.status===1?'店铺已启用':'店铺已停用' }}</span></div><p class="cw-detail">AI：{{ activeShop.ai_ready?activeShop.model_name:activeShop.ai_enabled?'配置待补全':'未启用' }}</p><p class="cw-detail">今日已记录回复：{{ activeShop.today_replies }}</p><div class="cw-actions"><button v-if="!activeShop.connected&&activeShop.status===1" class="cw-button" :disabled="connectingPk===activePk" @click="connectShop(activeShop)">{{ connectingPk===activePk?'连接中…':'连接消息通道' }}</button><button class="cw-text-button" @click="router.push('/shops')">更新店铺授权</button></div><small>通道连接与店铺授权是不同状态，平台同步结果可用于检查授权。</small></section>
        <section class="cw-tool-section"><h3>店铺商品</h3><div v-for="goods in activeShop.shop_products||[]" :key="goods.goods_id" class="cw-catalog-item"><strong>{{ goods.goods_name||'未命名商品' }}</strong><span v-if="goods.price">同步参考价 ¥{{ goods.price }}</span><small>商品ID：{{ goods.goods_id }}</small></div><p v-if="!activeShop.shop_products?.length" class="cw-detail">暂无已同步的商品目录，请到商品管理同步。</p><small>AI 可读取此目录；库存与当前价格仍需核实。</small></section><section class="cw-tool-section"><h3>商品与订单上下文</h3><template v-for="(object,index) in [goodsContext,orderContext]" :key="index"><dl v-if="object" class="cw-context"><template v-for="[key,value] in contextEntries(object)" :key="key"><dt>{{ contextLabel(key) }}</dt><dd>{{ value }}</dd></template></dl></template><p v-if="!goodsContext&&!orderContext" class="cw-detail">{{ thread?'当前会话尚未记录商品或订单信息':'选择会话后查看已记录的商品与订单' }}</p><small>仅展示已记录的信息，未执行订单查询、退款或发货操作。</small></section>
        <section class="cw-tool-section cw-phrases"><div class="cw-panel-head"><h3>快捷短语</h3><button class="cw-text-button" @click="showPhraseForm=!showPhraseForm">{{ showPhraseForm?'收起':'＋ 新增' }}</button></div><p class="cw-detail">点击填入输入框，确认后发送</p><div v-for="phrase in current.phrases" :key="phrase.id" class="cw-phrase"><button :disabled="!thread" @click="usePhrase(phrase)"><strong>{{ phrase.title }}</strong><span>{{ phrase.content }}</span><small>{{ phrase.shared?'店铺共享':'我的短语' }}</small></button><button v-if="phrase.editable" class="cw-text-button" @click="Object.assign(phraseForm,{...phrase,phrase_id:phrase.id});showPhraseForm=true">编辑</button><button v-if="phrase.editable" class="cw-text-button cw-danger" @click="archivePhrase(phrase)">停用</button></div><p v-if="!current.phrases.length" class="cw-detail">还没有快捷短语，可添加发货、售后等常用回复。</p><form v-if="showPhraseForm" class="cw-phrase-form" @submit.prevent="saveQuickPhrase"><input v-model="phraseForm.title" maxlength="64" aria-label="短语标题" placeholder="短语标题"><textarea v-model="phraseForm.content" maxlength="2000" aria-label="短语内容" placeholder="回复内容"></textarea><label><input v-model="phraseForm.shared" type="checkbox">供本店成员使用</label><p v-if="phraseError" class="cw-error-text">{{ phraseError }}</p><button class="cw-button" :disabled="phraseSaving||!phraseForm.title.trim()||!phraseForm.content.trim()">{{ phraseSaving?'保存中…':'保存短语' }}</button></form></section>
      </template><p v-else class="cw-empty">选择店铺后查看工作区</p></aside>
    </div>
    <div v-if="imagePreview" class="cw-image-preview" role="dialog" aria-label="聊天图片预览" @click.self="imagePreview=''"><button @click="imagePreview=''">关闭预览</button><img :src="imagePreview" alt="聊天图片预览"></div>
  </div>
  <div v-if="showTraces" class="cw-trace-backdrop" @click.self="showTraces=false"><section class="cw-trace-dialog" role="dialog" aria-modal="true" aria-label="回答依据与反馈"><header><h2>回答依据与反馈</h2><button class="cw-button" @click="showTraces=false">关闭</button></header><p>保留当时使用的知识版本。发送状态来自原发送记录；未关联记录不代表已发送。</p><p v-if="traceError" role="alert">{{ traceError }}</p><p v-if="!traceLoading&&!traceItems.length">暂无已留存的回答依据；升级前未保存的早期版本不会补造。</p><article v-for="answer in traceItems" :key="answer.id"><strong>{{ ({accepted:'发送成功',confirmed:'平台已确认',uncertain:'发送待核',review:'待人工核对',dispatching:'发送中',not_sent:'未发送'})[answer.actual_delivery?.state] || '未关联发送记录' }}</strong><small>{{ formatTime(answer.recorded_at) }}</small><ChatText :text="answer.customer_reply||answer.operator_message"/><small>模型 {{ answer.model||'未留存' }} · 规则 {{ answer.policy_version||'未留存' }}</small><ul><li v-for="ref in answer.knowledge?.references||[]" :key="`${ref.source}:${ref.id}`">{{ ({pdd_product_knowledge:'商品知识',pdd_customer_service_knowledge:'客服知识',pdd_product:'同步商品目录'})[ref.source]||'历史知识' }} #{{ ref.id }} · {{ ref.version_no ? `版本 ${ref.version_no}` : '当时未有正式版本' }}<details><summary>当时内容快照</summary><pre>{{ ref }}</pre></details></li></ul><button class="cw-button" @click="feedbackAnswer(answer)">针对这条反馈</button></article><button v-if="traceBefore" class="cw-button" :disabled="traceLoading" @click="loadTraces(true)">更早回答</button><p v-if="traceLoading">读取中…</p></section></div>
</template>

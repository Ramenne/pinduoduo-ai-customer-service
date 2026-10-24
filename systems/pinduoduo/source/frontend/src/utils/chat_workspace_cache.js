// 首次写入后也从响应式容器读回，禁止把赋值表达式的普通对象交给computed。
export function createWorkspaceCache(cache) {
function shopCache(pk) {
  if (!cache[pk]) cache[pk] = { conversations: [], convPage: 1, convMore: false, convLoading: false, convSequence: 0, convSync: {}, convError: '',
    filters: { q: '', start_time: '', end_time: '' }, selected: '', threads: {}, phrases: [], phraseLoading: false, syncing: false, recovering: false, recovery: '', recoveryError: '', wsState: 'connecting' }
  return cache[pk]
}

function threadCache(pk, uid) {
  const state = shopCache(pk)
  if (!state.threads[uid]) state.threads[uid] = { shopPk: pk, uid, messages: [], before: null, more: false, latestId: 0, sync: {}, loaded: false,
    deliveries: [], deliveryError: '', deliveryBusy: false, resolutionNote: '', resolutionOutcome: 'confirmed', control: null, controlBusy: false, controlError: '', controlSequence: 0, loading: false, aiBusy: false, aiError: '', lastAiAction: 'advice', supplement: '', supplementDirty: false, supplementRevision: null, supplementBusy: false, workspaceLoaded: false, workspaceSequence: 0, entries: [], curation: [], batchClosed: false, aiInput: '', aiInputKind: 'instruction', requests: [], business: {issues:[]}, assessed: false, view: 'customer', composeHeight: 52, aiDraftMeta: null, sequence: 0, error: '', query: '', start_time: '', end_time: '', draft: '', sending: false, context: null, conversationId: null }
  return state.threads[uid]
}
  return { shopCache, threadCache }
}

export function shouldAssessConversation(thread, ready) {
  return Boolean(ready && thread?.control?.mode === 'human' && thread.control.attention_count > 0
    && !thread.assessed && !thread.aiBusy && !thread.supplementBusy)
}

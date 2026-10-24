import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { reactive, ref } from 'vue'
import { createWorkspaceCache } from '../src/utils/chat_workspace_cache.js'
import { mergeConversations, mergeMessages } from '../src/utils/chat_history.js'

const source = readFileSync(new URL('../src/pages/online_chat.vue', import.meta.url), 'utf8')
function workspace(api = {}) {
  const cache = createWorkspaceCache(reactive({}))
  const env = { ...cache, shops: ref([{ id: 1 }]), shopLoading: ref(false), shopPage: ref(1),
    shopTotal: ref(1), shopQuery: ref(''), activePk: ref(1), shopSequence: 0, disposed: false,
    chatApi: api, mergeConversations, mergeMessages, cleanFilters: x => x,
    wsManager: { connect() {} }, selectShop() {}, applyControl() {}, console }
  const ctx = vm.createContext(env)
  for (const [start, end] of [['async function loadShops(', 'async function selectShop('],
    ['async function loadConversations(', 'async function searchConversations('],
    ['async function recoverShop(', 'const wsManager =']]) {
    vm.runInContext(source.slice(source.indexOf(start), source.indexOf(end)), ctx)
  }
  return ctx
}

test('background conversation refresh keeps existing content, selection, paging and loading state', async () => {
  let resolve
  const w = workspace({ archiveConversations: () => new Promise(r => { resolve = r }) })
  const s = w.shopCache(1)
  s.conversations = [{ customer_uid: 'a', last_content: 'old' }, { customer_uid: 'b' }]
  s.selected = 'b'; s.convPage = 3; s.convMore = false
  const pending = w.loadConversations(1, { silent: true })
  assert.equal(s.convLoading, false); assert.equal(s.conversations.length, 2)
  resolve({ list: [{ customer_uid: 'a', last_content: 'updated' }], has_more: true })
  await pending
  assert.equal(s.selected, 'b'); assert.equal(s.convPage, 3); assert.equal(s.convMore, false)
  assert.equal(s.conversations.find(c => c.customer_uid === 'a').last_content, 'updated')
})

test('background shop refresh preserves later loaded shops and active shop', async () => {
  const w = workspace({ workspaceShops: async () => ({ page: 1, total: 60, list: [{ id: 1 }] }) })
  w.shops.value = [{ id: 1 }, { id: 55 }]; w.activePk.value = 55; w.shopPage.value = 2
  await w.loadShops(false, { silent: true })
  assert.equal(w.shopLoading.value, false); assert.equal(w.activePk.value, 55)
  assert.equal(w.shops.value.length, 2); assert.equal(w.shopPage.value, 2)
})

test('quiet refresh cannot supersede a foreground query', async () => {
  const w = workspace({ archiveConversations() { throw Error('must not start') } })
  w.shopCache(1).convLoading = true
  await w.loadConversations(1, { silent: true })
  assert.equal(w.shopCache(1).convLoading, true)
})

test('quiet recovery stays silent and single-flight; real failures remain visible', async () => {
  let reject
  const w = workspace({ archiveMessages: () => new Promise((_, r) => { reject = r }) })
  w.threadCache(1, 'a').loaded = true
  const s = w.shopCache(1), pending = w.recoverShop(1, { silent: true })
  assert.equal(s.recovery, ''); assert.equal(s.recovering, true)
  await w.recoverShop(1, { silent: true })
  reject(Error('connection lost')); await pending
  assert.equal(s.recoveryError, 'connection lost'); assert.equal(s.recovering, false)
})

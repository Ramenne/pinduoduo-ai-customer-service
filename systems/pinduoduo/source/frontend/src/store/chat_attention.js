// 红点是未处理事项，不是已读标记。初次/断线恢复读快照，其余由事件唤醒。
import { reactive } from 'vue'
import { get } from '@/utils/request'
import { attentionWsUrl } from '@/utils/chat_ws_url'
export const chatAttention = reactive({ total: 0, shops: {}, connected: false })
let ws, retry, heartbeat, active = false, attempts = 0, fetching = false, dirty = false, generation = 0
export async function refreshChatAttention() {
  if (fetching) { dirty = true; return }
  fetching = true
  const started = generation
  try {
    const data = await get('/chat/workspace/attention')
    if (active && started === generation) { chatAttention.total = data.total; chatAttention.shops = Object.fromEntries(data.shops.map(s => [s.shop_pk, s.count])) }
  } catch { /* 已有红点保留，连接恢复后重新核对，不把失败显示成0。 */ }
  finally { fetching = false; if (dirty) { dirty = false; if (active) refreshChatAttention() } }
}
function connect() {
  if (!active) return
  ws = new WebSocket(attentionWsUrl(import.meta.env.VITE_API_BASE_URL || '/api/v1', location.href, localStorage.getItem('auth_token') || ''))
  ws.onopen = () => { attempts = 0; chatAttention.connected = true; refreshChatAttention(); heartbeat = setInterval(() => { if (ws?.readyState===WebSocket.OPEN) ws.send('{"type":"ping"}') },20000) }
  ws.onmessage = event => { try { if (JSON.parse(event.data).event==='attention_changed') refreshChatAttention() } catch {} }
  ws.onclose = event => { chatAttention.connected = false; clearInterval(heartbeat); if (active && event.code!==1008) retry = setTimeout(connect, Math.min(30000, 1000 * 2 ** Math.min(attempts++,5))) }
  ws.onerror = () => {}
}
export function startChatAttention() {
  if (active) return
  active = true; generation++; attempts = 0; connect()
}
export function stopChatAttention() {
  active = false; generation++; clearTimeout(retry); clearInterval(heartbeat)
  if (ws) { ws.onclose = null; ws.onopen = null; ws.onmessage = null; ws.close(); ws = null }
  chatAttention.total = 0; chatAttention.shops = {}; chatAttention.connected = false
}
if (import.meta.hot) import.meta.hot.dispose(stopChatAttention)

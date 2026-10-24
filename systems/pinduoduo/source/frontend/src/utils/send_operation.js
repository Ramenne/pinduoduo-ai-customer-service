// 同一未确认发送在刷新/网络恢复后沿用操作标识；不自动重放POST。
export async function sendOperation(shopPk, uid, content, storage = localStorage) {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`${shopPk}\n${uid}\n${content}`))
  const fingerprint = Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, '0')).join('')
  const slot = `oin:pending-send:${fingerprint}`
  const key = storage.getItem(slot) || crypto.randomUUID()
  storage.setItem(slot, key) // 本机无法持久保存操作标识时，停止发送。
  return { key, complete: () => { if (storage.getItem(slot) === key) storage.removeItem(slot) } }
}

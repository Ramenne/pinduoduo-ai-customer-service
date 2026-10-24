// HTTP 历史与实时消息共用合并规则；不同消息ID的相同文本仍是两条消息。
export function normalizeMessage(message) {
  const notice = isPlatformNotice(message)
  const content = notice && /^不支持的消息类型[：:]/.test(message.content || '') ? '平台扩展消息（当前未提供可读内容）' : message.content || ''
  return { ...message, platform_notice: notice, msg_at: message.msg_at || message.msg_time || message.created_at,
    direction: message.direction || 'in', content }
}
export function messageTime(message) {
  const raw = message.msg_at || message.msg_time || message.created_at
  if (raw) return Date.parse(/[zZ]|[+-]\d\d:\d\d$/.test(raw) ? raw : `${raw.replace(' ', 'T')}+08:00`) || 0
  return (Number(message.ts) || 0) * (Number(message.ts) < 1e12 ? 1000 : 1)
}
export function messageKey(message) {
  return message.msg_id ? `platform:${message.msg_id}` : message.id ? `db:${message.id}` : `local:${message.client_id}`
}
export function mergeMessages(existing, incoming) {
  const byKey = new Map()
  for (const raw of [...existing, ...incoming]) {
    const item = normalizeMessage(raw), key = messageKey(item)
    const previous = byKey.get(key)
    byKey.set(key, previous ? { ...previous, ...item } : item)
  }
  const result = [...byKey.values()]
  // 只对已确认的本端占位做一对一对账，限定时间窗口，不合并两个真实消息ID。
  const matched = new Set()
  return result.filter((item) => {
    if (!item.client_id || item.status !== 'sent' || item.msg_id) return true
    const real = result.find((candidate) => candidate.msg_id && !matched.has(candidate.msg_id) &&
      candidate.direction === item.direction && candidate.content === item.content &&
      Math.abs(messageTime(candidate) - messageTime(item)) < 5000)
    if (!real) return true
    matched.add(real.msg_id)
    return false
  }).sort((a, b) => messageTime(a) - messageTime(b) || (a.id || 0) - (b.id || 0))
}
export function mergeConversations(existing, incoming) {
  const byUid = new Map(existing.map((item) => [item.customer_uid, item]))
  for (const item of incoming) {
    const prev = byUid.get(item.customer_uid)
    const stale = prev && messageTime({ msg_at: prev.last_msg_at }) > messageTime({ msg_at: item.last_msg_at })
    byUid.set(item.customer_uid, stale ? { ...item, ...prev } : { ...prev, ...item })
  }
  return [...byUid.values()].sort((a, b) => messageTime({ msg_at: b.last_msg_at }) - messageTime({ msg_at: a.last_msg_at }))
}
export function safeMediaUrl(value) {
  if (typeof value !== 'string') return ''
  const text = value.trim()
  return /^(https?:\/\/|\/static\/)/i.test(text) ? text : ''
}
export function mediaUrl(message) {
  if (!/image|^1$/i.test(String(message.msg_type || ''))) return ''
  let content = message.content
  try { content = JSON.parse(content) } catch { /* 直接 URL */ }
  if (content && typeof content === 'object') content = content.url || content.image_url || content.pic_url
  return safeMediaUrl(content)
}
export function parseContext(value) {
  if (!value) return null
  if (typeof value === 'object') return value
  try { const result = JSON.parse(value); return result && typeof result === 'object' ? result : null } catch { return null }
}
export function nowBeijingIso() { return new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 23) }

export function isPlatformNotice(message) {
  return ['system_status', 'auth', 'mall_system_msg'].includes(message.msg_type)
}
// 只在本地确有比服务端边界更新的对话消息时隐藏草稿；缺少边界时由服务端读回裁决。
export function draftInvalidatedBy(draft, messages) {
  if (!draft) return false
  const boundary = messages.find(m => m.id === draft.history_boundary_id)
  if (!boundary) return false
  return messages.some(m => ['in','out'].includes(m.direction) && !isPlatformNotice(m) &&
    (messageTime(m) > messageTime(boundary) ||
      (messageTime(m) === messageTime(boundary) && (m.id || 0) > boundary.id)))
}

export function videoUrl(message) {
  if (!/video|^14$/i.test(String(message.msg_type || ''))) return ''
  let content=message.content
  try { content=JSON.parse(content) } catch { /* direct URL */ }
  if (content && typeof content==='object') content=content.url||content.video_url
  return safeMediaUrl(content)
}

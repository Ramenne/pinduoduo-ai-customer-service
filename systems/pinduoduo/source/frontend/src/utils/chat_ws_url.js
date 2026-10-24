// 与普通HTTP请求使用同一API基址，不再硬编码/api/v1。
export function chatWsUrl(apiBase, pageUrl, shopPk, token) {
  const url = new URL(`${apiBase.replace(/\/$/, '')}/chat/ws/${encodeURIComponent(shopPk)}`, pageUrl)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.searchParams.set('token', token)
  return url.href
}

export function attentionWsUrl(apiBase, pageUrl, token) {
  const url = new URL(`${apiBase.replace(/\/$/, '')}/chat/attention-ws`, pageUrl)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.searchParams.set('token', token)
  return url.href
}

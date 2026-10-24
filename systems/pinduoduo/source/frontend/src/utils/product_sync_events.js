// 只订阅后台任务，不拉取平台商品；切店铺/离开页面后中止，连接失败有限重连。
export function watchProductSync({ url, token, onState, onUnavailable,
  fetcher = fetch, retryDelay = 2000 }) {
  const controller = new AbortController()
  const signal = controller.signal
  const active = state => ['queued', 'running'].includes(state?.state)
  const pause = () => new Promise(resolve => {
    const timer = setTimeout(done, retryDelay)
    function done() { clearTimeout(timer); signal.removeEventListener('abort', done); resolve() }
    signal.addEventListener('abort', done, { once: true })
  })
  const finished = (async () => {
    let state = null
    let failures = 0
    while (!signal.aborted) {
      try {
        const started = Date.now()
        const response = await fetcher(url, { signal, headers: {
          Authorization: `Bearer ${token()}`, Accept: 'text/event-stream',
        } })
        if (!response.ok || !response.headers.get('content-type')?.includes('text/event-stream')) {
          throw new Error('无法订阅同步状态，请重新登录或重连')
        }
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        try {
          while (!signal.aborted) {
            const { value, done } = await reader.read()
            if (done) break
            buffer += decoder.decode(value, { stream: true })
            if (buffer.length > 128000) throw new Error('状态消息异常')
            let boundary
            while ((boundary = buffer.indexOf('\n\n')) >= 0) {
              const frame = buffer.slice(0, boundary)
              buffer = buffer.slice(boundary + 2)
              if (frame.includes('event: unavailable')) throw new Error('同步状态连接已中断')
              const line = frame.split('\n').find(line => line.startsWith('data: '))
              if (!line) continue
              const next = JSON.parse(line.slice(6))
              if (signal.aborted) return
              if (state?.revision !== next.revision) failures = 0
              state = next
              onState(next)
              if (!active(next)) return
            }
          }
        } finally {
          await reader.cancel().catch(() => {})
        }
        if (!active(state)) throw new Error('未收到同步状态')
        if (Date.now() - started < 55000) throw new Error('同步状态连接意外中断')
      } catch (error) {
        if (signal.aborted) return
        failures += 1
        if (failures >= 3) { onUnavailable(error.message); return }
      }
      // 正常60秒流轮换不算失败；异常断开限制重连次数。
      await pause()
    }
  })()
  return { stop: () => controller.abort(), finished }
}

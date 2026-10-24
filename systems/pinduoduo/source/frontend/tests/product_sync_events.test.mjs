import test from 'node:test'
import assert from 'node:assert/strict'
import { watchProductSync } from '../src/utils/product_sync_events.js'

function response(frames) {
  return new Response(new ReadableStream({ start(controller) {
    for (const frame of frames) controller.enqueue(new TextEncoder().encode(frame))
    controller.close()
  } }), { headers: { 'Content-Type': 'text/event-stream' } })
}

test('split frames deliver running and terminal states once', async () => {
  const seen = []
  const watcher = watchProductSync({ url: '/events', token: () => 'test',
    fetcher: async () => response(['data: {"state":"runn', 'ing","revision":1}\n\n',
      'data: {"state":"succeeded","revision":2}\n\n']),
    onState: value => seen.push(value.state), onUnavailable: assert.fail })
  await watcher.finished
  assert.deepEqual(seen, ['running', 'succeeded'])
})

test('repeated same snapshot and broken connection stops after three attempts', async () => {
  let calls = 0, errors = 0
  const watcher = watchProductSync({ url: '/events', token: () => 'test', retryDelay: 0,
    fetcher: async () => { calls++; return response(['data: {"state":"running","revision":1}\n\n']) },
    onState() {}, onUnavailable() { errors++ } })
  await watcher.finished
  assert.equal(calls, 3)
  assert.equal(errors, 1)
})

test('stop ignores delayed old-shop response', async () => {
  let resolve
  const pending = new Promise(done => { resolve = done })
  const seen = []
  const watcher = watchProductSync({ url: '/events', token: () => 'test',
    fetcher: () => pending, onState: value => seen.push(value), onUnavailable: assert.fail })
  watcher.stop()
  resolve(response(['data: {"state":"succeeded","revision":2}\n\n']))
  await watcher.finished
  assert.deepEqual(seen, [])
})

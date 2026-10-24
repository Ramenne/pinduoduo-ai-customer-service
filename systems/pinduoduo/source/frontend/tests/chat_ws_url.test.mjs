import test from 'node:test'
import assert from 'node:assert/strict'
import { chatWsUrl, attentionWsUrl } from '../src/utils/chat_ws_url.js'

test('local API prefix is shared by realtime channel', () => {
  assert.equal(new URL(chatWsUrl('/api','http://127.0.0.1:19102/online-chat',1,'fixture')).pathname,'/api/chat/ws/1')
})
test('production prefix and secure websocket are retained', () => {
  const url=new URL(chatWsUrl('/api/v1/','https://pdd.example/online-chat',2,'fixture+with&characters'))
  assert.equal(url.protocol,'wss:');assert.equal(url.pathname,'/api/v1/chat/ws/2')
  assert.equal(url.searchParams.get('token'),'fixture+with&characters')
})
test('explicit remote backend URL is respected', () => {
  const url=new URL(chatWsUrl('https://api.example/prefix','https://ui.example/online-chat',3,'fixture'))
  assert.equal(url.host,'api.example');assert.equal(url.pathname,'/prefix/chat/ws/3')
})
test('global attention channel uses same secure API prefix without shop data', () => {
  const url=new URL(attentionWsUrl('https://api.example/prefix/','https://ui.example/online-chat','fixture+with&characters'))
  assert.equal(url.protocol,'wss:');assert.equal(url.pathname,'/prefix/chat/attention-ws')
  assert.equal(url.searchParams.get('token'),'fixture+with&characters')
})

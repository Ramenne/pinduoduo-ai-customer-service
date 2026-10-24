import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { shouldSendWithEnter } from '../src/utils/chat_composer.js'

test('AI drafting never submits on Enter, multiline or IME confirmation', () => {
  for (const extra of [{}, {shiftKey:true}, {ctrlKey:true}, {metaKey:true}, {isComposing:true}, {keyCode:229}]) {
    assert.equal(shouldSendWithEnter({key:'Enter', ...extra}, 'ai'), false)
  }
})
test('customer Enter remains available, IME and modified Enter do not send', () => {
  assert.equal(shouldSendWithEnter({key:'Enter'}, 'customer'), true)
  for (const extra of [{shiftKey:true},{isComposing:true},{keyCode:229},{ctrlKey:true},{metaKey:true}]) {
    assert.equal(shouldSendWithEnter({key:'Enter',...extra}, 'customer'), false)
  }
})
test('composer form has no implicit submit channel; send button explicitly invokes action', () => {
  const source = readFileSync(new URL('../src/pages/online_chat.vue', import.meta.url),'utf8')
  assert.match(source, /<form class="cw-compose" @submit\.prevent>/)
  assert.match(source, /<button type="button" class="cw-button cw-primary" :disabled="!canSubmit" @click="submitComposer">/)
  assert.doesNotMatch(source, /@submit\.prevent="submitComposer"/)
})

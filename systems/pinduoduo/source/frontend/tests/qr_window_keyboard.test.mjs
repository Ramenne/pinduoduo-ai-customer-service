// 直接运行组件中的事件处理；接口只记录到内存，不接触真实验证码或平台。
import fs from 'node:fs'
import vm from 'node:vm'
import assert from 'node:assert/strict'
const source = fs.readFileSync(new URL('../src/components/shop_settings/QrVerificationWindow.vue', import.meta.url), 'utf8')
const script = source.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*\n/gm, '')
const sent = []
const context = { ref: value => ({ value }), defineProps: () => ({ sessionId: 'fixture' }),
  onMounted: () => {}, onUnmounted: () => {},
  shopApi: { sendQrInput: async (id, body) => { sent.push({ id, ...body }) } },
  setTimeout, clearTimeout }
vm.createContext(context)
vm.runInContext(script + '\nglobalThis.h = {captureInput, compositionEnd, paste, key, pointer, typeText, inputText, composing, keyboardCapture, flush: () => queue};', context)
const h = context.h
h.captureInput({ target: { value: '1' }, isComposing: false })
await h.flush()
assert.equal(sent.at(-1).text, '1')
h.paste({ preventDefault() {}, clipboardData: { getData: () => '234567' } })
await h.flush()
assert.equal(sent.at(-1).text, '234567')
const count = sent.length
h.composing.value = true
h.captureInput({ target: { value: 'zhong' }, isComposing: true })
assert.equal(sent.length, count)
const target = { value: '中' }
h.compositionEnd({ target, data: '中' })
h.captureInput({ target, isComposing: false })
await h.flush()
assert.equal(sent.length, count + 1)
assert.equal(sent.at(-1).text, '中')
h.key({ key: 'Enter', preventDefault() {} })
await h.flush()
assert.equal(sent.at(-1).key, 'Enter')
h.key({ key: 'a', metaKey: true, preventDefault() {} })
await h.flush()
assert.equal(sent.at(-1).key, 'Control+A')
h.inputText.value = '678901'
await h.typeText()
assert.equal(h.inputText.value, '')
assert.equal(sent.at(-1).text, '678901')
let focused = false
h.keyboardCapture.value = { focus() { focused = true } }
h.pointer({ pointerId: 1, clientX: 50, clientY: 50, currentTarget: {
  setPointerCapture() {}, getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 })
}}, 'down')
await h.flush()
assert.ok(focused)
assert.equal(sent.at(-1).kind, 'down')
assert.equal(sent.at(-1).x, .5)
console.log('Keyboard forwarding passed: typing, paste, IME, Enter, select-all, helper input, focus')

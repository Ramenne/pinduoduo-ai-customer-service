<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { shopApi } from '@/api'
const props = defineProps({ sessionId: { type: String, required: true }, loginMode: { type: String, default: 'qr' } })
const inputText = ref('')
const keyboardCapture = ref(null)
const keyboardActive = ref(false)
const composing = ref(false)
const inputError = ref('')
const sending = ref(false)
const image = ref('')
const nativeSize = ref(false)
const notice = ref('正在连接登录浏览器…')
let timer, disposed = false, held = false, lastMove = 0
let queue = Promise.resolve()
async function refresh() {
  try {
    const data = await shopApi.getQrWindow(props.sessionId)
    if (disposed) return
    image.value = data?.image ? `data:image/jpeg;base64,${data.image}` : ''
    notice.value = image.value ? '' : '登录页面正在加载或切换，请稍候…'
  } catch {
    if (!disposed) notice.value = '登录窗口连接暂时中断，请稍候或取消重试'
  } finally {
    if (!disposed) timer = setTimeout(refresh, 700)
  }
}
function send(payload) {
  const id = props.sessionId
  queue = queue.then(async () => {
    if (disposed) return false
    await shopApi.sendQrInput(id, payload)
    return true
  }).catch(() => {
    if (!disposed) inputError.value = '操作未送达，请等待画面恢复后重试'
    return false
  })
  return queue
}
function forwardText(text) {
  if (!text || text.length > 256 || /[\u0000-\u001f]/u.test(text)) {
    inputError.value = '请输入不超过 256 个字符的单行内容'
    return Promise.resolve(false)
  }
  inputError.value = ''
  return send({ kind: 'text', text })
}
async function typeText() {
  if (!inputText.value || sending.value) return
  const text = inputText.value
  sending.value = true
  try {
    if (await forwardText(text)) inputText.value = ''
  } finally {
    sending.value = false
  }
}
function captureInput(event) {
  if (composing.value || event.isComposing) return
  const text = event.target.value
  event.target.value = ''
  if (text) void forwardText(text)
}
function compositionEnd(event) {
  composing.value = false
  const text = event.data || event.target.value
  event.target.value = ''
  if (text) void forwardText(text)
}
function paste(event) {
  event.preventDefault()
  const text = event.clipboardData?.getData('text/plain') || ''
  if (text) void forwardText(text)
}
function pointer(event, kind) {
  if (kind === 'down') {
    held = true
    event.currentTarget.setPointerCapture(event.pointerId)
    keyboardCapture.value?.focus({ preventScroll: true })
  }
  if (kind === 'move') {
    if (!held || Date.now() - lastMove < 90) return
    lastMove = Date.now()
  }
  if (kind === 'up') held = false
  const r = event.currentTarget.getBoundingClientRect()
  send({ kind, x: Math.max(0, Math.min(1, (event.clientX-r.left)/r.width)), y: Math.max(0, Math.min(1, (event.clientY-r.top)/r.height)) })
}
function key(event) {
  if (event.isComposing || composing.value || event.keyCode === 229) return
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
    event.preventDefault()
    send({kind:'key', key:'Control+A'})
    return
  }
  if (event.ctrlKey || event.metaKey || event.altKey) return
  if (['Tab','Enter',' ','Backspace','Escape','ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Delete','Home','End'].includes(event.key)) {
    event.preventDefault()
    send({kind:'key',key:event.key === ' ' ? 'Space' : event.key})
  }
}
function wheel(event) {
  const r = event.currentTarget.getBoundingClientRect()
  const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 900 : 1
  const clamp = value => Math.max(-2000, Math.min(2000, value))
  send({ kind: 'wheel',
    x: Math.max(0, Math.min(1, (event.clientX-r.left)/r.width)),
    y: Math.max(0, Math.min(1, (event.clientY-r.top)/r.height)),
    delta_x: clamp(event.deltaX * unit), delta_y: clamp(event.deltaY * unit) })
}
onMounted(refresh)
onUnmounted(() => { disposed = true; clearTimeout(timer) })
</script>
<template>
  <div>
    <p v-if="loginMode === 'password'">下方是本次账号登录的操作窗口，可处理提示、验证码和手机确认。先点击画面中的短信验证码框，再直接输入或粘贴验证码；也可以用下方输入栏填入。</p>
    <p v-else>下方是本次登录的真实浏览器画面，<b>仅用于由您本人完成平台要求的安全验证</b>（关闭提示弹窗、拖动滑块等）。本系统不会代替您通过验证码；验证通过后该画面会自动收起，请回到二维码视图继续扫码。</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <button type="button" @click="nativeSize = !nativeSize">{{ nativeSize ? '适应窗口' : '原始尺寸（便于操作，可横向滚动）' }}</button>
    <p v-if="inputError" class="input-error" role="alert">{{ inputError }}</p>
    <div class="window-input">
      <label for="remote-login-text">验证码或待填写内容</label>
      <input id="remote-login-text" v-model="inputText" type="text" autocomplete="off" maxlength="256" placeholder="先点击画面内的输入框，再在此填写" @keydown.enter.prevent="typeText" />
      <button type="button" :disabled="!inputText || sending" @click="typeText">{{ sending ? '正在填入…' : '填入选中的输入框' }}</button>
      <button type="button" @click="send({kind:'key', key:'Enter'})">发送回车</button>
    </div>
    <p class="keyboard-hint">{{ keyboardActive ? '键盘已连接，可直接输入或粘贴；按回车提交。' : '点击画面内的输入框后，即可使用键盘。' }}</p>
    <div class="window-scroll">
      <textarea ref="keyboardCapture" class="keyboard-capture" aria-label="远程登录窗口键盘输入" autocomplete="off" autocapitalize="off" spellcheck="false"
        @focus="keyboardActive = true" @blur="keyboardActive = false"
        @keydown="key" @input="captureInput" @paste="paste"
        @compositionstart="composing = true" @compositionend="compositionEnd"></textarea>
      <img v-if="image" :src="image" :style="{ width: nativeSize ? '1440px' : '100%' }" class="remote-window" alt="人工安全验证画面" tabindex="0" draggable="false"
        @pointerdown.prevent="pointer($event,'down')" @pointermove.prevent="pointer($event,'move')"
        @pointerup.prevent="pointer($event,'up')" @pointercancel="pointer($event,'up')" @keydown="key" @wheel.prevent="wheel" />
    </div>
  </div>
</template>
<style scoped>
.window-scroll { position: relative; overflow: auto; max-height: 65vh; border: 1px solid #ddd; }
.remote-window { display: block; width: 1440px; max-width: none; touch-action: none; user-select: none; }
.window-input { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 12px; border: 1px solid #dbe4f0; border-radius: 8px; background: #f7faff; }
.window-input label { width: 100%; font-size: 13px; font-weight: 600; }
.window-input input { flex: 1; min-width: 240px; min-height: 40px; padding: 8px 12px; border: 1px solid #b9c9df; border-radius: 6px; background: white; color: #18233a; }
.window-input button { min-height: 40px; padding: 8px 12px; border: 1px solid #b9c9df; border-radius: 6px; background: white; }
.keyboard-capture { position: absolute; top: 0; left: 0; width: 1px; height: 1px; opacity: 0; pointer-events: none; padding: 0; border: 0; }
.input-error { color: #d93025; }
.keyboard-hint { color: #526a8c; }
p { font-size: 13px; line-height: 1.6; margin: 8px 0; }
</style>

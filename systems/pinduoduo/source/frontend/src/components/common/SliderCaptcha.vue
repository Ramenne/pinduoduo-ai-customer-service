<!--
  滑块拼图验证码组件（拼多多自动回复系统前端）
  职责（参照 xianyu-auto-reply-wangpan 登录页滑块验证形态，本实现自包含、无第三方依赖）：
    1. 调用后端生成滑块挑战（背景缺口图 + 拼图块），展示并支持鼠标/触摸拖动；
    2. 用户将拼图块拖动对齐缺口后，调用后端校验位移；
    3. 校验通过对外 emit('success', ticket)，并锁定滑块；失败自动重置并刷新图片。
  规范遵循：
    - 提示统一经 showToast（规范 6）；全中文文案（规范 27）；
    - 适配手机端触摸事件（规范 20）；不写死后端地址（经 @/api，规范 21）；
    - 不使用 v-html，图片经 :src 绑定后端返回的 data URL（规范 22）。
-->
<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { authApi } from '@/api'
import { showToast, TOAST_TYPE } from '@/utils/toast'

// 对外事件：success 携带一次性票据，refresh 通知父组件验证态已重置
const emit = defineEmits(['success', 'refresh'])

const props = defineProps({
  // 父组件可禁用滑块（如登录提交中）
  disabled: { type: Boolean, default: false },
})

// 挑战数据（后端返回）
const challenge = reactive({
  challenge_id: '',
  bg_image: '',
  puzzle_image: '',
  y: 0,
  piece_size: 48,
  bg_width: 280,
  bg_height: 155,
})

// 组件状态：loading 加载中 / ready 待拖动 / verifying 校验中 / success 成功
const status = ref('loading')
// 拼图块当前横向位移（像素，相对展示宽度）
const sliderLeft = ref(0)
// 容器实际渲染宽度（用于把展示位移换算回后端图片像素尺度）
const trackWidth = ref(0)
// 滑轨容器引用
const trackRef = ref(null)
// 拖动过程中的临时状态
const dragging = ref(false)
let startX = 0

// 加载/刷新一次滑块挑战
async function loadChallenge() {
  status.value = 'loading'
  sliderLeft.value = 0
  try {
    const data = await authApi.generateSliderCaptcha()
    if (data) {
      challenge.challenge_id = data.challenge_id
      challenge.bg_image = data.bg_image
      challenge.puzzle_image = data.puzzle_image
      challenge.y = data.y
      challenge.piece_size = data.piece_size
      challenge.bg_width = data.bg_width
      challenge.bg_height = data.bg_height
      status.value = 'ready'
      emit('refresh')
    }
  } catch (e) {
    // 生成失败已由 request 统一提示，保持 loading 态并允许点击刷新重试
  }
}

// 开始拖动（鼠标 / 触摸）
function onDragStart(event) {
  if (props.disabled || status.value !== 'ready') return
  dragging.value = true
  startX = getClientX(event)
  // 记录滑轨宽度，用于位移换算
  trackWidth.value = trackRef.value ? trackRef.value.clientWidth : challenge.bg_width
  window.addEventListener('mousemove', onDragMove)
  window.addEventListener('mouseup', onDragEnd)
  window.addEventListener('touchmove', onDragMove, { passive: false })
  window.addEventListener('touchend', onDragEnd)
}

// 拖动中：更新拼图块位移（限制在有效范围内）
function onDragMove(event) {
  if (!dragging.value) return
  if (event.cancelable) event.preventDefault()
  const delta = getClientX(event) - startX
  // 拼图块按展示宽度限制最大位移（展示宽度 - 拼图块展示宽度）
  const displayPiece = (challenge.piece_size / challenge.bg_width) * trackWidth.value
  const max = trackWidth.value - displayPiece
  let next = delta
  if (next < 0) next = 0
  if (next > max) next = max
  sliderLeft.value = next
}

// 移除拖动期间注册到 window 的全局监听器（供结束拖动与组件卸载共用）
function removeDragListeners() {
  window.removeEventListener('mousemove', onDragMove)
  window.removeEventListener('mouseup', onDragEnd)
  window.removeEventListener('touchmove', onDragMove)
  window.removeEventListener('touchend', onDragEnd)
}

// 结束拖动：换算为后端像素尺度并提交校验
async function onDragEnd() {
  if (!dragging.value) return
  dragging.value = false
  removeDragListeners()

  if (status.value !== 'ready') return
  status.value = 'verifying'
  // 把展示位移换算回后端图片的真实像素（按宽度比例还原）
  const ratio = challenge.bg_width / (trackWidth.value || challenge.bg_width)
  const distance = sliderLeft.value * ratio
  try {
    const data = await authApi.verifySliderCaptcha(challenge.challenge_id, distance)
    if (data && data.ticket) {
      status.value = 'success'
      emit('success', data.ticket)
      return
    }
    // 理论不达此分支（失败走 reject）
    resetAfterFail()
  } catch (e) {
    // 校验失败（位移不对齐 / 挑战失效）已由 request 统一提示，重置并刷新
    resetAfterFail()
  }
}

// 校验失败后：重置位移并重新加载一张图
function resetAfterFail() {
  sliderLeft.value = 0
  loadChallenge()
}

// 兼容鼠标与触摸事件，取横坐标
function getClientX(event) {
  if (event.touches && event.touches.length) return event.touches[0].clientX
  if (event.changedTouches && event.changedTouches.length) return event.changedTouches[0].clientX
  return event.clientX
}

// 供父组件主动重置（如登录失败后）
function reset() {
  loadChallenge()
}
defineExpose({ reset })

onMounted(() => {
  loadChallenge()
})

// 卸载兜底：若在拖动进行中被卸载（如登录流程跳转），onDragEnd 未触发，
// 此处移除残留的 window 监听器，避免监听器泄漏与对已销毁组件的回调引用。
onUnmounted(() => {
  removeDragListeners()
})
</script>

<template>
  <div class="slider-captcha">
    <!-- 背景图 + 缺口 + 可拖动拼图块 -->
    <div
      ref="trackRef"
      class="slider-captcha__canvas"
      :style="{ aspectRatio: `${challenge.bg_width} / ${challenge.bg_height}` }"
    >
      <img
        v-if="challenge.bg_image"
        class="slider-captcha__bg"
        :src="challenge.bg_image"
        alt="验证背景"
        draggable="false"
      />
      <img
        v-if="challenge.puzzle_image"
        class="slider-captcha__piece"
        :src="challenge.puzzle_image"
        alt="拼图块"
        draggable="false"
        :style="{
          width: `${(challenge.piece_size / challenge.bg_width) * 100}%`,
          top: `${(challenge.y / challenge.bg_height) * 100}%`,
          left: `${sliderLeft}px`,
        }"
      />
      <!-- 刷新按钮 -->
      <button
        type="button"
        class="slider-captcha__refresh"
        title="刷新验证码"
        :disabled="status === 'verifying'"
        @click="loadChallenge"
      >
        ⟳
      </button>
      <!-- 加载遮罩（规范 23） -->
      <div v-if="status === 'loading'" class="slider-captcha__mask">加载中...</div>
    </div>

    <!-- 拖动滑轨 -->
    <div class="slider-captcha__bar" :class="{ 'is-success': status === 'success' }">
      <span class="slider-captcha__bar-text" v-show="status !== 'success'">
        {{ status === 'verifying' ? '校验中...' : '按住滑块拖动完成拼图' }}
      </span>
      <span class="slider-captcha__bar-text slider-captcha__bar-text--ok" v-show="status === 'success'">
        ✓ 验证通过
      </span>
      <span class="slider-captcha__bar-fill" :style="{ width: `${sliderLeft}px` }"></span>
      <span
        class="slider-captcha__handle"
        :class="{ 'is-success': status === 'success', 'is-disabled': disabled || status === 'verifying' || status === 'success' }"
        :style="{ left: `${sliderLeft}px` }"
        @mousedown="onDragStart"
        @touchstart="onDragStart"
      >
        {{ status === 'success' ? '✓' : '⇆' }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.slider-captcha {
  width: 100%;
  user-select: none;
}

.slider-captcha__canvas {
  position: relative;
  width: 100%;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid var(--color-border);
  background: var(--color-bg);
}

.slider-captcha__bg {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.slider-captcha__piece {
  position: absolute;
  height: auto;
  pointer-events: none;
  filter: drop-shadow(0 0 4px rgba(0, 0, 0, 0.3));
}

.slider-captcha__refresh {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.85);
  color: #333;
  font-size: 15px;
  line-height: 1;
  cursor: pointer;
}

.slider-captcha__refresh:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.slider-captcha__mask {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.6);
  color: var(--color-text-secondary);
  font-size: 13px;
}

.slider-captcha__bar {
  position: relative;
  margin-top: 10px;
  height: 40px;
  border-radius: 8px;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  display: flex;
  align-items: center;
  justify-content: center;
}

.slider-captcha__bar.is-success {
  border-color: #16a34a;
}

.slider-captcha__bar-text {
  font-size: 13px;
  color: var(--color-text-secondary);
  pointer-events: none;
}

.slider-captcha__bar-text--ok {
  color: #16a34a;
}

.slider-captcha__bar-fill {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  background: var(--color-primary-light);
  border-radius: 8px 0 0 8px;
}

.slider-captcha__handle {
  position: absolute;
  top: -1px;
  left: 0;
  width: 40px;
  height: 40px;
  border-radius: 8px;
  background: var(--color-primary);
  color: var(--color-on-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  cursor: grab;
  box-shadow: var(--shadow-card);
  transition: background 0.15s ease;
}

.slider-captcha__handle.is-success {
  background: #16a34a;
}

.slider-captcha__handle.is-disabled {
  cursor: not-allowed;
}

.slider-captcha__handle:active {
  cursor: grabbing;
}
</style>

<!--
  通用消息提示组件（Toast）
  职责：
    1. 统一替代浏览器原生 alert（规范 6），以页面顶部浮层展示 success/error/warning/info 四类提示；
    2. 提示自动消失（由 ui_store 按 duration 定时移除）；
    3. 组件挂载时通过 registerToastHandler 将提示能力注入 utils/toast.js，
       使 request.js 等模块的 showToast 调用走正式组件；卸载时撤销注入。
  说明：文案全中文，背景色与文字保持足够对比（规范 24），不依赖第三方图标库（内联 SVG）。
-->
<script setup>
import { onMounted, onUnmounted } from 'vue'
import { useUIStore } from '@/store/ui_store'
import { registerToastHandler } from '@/utils/toast'

const uiStore = useUIStore()

// 组件挂载后注入正式 Toast 处理器，使全局 showToast 落到本组件
onMounted(() => {
  registerToastHandler((message, type, duration) => {
    uiStore.addToast(message, type, duration)
  })
})

// 组件卸载时撤销注入，回退为占位实现，避免悬空引用
onUnmounted(() => {
  registerToastHandler(null)
})

// 手动关闭某条提示
function handleClose(id) {
  uiStore.removeToast(id)
}
</script>

<template>
  <div class="toast-container">
    <transition-group name="toast">
      <div
        v-for="toast in uiStore.toasts"
        :key="toast.id"
        class="toast-item"
        :class="`toast-item--${toast.type}`"
        role="alert"
      >
        <!-- 类型图标（内联 SVG，避免引入图标库） -->
        <span class="toast-icon" aria-hidden="true">
          <svg v-if="toast.type === 'success'" viewBox="0 0 24 24" width="18" height="18">
            <path fill="currentColor" d="M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z" />
          </svg>
          <svg v-else-if="toast.type === 'error'" viewBox="0 0 24 24" width="18" height="18">
            <path fill="currentColor" d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20m1 15h-2v-2h2zm0-4h-2V7h2z" />
          </svg>
          <svg v-else-if="toast.type === 'warning'" viewBox="0 0 24 24" width="18" height="18">
            <path fill="currentColor" d="M1 21h22L12 2zm12-3h-2v-2h2zm0-4h-2v-4h2z" />
          </svg>
          <svg v-else viewBox="0 0 24 24" width="18" height="18">
            <path fill="currentColor" d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20m1 15h-2v-6h2zm0-8h-2V7h2z" />
          </svg>
        </span>
        <p class="toast-message">{{ toast.message }}</p>
        <!-- 关闭按钮 -->
        <button class="toast-close" type="button" aria-label="关闭" @click="handleClose(toast.id)">
          <svg viewBox="0 0 24 24" width="14" height="14">
            <path
              fill="currentColor"
              d="M18.3 5.71 12 12l6.3 6.29-1.41 1.42L10.59 13.4 4.3 19.71 2.89 18.3 9.17 12 2.89 5.71 4.3 4.29l6.29 6.3 6.3-6.3z"
            />
          </svg>
        </button>
      </div>
    </transition-group>
  </div>
</template>

<style scoped>
.toast-container {
  position: fixed;
  top: 24px;
  left: 50%;
  transform: translateX(-50%);
  /* 提示需高于弹窗（FormModal/ConfirmModal: 100001）与全屏加载遮罩（Loading: 100000），
     否则在弹窗打开时错误提示会被弹窗盖住、层级偏低。置于最高层确保始终可见。 */
  z-index: 100010;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  /* 容器本身不拦截点击，仅提示项可交互 */
  pointer-events: none;
  width: auto;
  max-width: 90vw;
}

.toast-item {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 240px;
  max-width: 90vw;
  padding: 10px 14px;
  border-radius: 8px;
  border: 1px solid transparent;
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.12);
  font-size: 14px;
  line-height: 1.5;
  pointer-events: auto;
}

.toast-message {
  flex: 1;
  word-break: break-word;
}

.toast-icon {
  display: inline-flex;
  flex-shrink: 0;
}

.toast-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: inherit;
  cursor: pointer;
  padding: 2px;
  border-radius: 4px;
  opacity: 0.7;
}

.toast-close:hover {
  opacity: 1;
}

/* 各类型配色：保证文字与背景对比清晰（规范 24） */
.toast-item--success {
  background: #f0f9eb;
  border-color: #b7eb8f;
  color: #237804;
}

.toast-item--error {
  background: #fff1f0;
  border-color: #ffa39e;
  color: #a8071a;
}

.toast-item--warning {
  background: #fffbe6;
  border-color: #ffe58f;
  color: #ad6800;
}

.toast-item--info {
  background: #e6f4ff;
  border-color: #91caff;
  color: #0958d9;
}

/* 进入/离开过渡动画 */
.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(-12px);
}
</style>

<!--
  通用加载组件（Loading）
  职责：界面加载数据时同时展示遮罩与转圈效果（规范 23 / 需求 23.7），
        不得仅展示遮罩而省略转圈。
  用法：
    - 全屏遮罩：<Loading :visible="loading" full-screen text="加载中..." />
    - 区域内联：<Loading :visible="loading" />（在相对定位容器内覆盖该区域）
  说明：转圈使用纯 CSS 动画实现，不依赖第三方库；文案中文。
-->
<script setup>
defineProps({
  // 是否展示加载
  visible: {
    type: Boolean,
    default: true,
  },
  // 是否全屏遮罩（false 时覆盖最近的相对定位父容器）
  fullScreen: {
    type: Boolean,
    default: false,
  },
  // 加载提示文案
  text: {
    type: String,
    default: '',
  },
  // 转圈尺寸：sm/md/lg
  size: {
    type: String,
    default: 'md',
  },
})
</script>

<template>
  <transition name="loading-fade">
    <!-- 遮罩层：遮罩与转圈始终同时出现（规范 23） -->
    <div v-if="visible" class="loading-mask" :class="{ 'loading-mask--full': fullScreen }">
      <div class="loading-content">
        <!-- 转圈：纯 CSS 旋转动画 -->
        <span class="loading-spinner" :class="`loading-spinner--${size}`" aria-hidden="true"></span>
        <p v-if="text" class="loading-text">{{ text }}</p>
      </div>
    </div>
  </transition>
</template>

<style scoped>
.loading-mask {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  /* 半透明遮罩 + 轻微模糊，保证下层内容被遮挡 */
  background: rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(2px);
  z-index: 1000;
}

/* 暗黑模式下的遮罩底色 */
:global(.dark) .loading-mask {
  background: rgba(31, 31, 31, 0.7);
}

.loading-mask--full {
  position: fixed;
  z-index: 100000;
}

.loading-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

/* 转圈：圆环 + 旋转动画（确保不只遮罩、必有转圈） */
.loading-spinner {
  display: inline-block;
  border-radius: 50%;
  border: 3px solid rgba(22, 119, 255, 0.2);
  border-top-color: var(--color-primary, #1677ff);
  animation: loading-rotate 0.8s linear infinite;
}

.loading-spinner--sm {
  width: 18px;
  height: 18px;
  border-width: 2px;
}

.loading-spinner--md {
  width: 32px;
  height: 32px;
}

.loading-spinner--lg {
  width: 48px;
  height: 48px;
  border-width: 4px;
}

.loading-text {
  font-size: 14px;
  color: var(--color-text, #1f2329);
}

@keyframes loading-rotate {
  to {
    transform: rotate(360deg);
  }
}

.loading-fade-enter-active,
.loading-fade-leave-active {
  transition: opacity 0.2s ease;
}

.loading-fade-enter-from,
.loading-fade-leave-to {
  opacity: 0;
}
</style>

<!--
  通用表单弹窗组件（FormModal）
  职责：为新增 / 编辑等表单场景提供统一弹窗容器。
  关键规范（规范 7 / 需求 23.12）：
    - 仅允许通过「关闭 / 取消 / 确定」按钮关闭弹窗；
    - 禁止点击遮罩或空白处关闭弹窗（遮罩点击不触发任何关闭逻辑）。
  用法（v-model 双向绑定显隐，表单内容经默认插槽传入）：
    <FormModal v-model="open" title="新增公告" :loading="saving" @confirm="onSubmit">
      <表单字段 />
    </FormModal>
-->
<script setup>
defineProps({
  // 是否显示（配合 v-model:modelValue）
  modelValue: {
    type: Boolean,
    default: false,
  },
  // 弹窗标题
  title: {
    type: String,
    default: '',
  },
  // 确定按钮文案
  confirmText: {
    type: String,
    default: '保存',
  },
  // 取消按钮文案
  cancelText: {
    type: String,
    default: '取消',
  },
  // 是否处于提交中（禁用按钮并展示转圈）
  loading: {
    type: Boolean,
    default: false,
  },
  // 是否展示底部操作栏（纯展示场景可隐藏）
  showFooter: {
    type: Boolean,
    default: true,
  },
  // 弹窗最大宽度
  maxWidth: {
    type: String,
    default: '560px',
  },
})

const emit = defineEmits(['update:modelValue', 'confirm', 'cancel'])

// 关闭弹窗（仅由按钮触发，遮罩点击不调用）
function close() {
  emit('update:modelValue', false)
  emit('cancel')
}

// 点击确定
function confirm() {
  emit('confirm')
}

// 遮罩点击：按规范 7 显式不做任何处理（保留空函数以表明刻意为之）
function onOverlayClick() {
  // 禁止点击遮罩关闭，故此处不执行任何操作
}
</script>

<template>
  <!--
    Teleport 到 body：避免弹窗被父级（如 ShopSettingsModal）创建的 z-index 堆叠上下文困住，
    从而保证多层弹窗（设置弹窗内再开新增/编辑/删除确认）层级正确、可正常交互。
  -->
  <Teleport to="body">
    <transition name="form-modal-fade">
      <div v-if="modelValue" class="form-modal-root">
      <!-- 遮罩层：点击不关闭（规范 7） -->
      <div class="form-modal-overlay" @click="onOverlayClick"></div>

      <!-- 弹窗主体 -->
      <div class="form-modal-dialog" role="dialog" aria-modal="true" :style="{ maxWidth }">
        <!-- 标题栏 + 右上角关闭按钮 -->
        <div class="form-modal-header">
          <h3 class="form-modal-title">{{ title }}</h3>
          <button class="form-modal-x" type="button" aria-label="关闭" @click="close">
            <svg viewBox="0 0 24 24" width="16" height="16">
              <path
                fill="currentColor"
                d="M18.3 5.71 12 12l6.3 6.29-1.41 1.42L10.59 13.4 4.3 19.71 2.89 18.3 9.17 12 2.89 5.71 4.3 4.29l6.29 6.3 6.3-6.3z"
              />
            </svg>
          </button>
        </div>

        <!-- 表单内容 -->
        <div class="form-modal-body">
          <slot />
        </div>

        <!-- 底部操作栏 -->
        <div v-if="showFooter" class="form-modal-footer">
          <button
            class="form-modal-btn form-modal-btn--cancel"
            type="button"
            :disabled="loading"
            @click="close"
          >
            {{ cancelText }}
          </button>
          <button
            class="form-modal-btn form-modal-btn--primary"
            type="button"
            :disabled="loading"
            @click="confirm"
          >
            <span v-if="loading" class="form-modal-spinner" aria-hidden="true"></span>
            {{ confirmText }}
          </button>
        </div>
      </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.form-modal-root {
  position: fixed;
  inset: 0;
  z-index: 100001;
  display: flex;
  align-items: center;
  justify-content: center;
}
.form-modal-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  cursor: default;
}
.form-modal-dialog {
  position: relative;
  width: 100%;
  margin: 0 16px;
  max-height: 86vh;
  display: flex;
  flex-direction: column;
  background: var(--color-bg-elevated, #fff);
  color: var(--color-text, #1f2329);
  border: 1px solid var(--color-border, #e5e6eb);
  border-radius: 12px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.2);
}
.form-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--color-border, #e5e6eb);
  flex-shrink: 0;
}
.form-modal-title {
  font-size: 16px;
  font-weight: 600;
}
.form-modal-x {
  display: inline-flex;
  border: none;
  background: transparent;
  color: #8c8c8c;
  cursor: pointer;
  padding: 4px;
  border-radius: 6px;
}
.form-modal-x:hover {
  background: var(--color-hover-bg, rgba(0, 0, 0, 0.06));
  color: var(--color-text, #1f2329);
}
.form-modal-body {
  padding: 20px;
  overflow-y: auto;
}
.form-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 12px 20px 20px;
  flex-shrink: 0;
}
.form-modal-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 9px 20px;
  border: none;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}
.form-modal-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.form-modal-btn--cancel {
  background: var(--color-hover-bg, #f2f3f5);
  color: var(--color-text, #1f2329);
}
.form-modal-btn--primary {
  background: var(--color-primary, #1677ff);
  color: var(--color-on-primary, #fff);
}
.form-modal-btn--primary:hover:not(:disabled) {
  background: var(--color-primary-hover, #4096ff);
}
.form-modal-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.4);
  border-top-color: #fff;
  border-radius: 50%;
  animation: form-modal-rotate 0.8s linear infinite;
}
@keyframes form-modal-rotate {
  to {
    transform: rotate(360deg);
  }
}
.form-modal-fade-enter-active,
.form-modal-fade-leave-active {
  transition: opacity 0.2s ease;
}
.form-modal-fade-enter-from,
.form-modal-fade-leave-to {
  opacity: 0;
}
</style>

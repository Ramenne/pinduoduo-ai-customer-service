<!--
  通用下拉选择组件（Select）
  职责：提供统一样式的单选下拉，替代原生 select，支持键盘与点击外部关闭。
  用法（v-model 绑定选中值）：
    <Select v-model="value" :options="[{ value:'a', label:'选项A' }]" placeholder="请选择" />
  说明：点击组件外部仅收起下拉面板（非弹窗，不受规范 7 弹窗约束）；文案中文，内联 SVG。
-->
<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'

const props = defineProps({
  // 当前选中值（v-model）
  modelValue: {
    type: [String, Number],
    default: '',
  },
  // 选项列表：{ value, label, disabled? }
  options: {
    type: Array,
    default: () => [],
  },
  // 占位文案
  placeholder: {
    type: String,
    default: '请选择',
  },
  // 是否禁用
  disabled: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['update:modelValue', 'change'])

const isOpen = ref(false)
const rootRef = ref(null)

// 当前选中项
const selectedOption = computed(() =>
  props.options.find((opt) => opt.value === props.modelValue)
)

// 切换面板展开
function toggleOpen() {
  if (props.disabled) {
    return
  }
  isOpen.value = !isOpen.value
}

// 选择某项
function selectOption(option) {
  if (option.disabled) {
    return
  }
  emit('update:modelValue', option.value)
  emit('change', option.value)
  isOpen.value = false
}

// 点击组件外部收起面板
function handleClickOutside(event) {
  if (rootRef.value && !rootRef.value.contains(event.target)) {
    isOpen.value = false
  }
}

onMounted(() => {
  document.addEventListener('mousedown', handleClickOutside)
})

onUnmounted(() => {
  document.removeEventListener('mousedown', handleClickOutside)
})
</script>

<template>
  <div ref="rootRef" class="select-root">
    <button
      type="button"
      class="select-trigger"
      :class="{ 'select-trigger--open': isOpen, 'select-trigger--disabled': disabled }"
      :disabled="disabled"
      @click="toggleOpen"
    >
      <span class="select-value" :class="{ 'select-value--placeholder': !selectedOption }">
        {{ selectedOption ? selectedOption.label : placeholder }}
      </span>
      <svg class="select-arrow" :class="{ 'select-arrow--open': isOpen }" viewBox="0 0 24 24" width="16" height="16">
        <path fill="currentColor" d="M7 10l5 5 5-5z" />
      </svg>
    </button>

    <div v-if="isOpen" class="select-panel">
      <div v-if="options.length === 0" class="select-empty">暂无选项</div>
      <button
        v-for="option in options"
        :key="option.value"
        type="button"
        class="select-option"
        :class="{
          'select-option--selected': option.value === modelValue,
          'select-option--disabled': option.disabled,
        }"
        :disabled="option.disabled"
        @click="selectOption(option)"
      >
        <span class="select-option-label">{{ option.label }}</span>
        <svg v-if="option.value === modelValue" viewBox="0 0 24 24" width="16" height="16">
          <path fill="currentColor" d="M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z" />
        </svg>
      </button>
    </div>
  </div>
</template>

<style scoped>
.select-root {
  position: relative;
  width: 100%;
}

.select-trigger {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  font-size: 14px;
  text-align: left;
  background: var(--color-bg, #ffffff);
  color: var(--color-text, #1f2329);
  border: 1px solid var(--color-border, #e5e6eb);
  border-radius: 6px;
  cursor: pointer;
}

.select-trigger--open {
  border-color: var(--color-primary, #1677ff);
}

.select-trigger--disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.select-value {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.select-value--placeholder {
  color: #8c8c8c;
}

.select-arrow {
  flex-shrink: 0;
  color: #8c8c8c;
  transition: transform 0.2s ease;
}

.select-arrow--open {
  transform: rotate(180deg);
}

.select-panel {
  position: absolute;
  z-index: 50;
  width: 100%;
  margin-top: 4px;
  max-height: 240px;
  overflow: auto;
  background: var(--color-bg, #ffffff);
  border: 1px solid var(--color-border, #e5e6eb);
  border-radius: 6px;
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.12);
}

.select-empty {
  padding: 8px 12px;
  font-size: 14px;
  color: #8c8c8c;
  text-align: center;
}

.select-option {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  font-size: 14px;
  text-align: left;
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--color-text, #1f2329);
}

.select-option:hover:not(.select-option--disabled) {
  background: rgba(22, 119, 255, 0.08);
}

.select-option--selected {
  color: var(--color-primary, #1677ff);
}

.select-option--disabled {
  color: #bfbfbf;
  cursor: not-allowed;
}

.select-option-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>

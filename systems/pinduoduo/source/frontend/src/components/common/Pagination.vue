<!--
  通用分页组件（Pagination）
  职责：为后端分页表格提供统一的分页控件（规范 28 / 需求 3.3）：
    - 默认每页 20 条，每页大小可选 10/20/50/100；
    - 文案全中文；切换页码 / 每页条数时向上抛出事件，由页面触发后端分页查询；
    - 采用后端分页，本组件不持有数据，仅展示与派发分页参数。
  用法（v-model 双向绑定 page 与 page-size）：
    <Pagination
      :page="page" :page-size="pageSize" :total="total"
      @update:page="onPageChange" @update:page-size="onPageSizeChange" />
-->
<script setup>
import { computed } from 'vue'
import Select from './Select.vue'

const props = defineProps({
  // 当前页码（从 1 开始）
  page: {
    type: Number,
    default: 1,
  },
  // 每页条数（10/20/50/100）
  pageSize: {
    type: Number,
    default: 20,
  },
  // 总记录数
  total: {
    type: Number,
    default: 0,
  },
})

const emit = defineEmits(['update:page', 'update:page-size'])

// 每页条数可选项（规范 28）
const pageSizeOptions = [
  { value: 10, label: '10 条/页' },
  { value: 20, label: '20 条/页' },
  { value: 50, label: '50 条/页' },
  { value: 100, label: '100 条/页' },
]

// 总页数（至少 1 页）
const totalPages = computed(() => {
  if (props.total <= 0) {
    return 1
  }
  return Math.ceil(props.total / props.pageSize)
})

// 是否禁用上一页 / 下一页
const isFirstPage = computed(() => props.page <= 1)
const isLastPage = computed(() => props.page >= totalPages.value)

// 跳转到上一页
function goPrev() {
  if (!isFirstPage.value) {
    emit('update:page', props.page - 1)
  }
}

// 跳转到下一页
function goNext() {
  if (!isLastPage.value) {
    emit('update:page', props.page + 1)
  }
}

// 切换每页条数（切换后回到第 1 页）
function onPageSizeChange(value) {
  const size = Number(value)
  emit('update:page-size', size)
  emit('update:page', 1)
}
</script>

<template>
  <div class="pagination">
    <span class="pagination__total">共 {{ total }} 条</span>
    <div class="pagination__controls">
      <button
        type="button"
        class="pagination__btn"
        :disabled="isFirstPage"
        @click="goPrev"
      >
        上一页
      </button>
      <span class="pagination__page">第 {{ page }} / {{ totalPages }} 页</span>
      <button
        type="button"
        class="pagination__btn"
        :disabled="isLastPage"
        @click="goNext"
      >
        下一页
      </button>
    </div>
    <div class="pagination__size">
      <Select
        :model-value="pageSize"
        :options="pageSizeOptions"
        @change="onPageSizeChange"
      />
    </div>
  </div>
</template>

<style scoped>
.pagination {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 16px;
  padding: 12px 4px;
  flex-wrap: wrap;
}
.pagination__total {
  font-size: 13px;
  color: var(--color-text-secondary, #5c6573);
}
.pagination__controls {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pagination__btn {
  padding: 6px 14px;
  border: 1px solid var(--color-border, #e5e6eb);
  border-radius: 6px;
  background: var(--color-bg, #fff);
  color: var(--color-text, #1f2329);
  font-size: 13px;
  cursor: pointer;
}
.pagination__btn:hover:not(:disabled) {
  border-color: var(--color-primary, #1677ff);
  color: var(--color-primary, #1677ff);
}
.pagination__btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.pagination__page {
  font-size: 13px;
  color: var(--color-text, #1f2329);
}
.pagination__size {
  width: 120px;
}
</style>

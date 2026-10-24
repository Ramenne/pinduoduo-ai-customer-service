<!--
  系统日志页面（需求 21.4）
  职责：后端分页查询系统日志，支持按级别、模块与北京时间范围筛选。系统日志为全局日志，仅授权用户可见。
  规范：后端分页（规范 28）、按时间范围筛选、表格固定高度内部滚动（规范 29）、全中文（规范 27）、
        加载遮罩 + 转圈（规范 23）、XSS 安全渲染（规范 22）、响应式（规范 20）、
        错误提示统一 showToast（规范 2/4）、不写死 localhost（规范 21）；仅查询不删除（需求 19.5）。
-->
<script setup>
import { onMounted, ref } from 'vue'
import { Loading, TableContainer, Pagination, Select, SafeHtml } from '@/components/common'
import { fetchSystemLogs } from '@/api/logs_api'
import { formatDateTime } from '@/utils/format'

// 加载态
const loading = ref(false)

// 筛选条件：级别、模块、起止时间
const filters = ref({
  level: '',
  module: '',
  start_time: '',
  end_time: '',
})

// 日志级别下拉选项（与后端 ALLOWED_LOG_LEVELS 一致，禁止 debug，规范 38）
const levelOptions = [
  { value: '', label: '全部级别' },
  { value: 'info', label: '信息' },
  { value: 'warning', label: '警告' },
  { value: 'error', label: '错误' },
]

// 级别枚举键到中文文案
const LEVEL_LABELS = { info: '信息', warning: '警告', error: '错误' }

// 分页与列表
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const list = ref([])

// 级别中文展示
function levelLabel(key) {
  if (!key) {
    return '—'
  }
  return LEVEL_LABELS[key] || key
}

// 拉取系统日志
async function loadLogs() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (filters.value.level !== '') {
      params.level = filters.value.level
    }
    if (filters.value.module && filters.value.module.trim()) {
      params.module = filters.value.module.trim()
    }
    if (filters.value.start_time) {
      params.start_time = filters.value.start_time.replace('T', ' ')
    }
    if (filters.value.end_time) {
      params.end_time = filters.value.end_time.replace('T', ' ')
    }
    const data = await fetchSystemLogs(params)
    list.value = (data && data.list) || []
    total.value = (data && data.total) || 0
  } finally {
    loading.value = false
  }
}

function onSearch() {
  page.value = 1
  loadLogs()
}

function onReset() {
  filters.value = { level: '', module: '', start_time: '', end_time: '' }
  page.value = 1
  loadLogs()
}

function onPageChange(next) {
  page.value = next
  loadLogs()
}
function onPageSizeChange(size) {
  pageSize.value = size
}

onMounted(loadLogs)

// 级别对应的标签样式类（便于视觉区分）
function levelClass(key) {
  return `level-tag level-tag--${key || 'info'}`
}
</script>

<template>
  <div class="log-page">
    <h2 class="log-page__title">系统日志</h2>

    <!-- 筛选区 -->
    <div class="log-page__filters">
      <div class="filter-item filter-item--select">
        <span class="filter-item__label">级别</span>
        <Select v-model="filters.level" :options="levelOptions" placeholder="全部级别" />
      </div>
      <label class="filter-item">
        <span class="filter-item__label">模块</span>
        <input v-model="filters.module" type="text" class="filter-item__input" placeholder="按模块筛选" />
      </label>
      <label class="filter-item">
        <span class="filter-item__label">起始时间</span>
        <input v-model="filters.start_time" type="datetime-local" class="filter-item__input" />
      </label>
      <label class="filter-item">
        <span class="filter-item__label">结束时间</span>
        <input v-model="filters.end_time" type="datetime-local" class="filter-item__input" />
      </label>
      <div class="filter-actions">
        <button type="button" class="btn btn--primary" :disabled="loading" @click="onSearch">查询</button>
        <button type="button" class="btn" :disabled="loading" @click="onReset">重置</button>
      </div>
    </div>

    <!-- 表格区 -->
    <div class="log-page__body">
      <TableContainer>
        <table class="data-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>级别</th>
              <th>模块</th>
              <th>内容</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in list" :key="row.id">
              <td class="nowrap">{{ formatDateTime(row.log_time) }}</td>
              <td class="nowrap"><span :class="levelClass(row.level)">{{ levelLabel(row.level) }}</span></td>
              <td class="nowrap">{{ row.module || '—' }}</td>
              <td class="content-cell"><SafeHtml :content="row.content || '—'" /></td>
            </tr>
            <tr v-if="list.length === 0">
              <td colspan="4" class="data-table__empty">暂无系统日志</td>
            </tr>
          </tbody>
        </table>
      </TableContainer>

      <Loading :visible="loading" text="加载中..." />
    </div>

    <!-- 分页 -->
    <Pagination
      :page="page"
      :page-size="pageSize"
      :total="total"
      @update:page="onPageChange"
      @update:page-size="onPageSizeChange"
    />
  </div>
</template>

<style scoped>
.log-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  gap: 16px;
}

.log-page__title {
  font-size: 18px;
  font-weight: 600;
  color: var(--color-text);
}

.log-page__filters {
  display: flex;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
  padding: 16px;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  border-radius: 10px;
}

.filter-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.filter-item--select {
  width: 160px;
}

.filter-item__label {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.filter-item__input {
  padding: 8px 12px;
  font-size: 14px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  color: var(--color-text);
}

.filter-actions {
  display: flex;
  gap: 10px;
}

.btn {
  padding: 8px 18px;
  font-size: 14px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg-elevated);
  color: var(--color-text);
  cursor: pointer;
  transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;
}

.btn:hover:not(:disabled) {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.btn--primary {
  background: var(--color-primary);
  color: var(--color-on-primary);
  border-color: var(--color-primary);
}

.btn--primary:hover:not(:disabled) {
  background: var(--color-primary-hover);
  color: var(--color-on-primary);
}

.btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.log-page__body {
  position: relative;
  flex: 1;
  min-height: 0;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

.data-table th,
.data-table td {
  padding: 10px 14px;
  text-align: left;
  border-bottom: 1px solid var(--color-border);
  color: var(--color-text);
  vertical-align: top;
}

.data-table thead th {
  background: var(--color-bg-elevated);
  color: var(--color-text-secondary);
  font-weight: 600;
  white-space: nowrap;
}

.nowrap {
  white-space: nowrap;
}

.content-cell {
  max-width: 420px;
  word-break: break-word;
}

.data-table__empty {
  text-align: center;
  color: var(--color-text-secondary);
  padding: 32px 0;
}

/* 级别标签：不同级别不同配色（对比清晰，规范 24） */
.level-tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 500;
}

.level-tag--info {
  background: #e6f4ff;
  color: #0958d9;
}

.level-tag--warning {
  background: #fffbe6;
  color: #ad6800;
}

.level-tag--error {
  background: #fff1f0;
  color: #a8071a;
}
</style>

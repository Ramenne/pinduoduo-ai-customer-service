<!--
  风控日志页面（需求 19.2 / 19.3）
  职责：后端分页查询风控日志，支持按店铺、风控类型与北京时间范围筛选；风控类型枚举中文展示（需求 13.4）。
  规范：后端分页（规范 28）、按店铺/时间范围筛选（需求 19.3）、表格固定高度内部滚动（规范 29）、
        全中文（规范 27）、加载遮罩 + 转圈（规范 23）、XSS 安全渲染（规范 22）、
        响应式（规范 20）、错误提示统一 showToast（规范 2/4）、不写死 localhost（规范 21）；
        仅查询不删除（需求 19.5）。
-->
<script setup>
import { computed, onMounted, ref } from 'vue'
import { Loading, TableContainer, Pagination, Select, SafeHtml } from '@/components/common'
import { fetchRiskLogs } from '@/api/logs_api'
import { fetchShopOptions } from '@/api/shop_api'
import { fetchRiskTypes } from '@/api/risk_control_api'
import { formatDateTime } from '@/utils/format'

// 加载态
const loading = ref(false)

// 筛选条件：店铺、风控类型、起止时间
const filters = ref({
  shop_pk: '',
  risk_type: '',
  start_time: '',
  end_time: '',
})

// 店铺下拉选项（含「全部店铺」）
const shopOptions = ref([{ value: '', label: '全部店铺' }])
// 风控类型枚举：key -> 中文文案，用于表格展示
const riskTypeMap = ref({})
// 风控类型下拉选项（含「全部类型」）
const riskTypeOptions = ref([{ value: '', label: '全部类型' }])

// 分页与列表
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const list = ref([])

// 风控类型中文展示
function riskTypeLabel(key) {
  if (!key) {
    return '—'
  }
  return riskTypeMap.value[key] || key
}

// 拉取店铺下拉选项
async function loadShopOptions() {
  const options = await fetchShopOptions()
  shopOptions.value = [{ value: '', label: '全部店铺' }, ...options]
}

// 拉取风控类型枚举字典
async function loadRiskTypes() {
  const data = await fetchRiskTypes()
  const items = Array.isArray(data) ? data : []
  const map = {}
  const options = [{ value: '', label: '全部类型' }]
  for (const item of items) {
    map[item.key] = item.label
    options.push({ value: item.key, label: item.label })
  }
  riskTypeMap.value = map
  riskTypeOptions.value = options
}

// 拉取风控日志
async function loadLogs() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (filters.value.shop_pk !== '') {
      params.shop_pk = filters.value.shop_pk
    }
    if (filters.value.risk_type !== '') {
      params.risk_type = filters.value.risk_type
    }
    if (filters.value.start_time) {
      params.start_time = filters.value.start_time.replace('T', ' ')
    }
    if (filters.value.end_time) {
      params.end_time = filters.value.end_time.replace('T', ' ')
    }
    const data = await fetchRiskLogs(params)
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
  filters.value = { shop_pk: '', risk_type: '', start_time: '', end_time: '' }
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

onMounted(() => {
  loadShopOptions()
  loadRiskTypes()
  loadLogs()
})
</script>

<template>
  <div class="log-page">
    <h2 class="log-page__title">风控日志</h2>

    <!-- 筛选区 -->
    <div class="log-page__filters">
      <div class="filter-item filter-item--select">
        <span class="filter-item__label">店铺</span>
        <Select v-model="filters.shop_pk" :options="shopOptions" placeholder="全部店铺" />
      </div>
      <div class="filter-item filter-item--select">
        <span class="filter-item__label">风控类型</span>
        <Select v-model="filters.risk_type" :options="riskTypeOptions" placeholder="全部类型" />
      </div>
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
              <th>店铺</th>
              <th>风控类型</th>
              <th>触发原因</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in list" :key="row.id">
              <td class="nowrap">{{ formatDateTime(row.log_time) }}</td>
              <td class="nowrap">{{ row.shop_pk }}</td>
              <td class="nowrap">{{ riskTypeLabel(row.risk_type) }}</td>
              <td class="content-cell"><SafeHtml :content="row.trigger_reason || '—'" /></td>
            </tr>
            <tr v-if="list.length === 0">
              <td colspan="4" class="data-table__empty">暂无风控日志</td>
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
  width: 180px;
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
  max-width: 360px;
  word-break: break-word;
}

.data-table__empty {
  text-align: center;
  color: var(--color-text-secondary);
  padding: 32px 0;
}
</style>

<!--
  消息通知页面（需求 18.5）
  职责：后端分页查询通知记录（系统事件 / 测试发送的发送结果），支持按事件类型与发送结果筛选。
  规范：后端分页（规范 28）、表格固定高度内部滚动（规范 29）、全中文（规范 27）、
        加载遮罩 + 转圈（规范 23）、XSS 安全渲染（规范 22）、响应式（规范 20）、
        错误提示统一 showToast（规范 2/4）、不写死 localhost（规范 21）。
-->
<script setup>
import { onMounted, ref } from 'vue'
import { Loading, TableContainer, Pagination, Select, SafeHtml } from '@/components/common'
import { fetchNotifyRecords } from '@/api/notify_api'
import { formatDateTime } from '@/utils/format'

// 加载态
const loading = ref(false)

// 筛选条件：事件类型、发送结果
const filters = ref({ event_type: '', send_result: '' })

// 事件类型下拉选项（与后端 EVENT_TYPE_LABELS 一致）
const eventTypeOptions = [
  { value: '', label: '全部事件' },
  { value: 'connection_disconnected', label: '连接断开' },
  { value: 'login_expired', label: '登录态失效' },
  { value: 'risk_triggered', label: '风控触发' },
]

// 发送结果下拉选项
const sendResultOptions = [
  { value: '', label: '全部结果' },
  { value: 'success', label: '成功' },
  { value: 'failed', label: '失败' },
]

// 发送结果中文展示
const SEND_RESULT_LABELS = { success: '成功', failed: '失败' }
function sendResultLabel(key) {
  if (!key) {
    return '—'
  }
  return SEND_RESULT_LABELS[key] || key
}

// 分页与列表
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const list = ref([])

// 拉取通知记录
async function loadRecords() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (filters.value.event_type !== '') {
      params.event_type = filters.value.event_type
    }
    if (filters.value.send_result !== '') {
      params.send_result = filters.value.send_result
    }
    const data = await fetchNotifyRecords(params)
    list.value = (data && data.list) || []
    total.value = (data && data.total) || 0
  } finally {
    loading.value = false
  }
}

function onSearch() {
  page.value = 1
  loadRecords()
}

function onReset() {
  filters.value = { event_type: '', send_result: '' }
  page.value = 1
  loadRecords()
}

function onPageChange(next) {
  page.value = next
  loadRecords()
}
function onPageSizeChange(size) {
  pageSize.value = size
}

onMounted(loadRecords)
</script>

<template>
  <div class="notify-page">
    <h2 class="notify-page__title">消息通知</h2>

    <!-- 筛选区 -->
    <div class="notify-page__filters">
      <div class="filter-item filter-item--select">
        <span class="filter-item__label">事件类型</span>
        <Select v-model="filters.event_type" :options="eventTypeOptions" placeholder="全部事件" />
      </div>
      <div class="filter-item filter-item--select">
        <span class="filter-item__label">发送结果</span>
        <Select v-model="filters.send_result" :options="sendResultOptions" placeholder="全部结果" />
      </div>
      <div class="filter-actions">
        <button type="button" class="btn btn--primary" :disabled="loading" @click="onSearch">查询</button>
        <button type="button" class="btn" :disabled="loading" @click="onReset">重置</button>
      </div>
    </div>

    <!-- 表格区 -->
    <div class="notify-page__body">
      <TableContainer>
        <table class="data-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>渠道</th>
              <th>事件类型</th>
              <th>内容</th>
              <th>发送结果</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in list" :key="row.id">
              <td class="nowrap">{{ formatDateTime(row.log_time) }}</td>
              <td class="nowrap">{{ row.channel_id ?? '—' }}</td>
              <td class="nowrap">{{ row.event_label || '测试发送' }}</td>
              <td class="content-cell"><SafeHtml :content="row.content || '—'" /></td>
              <td class="nowrap">
                <span :class="row.send_result === 'success' ? 'status-tag status-tag--on' : 'status-tag status-tag--off'">
                  {{ sendResultLabel(row.send_result) }}
                </span>
              </td>
            </tr>
            <tr v-if="list.length === 0">
              <td colspan="5" class="data-table__empty">暂无通知记录</td>
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
.notify-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  gap: 16px;
}

.notify-page__title {
  font-size: 18px;
  font-weight: 600;
  color: var(--color-text);
}

.notify-page__filters {
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

.notify-page__body {
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

.status-tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 12px;
}

.status-tag--on {
  background: #f0f9eb;
  color: #237804;
}

.status-tag--off {
  background: #fff1f0;
  color: #a8071a;
}
</style>

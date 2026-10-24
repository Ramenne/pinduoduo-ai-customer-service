<!--
  意见反馈管理页面（需求 21.5）
  职责：管理员查看反馈列表（后端分页、按处理状态筛选）、查看详情并处理回复。
  规范要点：后端分页（规范 28）、加载遮罩 + 转圈（规范 23）、showToast 提示（规范 6）、
        弹窗仅按钮关闭（规范 7）、表格固定高度内部滚动（规范 29）、
        用户输入经 SafeHtml 防 XSS（规范 22）、全中文、响应式（规范 27/20）。
-->
<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Loading, Pagination, TableContainer, FormModal, Select, SafeHtml } from '@/components/common'
import { showToast, TOAST_TYPE } from '@/utils/toast'
import { formatDateTime } from '@/utils/format'
import { listFeedbacks, replyFeedback } from '@/api/feedback_api'

const loading = ref(false)
const feedbacks = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const statusFilter = ref('')

// 处理状态枚举（与后端 feedback_status 字典一致）
const statusOptions = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '待处理' },
  { value: 'processing', label: '处理中' },
  { value: 'done', label: '已处理' },
  { value: 'closed', label: '已关闭' },
]
const replyStatusOptions = [
  { value: 'pending', label: '待处理' },
  { value: 'processing', label: '处理中' },
  { value: 'done', label: '已处理' },
  { value: 'closed', label: '已关闭' },
]
const STATUS_LABEL = { pending: '待处理', processing: '处理中', done: '已处理', closed: '已关闭' }

// 处理回复弹窗
const replyOpen = ref(false)
const replySaving = ref(false)
const replyForm = reactive({ id: null, content: '', contact: '', reply: '', status: 'processing' })

async function loadFeedbacks() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (statusFilter.value) {
      params.status = statusFilter.value
    }
    const data = await listFeedbacks(params)
    feedbacks.value = (data && data.list) || []
    total.value = (data && data.total) || 0
  } finally {
    loading.value = false
  }
}

onMounted(loadFeedbacks)

function onPageChange(p) {
  page.value = p
  loadFeedbacks()
}
function onPageSizeChange(size) {
  pageSize.value = size
  loadFeedbacks()
}
function onStatusFilterChange() {
  page.value = 1
  loadFeedbacks()
}

// 状态中文文案
function statusLabel(status) {
  return STATUS_LABEL[status] || status
}

// 打开处理回复弹窗
function openReply(fb) {
  replyForm.id = fb.id
  replyForm.content = fb.content
  replyForm.contact = fb.contact || ''
  replyForm.reply = fb.reply || ''
  replyForm.status = fb.status || 'processing'
  replyOpen.value = true
}

// 提交处理回复
async function submitReply() {
  replySaving.value = true
  try {
    await replyFeedback(replyForm.id, { reply: replyForm.reply, status: replyForm.status })
    showToast('反馈已处理', TOAST_TYPE.SUCCESS)
    replyOpen.value = false
    await loadFeedbacks()
  } finally {
    replySaving.value = false
  }
}
</script>

<template>
  <div class="fb-page">
    <div class="fb-page__header">
      <h2 class="fb-page__title">意见反馈</h2>
      <div class="fb-page__filter">
        <Select v-model="statusFilter" :options="statusOptions" @change="onStatusFilterChange" />
      </div>
    </div>

    <TableContainer max-height="calc(100vh - 240px)">
      <table class="data-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>反馈内容</th>
            <th>联系方式</th>
            <th>状态</th>
            <th>提交时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="fb in feedbacks" :key="fb.id">
            <td>{{ fb.id }}</td>
            <td class="content-cell"><SafeHtml :content="fb.content" /></td>
            <td><SafeHtml :content="fb.contact || '-'" /></td>
            <td>{{ statusLabel(fb.status) }}</td>
            <td>{{ formatDateTime(fb.created_at) }}</td>
            <td class="actions">
              <button class="link-btn" @click="openReply(fb)">处理 / 回复</button>
            </td>
          </tr>
          <tr v-if="feedbacks.length === 0">
            <td colspan="6" class="empty">暂无反馈数据</td>
          </tr>
        </tbody>
      </table>
    </TableContainer>

    <Pagination
      :page="page" :page-size="pageSize" :total="total"
      @update:page="onPageChange" @update:page-size="onPageSizeChange"
    />

    <!-- 处理回复弹窗 -->
    <FormModal v-model="replyOpen" title="处理反馈" confirm-text="提交处理" :loading="replySaving" @confirm="submitReply">
      <div class="form-row">
        <label class="form-label">反馈内容</label>
        <div class="readonly-box"><SafeHtml :content="replyForm.content" /></div>
      </div>
      <div class="form-row">
        <label class="form-label">联系方式</label>
        <div class="readonly-box"><SafeHtml :content="replyForm.contact || '-'" /></div>
      </div>
      <div class="form-row">
        <label class="form-label">处理状态</label>
        <Select v-model="replyForm.status" :options="replyStatusOptions" />
      </div>
      <div class="form-row">
        <label class="form-label">回复内容</label>
        <textarea v-model="replyForm.reply" class="form-textarea" rows="4"></textarea>
      </div>
    </FormModal>

    <Loading :visible="loading" full-screen text="加载中..." />
  </div>
</template>

<style scoped>
.fb-page {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
}
.fb-page__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.fb-page__title {
  font-size: 18px;
  font-weight: 600;
  color: var(--color-text);
}
.fb-page__filter {
  width: 160px;
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
}
.data-table thead th {
  background: var(--color-bg-elevated);
  color: var(--color-text-secondary);
  font-weight: 600;
}
.content-cell {
  max-width: 360px;
}
.empty {
  text-align: center;
  color: var(--color-text-secondary);
  padding: 32px 0;
}
.actions {
  display: flex;
  gap: 12px;
}
.link-btn {
  border: none;
  background: transparent;
  color: var(--color-primary);
  cursor: pointer;
  font-size: 13px;
  padding: 0;
}
.form-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 14px;
}
.form-label {
  font-size: 13px;
  color: var(--color-text-secondary);
}
.form-textarea {
  width: 100%;
  padding: 8px 12px;
  font-size: 14px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  color: var(--color-text);
  resize: vertical;
}
.readonly-box {
  padding: 8px 12px;
  font-size: 14px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-hover-bg);
  color: var(--color-text);
  word-break: break-word;
}
</style>

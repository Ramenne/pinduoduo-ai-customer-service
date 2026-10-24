<!--
  意见反馈页面（用户端，需求 21.5）
  职责：所有登录用户提交意见反馈（内容 + 联系方式），并查看本人历史反馈与处理回复。
  规范要点：后端分页（规范 28）、加载遮罩 + 转圈（规范 23）、showToast 提示（规范 6）、
        表格固定高度内部滚动（规范 29）、用户输入经 SafeHtml 防 XSS（规范 22）、
        全中文、响应式（规范 27/20）。
-->
<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Loading, Pagination, TableContainer, SafeHtml } from '@/components/common'
import { showToast, TOAST_TYPE } from '@/utils/toast'
import { formatDateTime } from '@/utils/format'
import { submitFeedback, listMyFeedbacks } from '@/api/feedback_api'

const loading = ref(false)
const submitting = ref(false)

// 提交表单
const form = reactive({ content: '', contact: '' })

// 本人反馈列表
const feedbacks = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

const STATUS_LABEL = { pending: '待处理', processing: '处理中', done: '已处理', closed: '已关闭' }

async function loadMine() {
  loading.value = true
  try {
    const data = await listMyFeedbacks({ page: page.value, page_size: pageSize.value })
    feedbacks.value = (data && data.list) || []
    total.value = (data && data.total) || 0
  } finally {
    loading.value = false
  }
}

onMounted(loadMine)

function onPageChange(p) {
  page.value = p
  loadMine()
}
function onPageSizeChange(size) {
  pageSize.value = size
  loadMine()
}

function statusLabel(status) {
  return STATUS_LABEL[status] || status
}

// 提交反馈
async function onSubmit() {
  if (!form.content.trim()) {
    showToast('请填写反馈内容', TOAST_TYPE.WARNING)
    return
  }
  submitting.value = true
  try {
    await submitFeedback({ content: form.content.trim(), contact: form.contact.trim() || null })
    showToast('反馈已提交，感谢您的建议', TOAST_TYPE.SUCCESS)
    form.content = ''
    form.contact = ''
    page.value = 1
    await loadMine()
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="feedback-page">
    <h2 class="feedback-page__title">意见反馈</h2>

    <!-- 提交反馈卡片 -->
    <section class="card">
      <h3 class="card__title">提交反馈</h3>
      <div class="form-row">
        <label class="form-label">反馈内容</label>
        <textarea v-model="form.content" class="form-textarea" rows="4" placeholder="请描述您的意见或建议"></textarea>
      </div>
      <div class="form-row">
        <label class="form-label">联系方式（可选）</label>
        <input v-model="form.contact" class="form-input" placeholder="微信 / QQ / 邮箱，便于我们与您联系" />
      </div>
      <button class="btn-primary" :disabled="submitting" @click="onSubmit">提交反馈</button>
    </section>

    <!-- 本人反馈列表 -->
    <section class="card card--list">
      <h3 class="card__title">我的反馈</h3>
      <TableContainer max-height="calc(100vh - 460px)">
        <table class="data-table">
          <thead>
            <tr>
              <th>反馈内容</th>
              <th>状态</th>
              <th>管理员回复</th>
              <th>提交时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="fb in feedbacks" :key="fb.id">
              <td class="content-cell"><SafeHtml :content="fb.content" /></td>
              <td>{{ statusLabel(fb.status) }}</td>
              <td class="content-cell"><SafeHtml :content="fb.reply || '-'" /></td>
              <td>{{ formatDateTime(fb.created_at) }}</td>
            </tr>
            <tr v-if="feedbacks.length === 0">
              <td colspan="4" class="empty">暂无反馈记录</td>
            </tr>
          </tbody>
        </table>
      </TableContainer>
      <Pagination
        :page="page" :page-size="pageSize" :total="total"
        @update:page="onPageChange" @update:page-size="onPageSizeChange"
      />
    </section>

    <Loading :visible="loading" full-screen text="加载中..." />
  </div>
</template>

<style scoped>
.feedback-page {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.feedback-page__title {
  font-size: 18px;
  font-weight: 600;
  color: var(--color-text);
}
.card {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.card__title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text);
}
.form-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.form-label {
  font-size: 13px;
  color: var(--color-text-secondary);
}
.form-input,
.form-textarea {
  width: 100%;
  padding: 8px 12px;
  font-size: 14px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  color: var(--color-text);
}
.form-textarea {
  resize: vertical;
}
.btn-primary {
  align-self: flex-start;
  padding: 8px 18px;
  border: none;
  border-radius: 8px;
  background: var(--color-primary);
  color: var(--color-on-primary);
  font-size: 14px;
  cursor: pointer;
}
.btn-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
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
  max-width: 320px;
}
.empty {
  text-align: center;
  color: var(--color-text-secondary);
  padding: 32px 0;
}
</style>

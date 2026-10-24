<!--
  公告管理页面（需求 21.3）
  职责：管理员新增、编辑、启停用、逻辑删除公告，列表后端分页。
  规范要点：后端分页（规范 28）、加载遮罩 + 转圈（规范 23）、showToast 提示（规范 6）、
        弹窗仅按钮关闭（规范 7）、表格固定高度内部滚动（规范 29）、
        用户输入经 SafeHtml 防 XSS（规范 22）、全中文、响应式（规范 27/20）。
-->
<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Loading, Pagination, TableContainer, FormModal, ConfirmModal, SafeHtml } from '@/components/common'
import { showToast, TOAST_TYPE } from '@/utils/toast'
import { formatDateTime } from '@/utils/format'
import {
  listAnnouncements, createAnnouncement, updateAnnouncement,
  setAnnouncementStatus, deleteAnnouncement,
} from '@/api/announcements_api'

const loading = ref(false)
const announcements = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

// 新增 / 编辑弹窗
const editOpen = ref(false)
const editSaving = ref(false)
const editForm = reactive({ id: null, title: '', content: '', enabled: true })
const isEdit = ref(false)

// 删除确认弹窗
const deleteOpen = ref(false)
const deleteSaving = ref(false)
const deleteTarget = reactive({ id: null, title: '' })

async function loadAnnouncements() {
  loading.value = true
  try {
    const data = await listAnnouncements({ page: page.value, page_size: pageSize.value })
    announcements.value = (data && data.list) || []
    total.value = (data && data.total) || 0
  } finally {
    loading.value = false
  }
}

onMounted(loadAnnouncements)

function onPageChange(p) {
  page.value = p
  loadAnnouncements()
}
function onPageSizeChange(size) {
  pageSize.value = size
  loadAnnouncements()
}

// 打开新增
function openCreate() {
  isEdit.value = false
  editForm.id = null
  editForm.title = ''
  editForm.content = ''
  editForm.enabled = true
  editOpen.value = true
}

// 打开编辑
function openEdit(ann) {
  isEdit.value = true
  editForm.id = ann.id
  editForm.title = ann.title
  editForm.content = ann.content
  editForm.enabled = ann.enabled
  editOpen.value = true
}

// 提交新增 / 编辑
async function submitEdit() {
  if (!editForm.title.trim()) {
    showToast('请填写公告标题', TOAST_TYPE.WARNING)
    return
  }
  if (!editForm.content.trim()) {
    showToast('请填写公告内容', TOAST_TYPE.WARNING)
    return
  }
  editSaving.value = true
  try {
    if (isEdit.value) {
      await updateAnnouncement(editForm.id, { title: editForm.title.trim(), content: editForm.content.trim() })
      showToast('公告已更新', TOAST_TYPE.SUCCESS)
    } else {
      await createAnnouncement({ title: editForm.title.trim(), content: editForm.content.trim(), enabled: editForm.enabled })
      showToast('公告已发布', TOAST_TYPE.SUCCESS)
      page.value = 1
    }
    editOpen.value = false
    await loadAnnouncements()
  } finally {
    editSaving.value = false
  }
}

// 启停用
async function toggleStatus(ann) {
  loading.value = true
  try {
    await setAnnouncementStatus(ann.id, !ann.enabled)
    showToast(!ann.enabled ? '已启用' : '已停用', TOAST_TYPE.SUCCESS)
    await loadAnnouncements()
  } finally {
    loading.value = false
  }
}

// 打开删除确认
function openDelete(ann) {
  deleteTarget.id = ann.id
  deleteTarget.title = ann.title
  deleteOpen.value = true
}

// 确认删除（逻辑删除）
async function confirmDelete() {
  deleteSaving.value = true
  try {
    await deleteAnnouncement(deleteTarget.id)
    showToast('公告已删除', TOAST_TYPE.SUCCESS)
    deleteOpen.value = false
    await loadAnnouncements()
  } finally {
    deleteSaving.value = false
  }
}
</script>

<template>
  <div class="ann-page">
    <div class="ann-page__header">
      <h2 class="ann-page__title">公告管理</h2>
      <button class="btn-primary" @click="openCreate">+ 发布公告</button>
    </div>

    <TableContainer max-height="calc(100vh - 240px)">
      <table class="data-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>标题</th>
            <th>状态</th>
            <th>发布时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="ann in announcements" :key="ann.id">
            <td>{{ ann.id }}</td>
            <td class="title-cell"><SafeHtml :content="ann.title" /></td>
            <td>
              <span :class="['status-tag', ann.enabled ? 'status-tag--on' : 'status-tag--off']">
                {{ ann.enabled ? '已上线' : '已下线' }}
              </span>
            </td>
            <td>{{ formatDateTime(ann.publish_at || ann.created_at) }}</td>
            <td class="actions">
              <button class="link-btn" @click="openEdit(ann)">编辑</button>
              <button class="link-btn" @click="toggleStatus(ann)">{{ ann.enabled ? '下线' : '上线' }}</button>
              <button class="link-btn link-btn--danger" @click="openDelete(ann)">删除</button>
            </td>
          </tr>
          <tr v-if="announcements.length === 0">
            <td colspan="5" class="empty">暂无公告数据</td>
          </tr>
        </tbody>
      </table>
    </TableContainer>

    <Pagination
      :page="page" :page-size="pageSize" :total="total"
      @update:page="onPageChange" @update:page-size="onPageSizeChange"
    />

    <!-- 新增 / 编辑公告弹窗 -->
    <FormModal v-model="editOpen" :title="isEdit ? '编辑公告' : '发布公告'" :loading="editSaving" @confirm="submitEdit">
      <div class="form-row">
        <label class="form-label">标题</label>
        <input v-model="editForm.title" class="form-input" />
      </div>
      <div class="form-row">
        <label class="form-label">正文</label>
        <textarea v-model="editForm.content" class="form-textarea" rows="6"></textarea>
      </div>
      <div v-if="!isEdit" class="form-row">
        <label class="check-row"><input type="checkbox" v-model="editForm.enabled" /> 发布后立即上线</label>
      </div>
    </FormModal>

    <!-- 删除确认弹窗 -->
    <ConfirmModal
      v-model="deleteOpen"
      title="删除公告"
      :message="`确认删除公告「${deleteTarget.title}」吗？删除后将不再展示（数据仍保留）。`"
      type="danger"
      :loading="deleteSaving"
      @confirm="confirmDelete"
    />

    <Loading :visible="loading" full-screen text="加载中..." />
  </div>
</template>

<style scoped>
.ann-page {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
}
.ann-page__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.ann-page__title {
  font-size: 18px;
  font-weight: 600;
  color: var(--color-text);
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
.title-cell {
  max-width: 360px;
}
.empty {
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
.link-btn--danger {
  color: #ff4d4f;
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
.check-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: var(--color-text);
  cursor: pointer;
}
.btn-primary {
  padding: 8px 18px;
  border: none;
  border-radius: 8px;
  background: var(--color-primary);
  color: var(--color-on-primary);
  font-size: 14px;
  cursor: pointer;
}
</style>

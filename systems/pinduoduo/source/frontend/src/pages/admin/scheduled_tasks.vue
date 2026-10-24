<!--
  定时任务页面（需求 21.2）
  职责：管理员查看与配置定时任务（调度方式 / 配置 / 启停用），并查看执行日志。
  规范要点：后端分页（规范 28）、加载遮罩 + 转圈（规范 23）、showToast 提示（规范 6）、
        弹窗仅按钮关闭（规范 7）、表格固定高度内部滚动（规范 29）、
        全中文、响应式（规范 27/20）。
-->
<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Loading, Pagination, TableContainer, FormModal, Select } from '@/components/common'
import { showToast, TOAST_TYPE } from '@/utils/toast'
import { formatDateTime } from '@/utils/format'
import {
  listScheduledTasks, updateScheduledTask, setScheduledTaskStatus, listTaskRunLogs,
} from '@/api/scheduled_tasks_api'

// 当前页签：tasks=任务配置，logs=执行日志
const activeTab = ref('tasks')

// 任务列表
const loading = ref(false)
const tasks = ref([])
const tasksTotal = ref(0)
const tasksPage = ref(1)
const tasksPageSize = ref(20)

// 执行日志列表
const logs = ref([])
const logsTotal = ref(0)
const logsPage = ref(1)
const logsPageSize = ref(20)

// 调度方式枚举（与后端 schedule_type 字典一致）
const scheduleTypeOptions = [
  { value: 'cron', label: 'Cron 表达式' },
  { value: 'interval', label: '固定间隔（秒）' },
]
const SCHEDULE_TYPE_LABEL = { cron: 'Cron 表达式', interval: '固定间隔' }
const RESULT_LABEL = { success: '成功', failed: '失败' }

// 编辑任务弹窗
const editOpen = ref(false)
const editSaving = ref(false)
const editForm = reactive({ id: null, task_name: '', schedule_type: 'interval', schedule_config: '', enabled: true })

async function loadTasks() {
  loading.value = true
  try {
    const data = await listScheduledTasks({ page: tasksPage.value, page_size: tasksPageSize.value })
    tasks.value = (data && data.list) || []
    tasksTotal.value = (data && data.total) || 0
  } finally {
    loading.value = false
  }
}

async function loadLogs() {
  loading.value = true
  try {
    const data = await listTaskRunLogs({ page: logsPage.value, page_size: logsPageSize.value })
    logs.value = (data && data.list) || []
    logsTotal.value = (data && data.total) || 0
  } finally {
    loading.value = false
  }
}

onMounted(loadTasks)

// 切换页签
function switchTab(tab) {
  activeTab.value = tab
  if (tab === 'tasks') {
    loadTasks()
  } else {
    loadLogs()
  }
}

// 分页事件
function onTasksPageChange(p) {
  tasksPage.value = p
  loadTasks()
}
function onTasksPageSizeChange(size) {
  tasksPageSize.value = size
  loadTasks()
}
function onLogsPageChange(p) {
  logsPage.value = p
  loadLogs()
}
function onLogsPageSizeChange(size) {
  logsPageSize.value = size
  loadLogs()
}

// 枚举中文
function scheduleTypeLabel(type) {
  return SCHEDULE_TYPE_LABEL[type] || type
}
function resultLabel(result) {
  return RESULT_LABEL[result] || result
}

// 打开编辑任务弹窗
function openEdit(task) {
  editForm.id = task.id
  editForm.task_name = task.task_name
  editForm.schedule_type = task.schedule_type
  editForm.schedule_config = task.schedule_config || ''
  editForm.enabled = task.enabled
  editOpen.value = true
}

// 提交编辑
async function submitEdit() {
  if (!editForm.schedule_config.trim()) {
    showToast('请填写调度配置', TOAST_TYPE.WARNING)
    return
  }
  editSaving.value = true
  try {
    await updateScheduledTask(editForm.id, {
      schedule_type: editForm.schedule_type,
      schedule_config: editForm.schedule_config.trim(),
      enabled: editForm.enabled,
    })
    showToast('定时任务已更新', TOAST_TYPE.SUCCESS)
    editOpen.value = false
    await loadTasks()
  } finally {
    editSaving.value = false
  }
}

// 启停用
async function toggleStatus(task) {
  loading.value = true
  try {
    await setScheduledTaskStatus(task.id, !task.enabled)
    showToast(!task.enabled ? '已启用' : '已停用', TOAST_TYPE.SUCCESS)
    await loadTasks()
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="task-page">
    <h2 class="task-page__title">定时任务</h2>

    <!-- 页签切换 -->
    <div class="tab-bar">
      <button class="tab" :class="{ 'tab--active': activeTab === 'tasks' }" @click="switchTab('tasks')">任务配置</button>
      <button class="tab" :class="{ 'tab--active': activeTab === 'logs' }" @click="switchTab('logs')">执行日志</button>
    </div>

    <!-- 任务配置 -->
    <template v-if="activeTab === 'tasks'">
      <TableContainer max-height="calc(100vh - 290px)">
        <table class="data-table">
          <thead>
            <tr>
              <th>任务名称</th>
              <th>任务键</th>
              <th>调度方式</th>
              <th>调度配置</th>
              <th>状态</th>
              <th>上次执行</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="task in tasks" :key="task.id">
              <td>{{ task.task_name }}</td>
              <td>{{ task.task_key }}</td>
              <td>{{ scheduleTypeLabel(task.schedule_type) }}</td>
              <td>{{ task.schedule_config }}</td>
              <td>
                <span :class="['status-tag', task.enabled ? 'status-tag--on' : 'status-tag--off']">
                  {{ task.enabled ? '启用' : '停用' }}
                </span>
              </td>
              <td>{{ formatDateTime(task.last_run_at) }}</td>
              <td class="actions">
                <button class="link-btn" @click="openEdit(task)">配置</button>
                <button class="link-btn" @click="toggleStatus(task)">{{ task.enabled ? '停用' : '启用' }}</button>
              </td>
            </tr>
            <tr v-if="tasks.length === 0">
              <td colspan="7" class="empty">暂无定时任务</td>
            </tr>
          </tbody>
        </table>
      </TableContainer>
      <Pagination
        :page="tasksPage" :page-size="tasksPageSize" :total="tasksTotal"
        @update:page="onTasksPageChange" @update:page-size="onTasksPageSizeChange"
      />
    </template>

    <!-- 执行日志 -->
    <template v-else>
      <TableContainer max-height="calc(100vh - 290px)">
        <table class="data-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>任务键</th>
              <th>执行结果</th>
              <th>执行信息</th>
              <th>执行时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="log in logs" :key="log.id">
              <td>{{ log.id }}</td>
              <td>{{ log.task_key }}</td>
              <td>
                <span :class="['status-tag', log.run_result === 'success' ? 'status-tag--on' : 'status-tag--off']">
                  {{ resultLabel(log.run_result) }}
                </span>
              </td>
              <td class="content-cell">{{ log.message || '-' }}</td>
              <td>{{ formatDateTime(log.log_time || log.created_at) }}</td>
            </tr>
            <tr v-if="logs.length === 0">
              <td colspan="5" class="empty">暂无执行日志</td>
            </tr>
          </tbody>
        </table>
      </TableContainer>
      <Pagination
        :page="logsPage" :page-size="logsPageSize" :total="logsTotal"
        @update:page="onLogsPageChange" @update:page-size="onLogsPageSizeChange"
      />
    </template>

    <!-- 编辑任务弹窗 -->
    <FormModal v-model="editOpen" :title="`配置定时任务 - ${editForm.task_name}`" :loading="editSaving" @confirm="submitEdit">
      <div class="form-row">
        <label class="form-label">调度方式</label>
        <Select v-model="editForm.schedule_type" :options="scheduleTypeOptions" />
      </div>
      <div class="form-row">
        <label class="form-label">
          调度配置（{{ editForm.schedule_type === 'cron' ? 'Cron 表达式，如 0 3 * * *' : '间隔秒数，如 600' }}）
        </label>
        <input v-model="editForm.schedule_config" class="form-input" />
      </div>
      <div class="form-row">
        <label class="check-row"><input type="checkbox" v-model="editForm.enabled" /> 启用该任务</label>
      </div>
    </FormModal>

    <Loading :visible="loading" full-screen text="加载中..." />
  </div>
</template>

<style scoped>
.task-page {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
}
.task-page__title {
  font-size: 18px;
  font-weight: 600;
  margin-bottom: 12px;
  color: var(--color-text);
}
.tab-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}
.tab {
  padding: 8px 18px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-bg);
  color: var(--color-text-secondary);
  font-size: 14px;
  cursor: pointer;
}
.tab--active {
  background: var(--color-primary-light);
  color: var(--color-primary);
  border-color: var(--color-primary);
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
  word-break: break-word;
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
.form-input {
  width: 100%;
  padding: 8px 12px;
  font-size: 14px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  color: var(--color-text);
}
.check-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: var(--color-text);
  cursor: pointer;
}
</style>

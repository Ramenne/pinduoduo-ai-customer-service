<!--
  用户与角色权限管理页面（需求 2.1/2.2/2.7/2.8）
  职责：管理员创建用户并指定角色、修改用户角色、启用 / 停用用户（逻辑删除），
        以及查看角色列表。
  规范要点：
    - 后端分页（规范 28）、加载遮罩 + 转圈（规范 23）、showToast 提示（规范 6）；
    - 弹窗仅「关闭 / 取消 / 确定」按钮关闭（规范 7）；
    - 表格固定高度内部滚动（规范 29）、用户输入经 SafeHtml 防 XSS（规范 22）；
    - 全中文、响应式（规范 27/20）。
-->
<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Loading, Pagination, TableContainer, FormModal, ConfirmModal, Select, SafeHtml } from '@/components/common'
import { showToast, TOAST_TYPE } from '@/utils/toast'
import { formatDateTime } from '@/utils/format'
import {
  listUsers, createUser, updateUserRole, updateUserStatus, listRoles,
} from '@/api/users_api'

// 列表数据与分页状态
const loading = ref(false)
const users = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

// 角色选项（用于创建 / 改角色下拉与展示角色名）
const roles = ref([])
const roleOptions = ref([])
const roleNameMap = reactive({})

// 创建用户弹窗
const createOpen = ref(false)
const createSaving = ref(false)
const createForm = reactive({ username: '', password: '', role_id: '', wechat: '', qq: '' })

// 改角色弹窗
const roleOpen = ref(false)
const roleSaving = ref(false)
const roleForm = reactive({ userId: null, username: '', role_id: '' })

// 启停用确认弹窗
const statusOpen = ref(false)
const statusSaving = ref(false)
const statusTarget = reactive({ userId: null, username: '', enabled: true })

// 加载角色列表（取较大页便于下拉展示）
async function loadRoles() {
  const data = await listRoles({ page: 1, page_size: 100 })
  roles.value = (data && data.list) || []
  roleOptions.value = roles.value.map((r) => ({ value: r.id, label: r.role_name }))
  roles.value.forEach((r) => {
    roleNameMap[r.id] = r.role_name
  })
}

// 加载用户列表（后端分页）
async function loadUsers() {
  loading.value = true
  try {
    const data = await listUsers({ page: page.value, page_size: pageSize.value })
    users.value = (data && data.list) || []
    total.value = (data && data.total) || 0
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadRoles()
  await loadUsers()
})

// 分页事件
function onPageChange(p) {
  page.value = p
  loadUsers()
}
function onPageSizeChange(size) {
  pageSize.value = size
  loadUsers()
}

// 角色名展示
function roleName(roleId) {
  return roleNameMap[roleId] || '未分配'
}

// 打开创建用户弹窗
function openCreate() {
  createForm.username = ''
  createForm.password = ''
  createForm.role_id = ''
  createForm.wechat = ''
  createForm.qq = ''
  createOpen.value = true
}

// 提交创建用户（前端校验用户名 / 密码必填）
async function submitCreate() {
  if (!createForm.username.trim()) {
    showToast('请填写用户名', TOAST_TYPE.WARNING)
    return
  }
  if (!createForm.password || createForm.password.length < 6) {
    showToast('密码长度不能少于 6 位', TOAST_TYPE.WARNING)
    return
  }
  createSaving.value = true
  try {
    await createUser({
      username: createForm.username.trim(),
      password: createForm.password,
      role_id: createForm.role_id || null,
      wechat: createForm.wechat || null,
      qq: createForm.qq || null,
    })
    showToast('用户创建成功', TOAST_TYPE.SUCCESS)
    createOpen.value = false
    page.value = 1
    await loadUsers()
  } finally {
    createSaving.value = false
  }
}

// 打开改角色弹窗
function openRole(user) {
  roleForm.userId = user.id
  roleForm.username = user.username
  roleForm.role_id = user.role_id || ''
  roleOpen.value = true
}

// 提交改角色
async function submitRole() {
  roleSaving.value = true
  try {
    await updateUserRole(roleForm.userId, roleForm.role_id || null)
    showToast('角色已更新', TOAST_TYPE.SUCCESS)
    roleOpen.value = false
    await loadUsers()
  } finally {
    roleSaving.value = false
  }
}

// 打开启停用确认
function openStatus(user, enabled) {
  statusTarget.userId = user.id
  statusTarget.username = user.username
  statusTarget.enabled = enabled
  statusOpen.value = true
}

// 确认启停用
async function confirmStatus() {
  statusSaving.value = true
  try {
    await updateUserStatus(statusTarget.userId, statusTarget.enabled)
    showToast(statusTarget.enabled ? '已启用' : '已停用', TOAST_TYPE.SUCCESS)
    statusOpen.value = false
    await loadUsers()
  } finally {
    statusSaving.value = false
  }
}
</script>

<template>
  <div class="user-page">
    <div class="user-page__header">
      <h2 class="user-page__title">用户与角色权限管理</h2>
      <button class="btn-primary" @click="openCreate">+ 新增用户</button>
    </div>

    <TableContainer max-height="calc(100vh - 240px)">
      <table class="data-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>用户名</th>
            <th>角色</th>
            <th>状态</th>
            <th>创建时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="user in users" :key="user.id">
            <td>{{ user.id }}</td>
            <td><SafeHtml :content="user.username" /></td>
            <td>{{ roleName(user.role_id) }}</td>
            <td>
              <span :class="['status-tag', user.status === 1 ? 'status-tag--on' : 'status-tag--off']">
                {{ user.status === 1 ? '启用' : '停用' }}
              </span>
            </td>
            <td>{{ formatDateTime(user.created_at) }}</td>
            <td class="actions">
              <button class="link-btn" @click="openRole(user)">改角色</button>
              <button v-if="user.status === 1" class="link-btn link-btn--danger" @click="openStatus(user, false)">停用</button>
              <button v-else class="link-btn" @click="openStatus(user, true)">启用</button>
            </td>
          </tr>
          <tr v-if="users.length === 0">
            <td colspan="6" class="empty">暂无用户数据</td>
          </tr>
        </tbody>
      </table>
    </TableContainer>

    <Pagination
      :page="page" :page-size="pageSize" :total="total"
      @update:page="onPageChange" @update:page-size="onPageSizeChange"
    />

    <!-- 新增用户弹窗 -->
    <FormModal v-model="createOpen" title="新增用户" :loading="createSaving" @confirm="submitCreate">
      <div class="form-row">
        <label class="form-label">用户名</label>
        <input v-model="createForm.username" class="form-input" />
      </div>
      <div class="form-row">
        <label class="form-label">密码（至少 6 位）</label>
        <input v-model="createForm.password" type="password" class="form-input" />
      </div>
      <div class="form-row">
        <label class="form-label">角色</label>
        <Select v-model="createForm.role_id" :options="roleOptions" placeholder="请选择角色" />
      </div>
      <div class="form-row">
        <label class="form-label">微信（可选）</label>
        <input v-model="createForm.wechat" class="form-input" />
      </div>
      <div class="form-row">
        <label class="form-label">QQ（可选）</label>
        <input v-model="createForm.qq" class="form-input" />
      </div>
    </FormModal>

    <!-- 改角色弹窗 -->
    <FormModal v-model="roleOpen" title="修改用户角色" :loading="roleSaving" @confirm="submitRole">
      <div class="form-row">
        <label class="form-label">用户名</label>
        <input :value="roleForm.username" class="form-input" disabled />
      </div>
      <div class="form-row">
        <label class="form-label">角色</label>
        <Select v-model="roleForm.role_id" :options="roleOptions" placeholder="请选择角色" />
      </div>
    </FormModal>

    <!-- 启停用确认弹窗 -->
    <ConfirmModal
      v-model="statusOpen"
      :title="statusTarget.enabled ? '启用用户' : '停用用户'"
      :message="`确认${statusTarget.enabled ? '启用' : '停用'}用户「${statusTarget.username}」吗？停用后该用户将无法登录。`"
      :type="statusTarget.enabled ? 'info' : 'danger'"
      :loading="statusSaving"
      @confirm="confirmStatus"
    />

    <Loading :visible="loading" full-screen text="加载中..." />
  </div>
</template>

<style scoped>
.user-page {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
}
.user-page__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.user-page__title {
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
.form-input {
  width: 100%;
  padding: 8px 12px;
  font-size: 14px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  color: var(--color-text);
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

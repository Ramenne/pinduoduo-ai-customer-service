<!--
  角色权限页面（需求 2.3/2.4）
  职责：管理员管理角色（新增 / 改名 / 启停用 / 设为默认注册角色）与为角色分配权限。
  说明：权限判断的集中逻辑在后端统一权限模块（需求 2.3/2.4）；本页提供角色维护与
        权限勾选界面，权限点由后端按资源分组返回（中文名来自数据字典，规范 15）。
  规范要点：后端分页（规范 28）、加载遮罩 + 转圈（规范 23）、showToast 提示（规范 6）、
        弹窗仅「关闭 / 取消 / 确定」按钮关闭（规范 7）、表格固定高度内部滚动（规范 29）、
        用户输入经 SafeHtml 防 XSS（规范 22）、全中文、响应式（规范 27/20）。
-->
<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Loading, Pagination, TableContainer, FormModal, ConfirmModal, SafeHtml } from '@/components/common'
import { showToast, TOAST_TYPE } from '@/utils/toast'
import { formatDateTime } from '@/utils/format'
import {
  listRoles, createRole, updateRole, updateRoleStatus, setDefaultRole,
  getRolePermissions, assignRolePermissions, listPermissions,
} from '@/api/users_api'

// 列表数据与分页状态
const loading = ref(false)
const roles = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

// 新增 / 编辑角色弹窗
const editOpen = ref(false)
const editSaving = ref(false)
const editForm = reactive({ id: null, role_name: '' })

// 启停用确认弹窗
const statusOpen = ref(false)
const statusSaving = ref(false)
const statusTarget = reactive({ id: null, role_name: '', enabled: true })

// 权限分配弹窗
const permOpen = ref(false)
const permSaving = ref(false)
const permLoading = ref(false)
const permGroups = ref([])            // [{resource_key, resource_name, actions:[{permission_id, action, action_name}]}]
const checkedPerms = reactive({})     // { [permission_id]: true }
const permTarget = reactive({ id: null, role_name: '' })

// 加载角色列表（后端分页）
async function loadRoles() {
  loading.value = true
  try {
    const data = await listRoles({ page: page.value, page_size: pageSize.value })
    roles.value = (data && data.list) || []
    total.value = (data && data.total) || 0
  } finally {
    loading.value = false
  }
}

onMounted(loadRoles)

function onPageChange(p) {
  page.value = p
  loadRoles()
}
function onPageSizeChange(size) {
  pageSize.value = size
  loadRoles()
}

// —— 新增 / 编辑角色 ——
function openCreate() {
  editForm.id = null
  editForm.role_name = ''
  editOpen.value = true
}
function openEdit(role) {
  editForm.id = role.id
  editForm.role_name = role.role_name
  editOpen.value = true
}
async function submitEdit() {
  const name = editForm.role_name.trim()
  if (!name) {
    showToast('请填写角色名称', TOAST_TYPE.WARNING)
    return
  }
  editSaving.value = true
  try {
    if (editForm.id) {
      await updateRole(editForm.id, { role_name: name })
      showToast('角色已更新', TOAST_TYPE.SUCCESS)
    } else {
      await createRole({ role_name: name })
      showToast('角色创建成功', TOAST_TYPE.SUCCESS)
      page.value = 1
    }
    editOpen.value = false
    await loadRoles()
  } finally {
    editSaving.value = false
  }
}

// —— 启停用 ——
function openStatus(role, enabled) {
  statusTarget.id = role.id
  statusTarget.role_name = role.role_name
  statusTarget.enabled = enabled
  statusOpen.value = true
}
async function confirmStatus() {
  statusSaving.value = true
  try {
    await updateRoleStatus(statusTarget.id, statusTarget.enabled)
    showToast(statusTarget.enabled ? '已启用' : '已停用', TOAST_TYPE.SUCCESS)
    statusOpen.value = false
    await loadRoles()
  } finally {
    statusSaving.value = false
  }
}

// —— 设为默认注册角色 ——
async function onSetDefault(role) {
  await setDefaultRole(role.id)
  showToast(`已将「${role.role_name}」设为默认注册角色`, TOAST_TYPE.SUCCESS)
  await loadRoles()
}

// —— 权限分配 ——
async function openPerm(role) {
  permTarget.id = role.id
  permTarget.role_name = role.role_name
  permOpen.value = true
  permLoading.value = true
  // 清空旧勾选
  Object.keys(checkedPerms).forEach((k) => delete checkedPerms[k])
  try {
    // 并行加载全部权限点与该角色已授予权限
    const [permData, roleData] = await Promise.all([
      listPermissions(),
      getRolePermissions(role.id),
    ])
    permGroups.value = (permData && permData.groups) || []
    const granted = (roleData && roleData.permission_ids) || []
    granted.forEach((pid) => {
      checkedPerms[pid] = true
    })
  } finally {
    permLoading.value = false
  }
}
function togglePerm(permissionId) {
  if (checkedPerms[permissionId]) {
    delete checkedPerms[permissionId]
  } else {
    checkedPerms[permissionId] = true
  }
}
// 切换整组（资源行）全选 / 取消
function toggleGroup(group) {
  const allChecked = group.actions.every((a) => checkedPerms[a.permission_id])
  group.actions.forEach((a) => {
    if (allChecked) {
      delete checkedPerms[a.permission_id]
    } else {
      checkedPerms[a.permission_id] = true
    }
  })
}
function isGroupAllChecked(group) {
  return group.actions.length > 0 && group.actions.every((a) => checkedPerms[a.permission_id])
}
async function submitPerm() {
  permSaving.value = true
  try {
    const ids = Object.keys(checkedPerms).filter((k) => checkedPerms[k]).map((k) => Number(k))
    await assignRolePermissions(permTarget.id, ids)
    showToast('权限已保存', TOAST_TYPE.SUCCESS)
    permOpen.value = false
  } finally {
    permSaving.value = false
  }
}
</script>

<template>
  <div class="role-page">
    <div class="role-page__header">
      <h2 class="role-page__title">角色权限</h2>
      <button class="btn-primary" @click="openCreate">+ 新增角色</button>
    </div>

    <TableContainer max-height="calc(100vh - 240px)">
      <table class="data-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>角色名称</th>
            <th>类型</th>
            <th>默认注册</th>
            <th>状态</th>
            <th>创建时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="role in roles" :key="role.id">
            <td>{{ role.id }}</td>
            <td><SafeHtml :content="role.role_name" /></td>
            <td>{{ role.is_admin ? '管理员' : '普通角色' }}</td>
            <td>
              <span v-if="role.is_default" class="badge badge--default">默认</span>
              <span v-else class="badge-muted">—</span>
            </td>
            <td>
              <span :class="['status-tag', role.status === 1 ? 'status-tag--on' : 'status-tag--off']">
                {{ role.status === 1 ? '启用' : '停用' }}
              </span>
            </td>
            <td>{{ formatDateTime(role.created_at) }}</td>
            <td class="actions">
              <!-- 管理员角色为系统内置，禁止改名 / 停用 / 改权限 / 设默认 -->
              <template v-if="!role.is_admin">
                <button class="link-btn" @click="openEdit(role)">改名</button>
                <button class="link-btn" @click="openPerm(role)">配置权限</button>
                <button v-if="!role.is_default && role.status === 1" class="link-btn" @click="onSetDefault(role)">设为默认</button>
                <button v-if="role.status === 1" class="link-btn link-btn--danger" @click="openStatus(role, false)">停用</button>
                <button v-else class="link-btn" @click="openStatus(role, true)">启用</button>
              </template>
              <span v-else class="badge-muted">系统内置</span>
            </td>
          </tr>
          <tr v-if="roles.length === 0">
            <td colspan="7" class="empty">暂无角色数据</td>
          </tr>
        </tbody>
      </table>
    </TableContainer>

    <Pagination
      :page="page" :page-size="pageSize" :total="total"
      @update:page="onPageChange" @update:page-size="onPageSizeChange"
    />

    <!-- 新增 / 编辑角色弹窗 -->
    <FormModal v-model="editOpen" :title="editForm.id ? '编辑角色' : '新增角色'" :loading="editSaving" @confirm="submitEdit">
      <div class="form-row">
        <label class="form-label">角色名称</label>
        <input v-model="editForm.role_name" class="form-input" placeholder="请输入角色名称" />
      </div>
    </FormModal>

    <!-- 权限分配弹窗 -->
    <FormModal v-model="permOpen" :title="`配置权限 - ${permTarget.role_name}`" :loading="permSaving" @confirm="submitPerm">
      <div v-if="permLoading" class="perm-loading">权限加载中...</div>
      <div v-else class="perm-groups">
        <div v-for="group in permGroups" :key="group.resource_key" class="perm-group">
          <label class="perm-group__head">
            <input type="checkbox" :checked="isGroupAllChecked(group)" @change="toggleGroup(group)" />
            <span class="perm-group__name">{{ group.resource_name }}</span>
          </label>
          <div class="perm-actions">
            <label v-for="act in group.actions" :key="act.permission_id" class="perm-action">
              <input type="checkbox" :checked="!!checkedPerms[act.permission_id]" @change="togglePerm(act.permission_id)" />
              <span>{{ act.action_name }}</span>
            </label>
          </div>
        </div>
        <div v-if="permGroups.length === 0" class="empty">暂无权限点</div>
      </div>
    </FormModal>

    <!-- 启停用确认弹窗 -->
    <ConfirmModal
      v-model="statusOpen"
      :title="statusTarget.enabled ? '启用角色' : '停用角色'"
      :message="`确认${statusTarget.enabled ? '启用' : '停用'}角色「${statusTarget.role_name}」吗？停用后该角色下的用户将无相应权限。`"
      :type="statusTarget.enabled ? 'info' : 'danger'"
      :loading="statusSaving"
      @confirm="confirmStatus"
    />

    <Loading :visible="loading" full-screen text="加载中..." />
  </div>
</template>

<style scoped>
.role-page {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
}
.role-page__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.role-page__title {
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
.badge--default {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 12px;
  background: #e6f4ff;
  color: #0958d9;
}
.badge-muted {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.actions {
  display: flex;
  flex-wrap: wrap;
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
.btn-primary {
  padding: 8px 18px;
  border: none;
  border-radius: 8px;
  background: var(--color-primary);
  color: var(--color-on-primary);
  font-size: 14px;
  cursor: pointer;
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
/* 权限分配 */
.perm-loading {
  text-align: center;
  color: var(--color-text-secondary);
  padding: 24px 0;
}
.perm-groups {
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-height: 50vh;
  overflow-y: auto;
}
.perm-group {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 10px 12px;
}
.perm-group__head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: var(--color-text);
  cursor: pointer;
}
.perm-group__name {
  font-size: 14px;
}
.perm-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 8px;
  padding-left: 24px;
}
.perm-action {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--color-text);
  cursor: pointer;
}
</style>
